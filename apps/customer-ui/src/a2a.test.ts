import { describe, expect, it, vi } from "vitest";
import type { SpanContext } from "@opentelemetry/api";
import { buildSendMessageRequest, sendClaim, type Claim } from "./a2a";
import { getTracer, initTelemetry } from "./telemetry";

const claim: Claim = {
  claimId: "CLM-1842",
  policyNumber: "POL-77120",
  description: "Rear-end collision at a junction",
  estimatedAmount: 6200,
};

describe("buildSendMessageRequest", () => {
  it("builds a JSON-RPC 2.0 message/send envelope", () => {
    const request = buildSendMessageRequest(claim);

    expect(request.jsonrpc).toBe("2.0");
    expect(request.method).toBe("message/send");
    expect(request.id).toBeTruthy();
  });

  it("carries the claim as a JSON text part", () => {
    const request = buildSendMessageRequest(claim);
    const part = request.params.message.parts[0];

    expect(part.kind).toBe("text");
    expect(JSON.parse(part.text)).toEqual(claim);
  });

  it("sets the message role to user", () => {
    expect(buildSendMessageRequest(claim).params.message.role).toBe("user");
  });
});

describe("sendClaim", () => {
  it("posts to the agent base URL and returns the parsed result", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string);
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: {
            kind: "message",
            role: "agent",
            messageId: "m1",
            parts: [
              {
                kind: "text",
                text: JSON.stringify({
                  claimId: "CLM-1842",
                  paymentId: "PAY-000001",
                  amount: 6200,
                  currency: "EUR",
                  status: "executed",
                }),
              },
            ],
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendClaim("http://localhost:19001", claim);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:19001/");
    expect(result.paymentId).toBe("PAY-000001");
    expect(result.status).toBe("executed");
  });

  it("throws when the agent returns a JSON-RPC error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            jsonrpc: "2.0",
            id: "x",
            error: { code: -32000, message: "intake rejected the claim" },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    await expect(sendClaim("http://localhost:19001", claim)).rejects.toThrow(
      "intake rejected the claim",
    );
  });

  it("injects the active span's traceparent into the outbound request, surviving sendClaim's own await boundary (A19)", async () => {
    // Plan defect, found empirically against the real deployed browser: the
    // brief's Step 6 sketch read `injectContext` via
    // `const { injectContext } = await import("./telemetry")` as the first
    // thing inside sendClaim, immediately before the fetch call. Verified by
    // capture: crossing that await loses the active span context under
    // Zone.js's ZoneContextManager (native async/await bypasses Zone's
    // monkey-patched global Promise via realm-intrinsic continuation
    // scheduling), so the request that reaches claims-agent carries no
    // traceparent header at all — the browser's root span never connects to
    // anything downstream. Fixed: `injectContext` is imported statically, so
    // it runs synchronously, before sendClaim's own first await. This test
    // asserts the actual header's substance (a real traceparent carrying the
    // active span's own trace/span IDs), not merely that no error occurred,
    // and reproduces the same real ZoneContextManager used in production
    // (telemetry.ts's initTelemetry), not a hand-rolled stand-in.
    initTelemetry();

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "x",
          result: {
            kind: "message",
            role: "agent",
            messageId: "m1",
            parts: [{ kind: "text", text: JSON.stringify({ claimId: "CLM-1842" }) }],
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    let spanContext: SpanContext | undefined;
    await getTracer().startActiveSpan("claim.submit", async (span) => {
      spanContext = span.spanContext();
      try {
        await sendClaim("http://localhost:19001", claim);
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
  });
});
