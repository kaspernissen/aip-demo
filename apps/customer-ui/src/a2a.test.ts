import { describe, expect, it, vi } from "vitest";
import type { SpanContext } from "@opentelemetry/api";
import { awaitOutcome, buildStreamRequest, getPushConfig, streamClaim, type Claim } from "./a2a";
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

  it("carries the push config as configuration.pushNotificationConfig, only when given", () => {
    const push = { url: "http://ui/a2a/push", token: "tok" };
    expect(buildStreamRequest(claim, push).params.configuration).toEqual({ pushNotificationConfig: push });
    expect(buildStreamRequest(claim).params.configuration).toBeUndefined();
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

const TOKEN = "tok-secret-0123456789abcdefghijklmnopqrstuvwxyz";
const push = { url: "http://ui/a2a/push", token: TOKEN };

/** The events stream carries bare Task JSON, not JSON-RPC frames. */
function eventsResponse(tasks: object[]): Response {
  return new Response(tasks.map((t) => `data: ${JSON.stringify(t)}\n\n`).join(""), { status: 200 });
}
const taskJson = (state: string, result?: object) => task(state, result);
const getResponse = (state: string, result?: object) =>
  new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task(state, result) }));

describe("getPushConfig", () => {
  it("POSTs to the same-origin /claims/push-config and returns url and token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(push)));
    vi.stubGlobal("fetch", fetchMock);
    expect(await getPushConfig("http://ui")).toEqual(push);
    expect(fetchMock.mock.calls[0][0]).toBe("http://ui/claims/push-config");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });
});

describe("streamClaim", () => {
  it("sends message/stream with the push config and reads the stream to its end", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" })], { ping: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];
    const snap = await streamClaim("http://agent", { claimId: "C", policyNumber: "P", description: "", estimatedAmount: 5800 }, (s) => seen.push(s.state), push);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.method).toBe("message/stream");
    expect(body.params.configuration.pushNotificationConfig).toEqual(push);
    expect(snap).toMatchObject({ taskId: "t1", contextId: "c1", state: "input-required" });
    expect(snap.result?.status).toBe("awaiting_approval");
    expect(seen).toEqual(["submitted", "input-required"]);
  });

  it("does not stop at input-required: it returns the last snapshot the stream carries", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" }), update("completed", { claimId: "C", status: "executed" })]),
    ));
    const snap = await streamClaim("http://agent", claim, () => {}, push);
    expect(snap.state).toBe("completed");
  });

  it("reads CRLF-terminated frames, which is what sse-starlette sends", async () => {
    const crlf = sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" })], { ping: true });
    const text = (await crlf.text()).replace(/\n/g, "\r\n");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(text, { status: 200 })));
    const snap = await streamClaim("http://agent", claim, () => {}, push);
    expect(snap.state).toBe("input-required");
    expect(snap.result?.status).toBe("awaiting_approval");
  });

  it("throws when the agent returns a JSON-RPC error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(`data: ${JSON.stringify({ jsonrpc: "2.0", id: "x", error: { code: -32000, message: "intake rejected the claim" } })}\n\n`, { status: 200 }),
    ));
    await expect(streamClaim("http://agent", claim, () => {}, push)).rejects.toThrow("intake rejected the claim");
  });
});

describe("awaitOutcome", () => {
  const run = (onEvent: (s: any) => void = () => {}, opts?: { maxEmptyDrops?: number }) =>
    awaitOutcome("http://agent", "http://ui", "t1", TOKEN, onEvent, opts);

  it("opens GET /claims/events with x-claim-token and ends on a terminal frame", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      eventsResponse([taskJson("input-required"), taskJson("completed", { claimId: "C", status: "executed" })]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];
    const snap = await run((s) => seen.push(s.state));
    expect(fetchMock.mock.calls[0][0]).toBe("http://ui/claims/events");
    expect(fetchMock.mock.calls[0][1].headers["x-claim-token"]).toBe(TOKEN);
    expect(snap.state).toBe("completed");
    expect(snap.result?.status).toBe("executed");
    expect(seen).toEqual(["input-required", "completed"]);
  });

  it("reads the task once after a drop and reopens", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(eventsResponse([])) // ends with no terminal state: a drop
      .mockResolvedValueOnce(getResponse("input-required"))
      .mockResolvedValueOnce(eventsResponse([taskJson("completed", { claimId: "C", status: "rejected" })]));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await run();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["http://ui/claims/events", "http://agent/", "http://ui/claims/events"]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).method).toBe("tasks/get");
    expect(snap.result?.status).toBe("rejected");
  });

  it("returns at once when the read after a drop is already terminal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(eventsResponse([]))
      .mockResolvedValueOnce(getResponse("completed", { claimId: "C", status: "executed" }));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await run();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(snap.state).toBe("completed");
  });

  it("gives up loudly after repeated drops with nothing in between", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(String(url).endsWith("/claims/events") ? eventsResponse([]) : getResponse("input-required")),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(run(() => {}, { maxEmptyDrops: 3 })).rejects.toThrow(/lost the claim's stream/);
  });

  it("surfaces an events refusal rather than looping", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 401 })));
    await expect(run()).rejects.toThrow(/HTTP 401/);
  });

  it("never puts the token in a URL", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(eventsResponse([]))
      .mockResolvedValueOnce(getResponse("input-required"))
      .mockResolvedValueOnce(eventsResponse([taskJson("completed", { claimId: "C", status: "executed" })]));
    vi.stubGlobal("fetch", fetchMock);
    await run();
    for (const call of fetchMock.mock.calls) expect(String(call[0])).not.toContain(TOKEN);
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
