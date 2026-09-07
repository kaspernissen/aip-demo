import { injectContext } from "./telemetry";

export type Claim = {
  claimId: string;
  policyNumber: string;
  description: string;
  estimatedAmount: number;
};

export type ClaimResult = {
  claimId: string;
  paymentId?: string;
  amount?: number;
  currency?: string;
  status?: string;
  narrative?: string;
};

type TextPart = { kind: "text"; text: string };

export type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: string;
  method: "message/send";
  params: {
    message: {
      role: "user";
      messageId: string;
      parts: TextPart[];
    };
  };
};

function newId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

export function buildSendMessageRequest(claim: Claim): JsonRpcRequest {
  return {
    jsonrpc: "2.0",
    id: newId(),
    method: "message/send",
    params: {
      message: {
        role: "user",
        messageId: newId(),
        parts: [{ kind: "text", text: JSON.stringify(claim) }],
      },
    },
  };
}

function extractJsonResult(result: unknown): ClaimResult {
  const container = result as { parts?: TextPart[]; status?: { message?: { parts?: TextPart[] } } };
  const parts = container.parts ?? container.status?.message?.parts ?? [];
  for (const part of [...parts].reverse()) {
    if (part.kind !== "text" || !part.text) continue;
    try {
      return JSON.parse(part.text) as ClaimResult;
    } catch {
      // Progress updates are plain prose, not JSON. Keep looking.
    }
  }
  throw new Error("agent returned no JSON payload");
}

export async function sendClaim(baseUrl: string, claim: Claim): Promise<ClaimResult> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  // Plan defect found empirically (A19): the task brief's Step 6 sketch used
  // `const { injectContext } = await import("./telemetry");` here, immediately
  // before the fetch call. That crosses an `await` boundary before
  // `injectContext` (and its `context.active()` read) ever runs. Verified by
  // capture against the real deployed pod: Zone.js's ZoneContextManager
  // cannot intercept a *native* async function's await-continuation
  // scheduling — V8's `await` resumes via the realm's intrinsic %Promise%,
  // captured once at realm creation, not the mutable global `Promise`
  // binding Zone.js monkey-patches — so the active span context (correctly
  // present up to that point, confirmed via console capture inside the
  // deployed browser) is gone by the time the awaited import resolves, and
  // `propagation.inject` silently writes nothing. The claims-agent request
  // then carries no `traceparent` header at all, and the browser's
  // claim.submit span never connects to anything downstream — the exact
  // "two traces instead of one" failure this task's own Step 11 contingency
  // describes, just with a different cause than the CORS rejection it names.
  // Fixed: `injectContext` is imported statically (below), so it runs
  // synchronously as part of constructing this fetch call's `init` object,
  // before `sendClaim`'s own first `await`. See `a2a.test.ts`'s
  // "injects the active span's traceparent into the outbound request,
  // surviving sendClaim's own await boundary (A19)" test and NOTES.md for the
  // regression coverage and the verified-by-capture header.
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/`, {
    method: "POST",
    headers: injectContext(headers),
    body: JSON.stringify(buildSendMessageRequest(claim)),
  });

  if (!response.ok) {
    throw new Error(`claims-agent returned HTTP ${response.status}`);
  }

  const body = (await response.json()) as { error?: { message: string }; result?: unknown };
  if (body.error) {
    throw new Error(body.error.message);
  }
  return extractJsonResult(body.result);
}
