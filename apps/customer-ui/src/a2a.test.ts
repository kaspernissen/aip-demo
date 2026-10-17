import { describe, expect, it, vi } from "vitest";
import type { SpanContext } from "@opentelemetry/api";
import { awaitOutcome, buildStreamRequest, streamClaim, type Claim } from "./a2a";
import { getTracer, initTelemetry } from "./telemetry";

const claim: Claim = {
  claimId: "CLM-1842",
  policyNumber: "POL-77120",
  description: "Rear-end collision at a junction",
  estimatedAmount: 6200,
};

describe("buildStreamRequest", () => {
  it("builds a JSON-RPC 2.0 message/stream envelope", () => {
    const request = buildStreamRequest(claim);

    expect(request.jsonrpc).toBe("2.0");
    expect(request.method).toBe("message/stream");
    expect(request.id).toBeTruthy();
  });

  it("carries the claim as a JSON text part", () => {
    const request = buildStreamRequest(claim);
    const part = request.params.message.parts[0];

    expect(part.kind).toBe("text");
    expect(JSON.parse(part.text)).toEqual(claim);
  });

  it("sets the message role to user", () => {
    expect(buildStreamRequest(claim).params.message.role).toBe("user");
  });
});


function sseResponse(frames: object[], { ping = false } = {}): Response {
  const text =
    (ping ? ": ping\n\n" : "") +
    frames.map((f) => `data: ${JSON.stringify({ jsonrpc: "2.0", id: "1", result: f })}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const task = (state: string, result?: object) => ({
  kind: "task",
  id: "t1",
  contextId: "c1",
  status: { state, message: result ? { parts: [{ kind: "text", text: JSON.stringify(result) }] } : undefined },
});
const update = (state: string, result: object) => ({
  kind: "status-update",
  taskId: "t1",
  contextId: "c1",
  final: false,
  status: { state, message: { parts: [{ kind: "text", text: JSON.stringify(result) }] } },
});

describe("streamClaim", () => {
  it("sends message/stream and returns at input-required with the reply", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" })], { ping: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const snap = await streamClaim("http://agent", { claimId: "C", policyNumber: "P", description: "", estimatedAmount: 5800 }, () => {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).method).toBe("message/stream");
    expect(snap).toMatchObject({ taskId: "t1", contextId: "c1", state: "input-required" });
    expect(snap.result?.status).toBe("awaiting_approval");
  });

  it("reads CRLF-terminated frames, which is what sse-starlette sends", async () => {
    const crlf = sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" })], { ping: true });
    const text = (await crlf.text()).replace(/\n/g, "\r\n");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(text, { status: 200 })));
    const snap = await streamClaim("http://agent", claim, () => {});
    expect(snap.state).toBe("input-required");
    expect(snap.result?.status).toBe("awaiting_approval");
  });

  it("throws when the agent returns a JSON-RPC error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(`data: ${JSON.stringify({ jsonrpc: "2.0", id: "x", error: { code: -32000, message: "intake rejected the claim" } })}\n\n`, { status: 200 }),
    ));
    await expect(streamClaim("http://agent", claim, () => {})).rejects.toThrow("intake rejected the claim");
  });
});

describe("awaitOutcome", () => {
  it("ends when the resubscribe stream delivers a terminal state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([update("completed", { claimId: "C", status: "executed" })])));
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    expect(snap.state).toBe("completed");
    expect(snap.result?.status).toBe("executed");
  });

  it("reads the task once after a drop and resubscribes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([])) // the stream ends with no terminal state: a drop
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("input-required") })))
      .mockResolvedValueOnce(sseResponse([update("completed", { claimId: "C", status: "rejected" })]));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    const methods = fetchMock.mock.calls.map((c) => JSON.parse(c[1].body).method);
    expect(methods).toEqual(["tasks/resubscribe", "tasks/get", "tasks/resubscribe"]);
    expect(snap.result?.status).toBe("rejected");
  });

  it("returns at once when the read after a drop is already terminal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("completed", { claimId: "C", status: "executed" }) })));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(snap.state).toBe("completed");
  });

  it("gives up loudly after repeated drops with nothing in between", async () => {
    const fetchMock = vi.fn().mockImplementation((_u: string, init: RequestInit) =>
      Promise.resolve(
        JSON.parse(String(init.body)).method === "tasks/get"
          ? new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("input-required") }))
          : sseResponse([]),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(awaitOutcome("http://agent", "t1", () => {}, { maxEmptyDrops: 3 })).rejects.toThrow(/lost the claim's stream/);
  });

  it("surfaces a JSON-RPC error from resubscribe when the task is not terminal", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(`data: ${JSON.stringify({ jsonrpc: "2.0", id: "1", error: { code: -32602, message: "Task is in terminal state" } })}\n\n`, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("input-required", undefined) })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(awaitOutcome("http://agent", "t1", () => {})).rejects.toThrow(/terminal state/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("resolves from tasks/get when resubscribe is refused because the decision already landed", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(`data: ${JSON.stringify({ jsonrpc: "2.0", id: "1", error: { code: -32602, message: "Task is in terminal state" } })}\n\n`, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("completed", { claimId: "C", status: "executed" }) })));
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];
    const done = await awaitOutcome("http://agent", "t1", (s) => seen.push(s.state));
    expect(done.state).toBe("completed");
    expect(done.result).toEqual({ claimId: "C", status: "executed" });
    expect(seen).toEqual(["completed"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("traceparent", () => {
  it("injects the active span's traceparent into the outbound request, surviving streamClaim's own await boundary", async () => {
    // Under Zone.js's ZoneContextManager, native async/await bypasses Zone's
    // monkey-patched global Promise via realm-intrinsic continuation
    // scheduling: crossing any `await` before `injectContext` runs drops the
    // active span context, so the request that reaches claims-agent would
    // carry no traceparent header at all and the browser's root span would
    // never connect to anything downstream. `injectContext` is therefore
    // imported statically (see a2a.ts) and called before streamClaim's own
    // first await, so no context ever needs to survive crossing one. This
    // test asserts the actual header's substance (a real traceparent
    // carrying the active span's own trace and span IDs), not merely that no
    // error occurred, and reproduces the same real ZoneContextManager used in
    // production (telemetry.ts's initTelemetry), not a hand-rolled stand-in.
    initTelemetry();

    const fetchMock = vi.fn(async () =>
      sseResponse([task("completed", { claimId: "CLM-1842", status: "executed" })]),
    );
    vi.stubGlobal("fetch", fetchMock);

    let spanContext: SpanContext | undefined;
    await getTracer().startActiveSpan("northstar.claim.submit", async (span) => {
      spanContext = span.spanContext();
      try {
        await streamClaim("http://localhost:19001", claim, () => {});
      } finally {
        span.end();
      }
    });

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    const traceparent = headers.traceparent;
    expect(traceparent, "no traceparent header was sent at all").toBeTruthy();
    const match = /^00-([0-9a-f]{32})-([0-9a-f]{16})-\d{2}$/.exec(traceparent);
    expect(match, `traceparent ${traceparent} does not match the W3C format`).toBeTruthy();
    expect(match![1]).toBe(spanContext!.traceId);
    expect(match![2]).toBe(spanContext!.spanId);
  });
});
