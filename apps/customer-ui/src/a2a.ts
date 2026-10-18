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
  rule?: string | null;
  limit?: number | null;
  decision?: string | null;
};

type TextPart = { kind: "text"; text: string };

/** Where the claims-agent pushes task snapshots, and the token that proves they are ours. */
export type PushConfig = { url: string; token: string };

export type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: string;
  method: "message/stream";
  params: {
    configuration?: { pushNotificationConfig: PushConfig };
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

export function buildStreamRequest(claim: Claim, push?: PushConfig): JsonRpcRequest {
  return {
    jsonrpc: "2.0",
    id: newId(),
    method: "message/stream",
    params: {
      message: {
        role: "user",
        messageId: newId(),
        parts: [{ kind: "text", text: JSON.stringify(claim) }],
      },
      ...(push ? { configuration: { pushNotificationConfig: push } } : {}),
    },
  };
}

export type TaskSnapshot = {
  taskId: string;
  contextId: string;
  state: string;
  result: ClaimResult | null;
};
export const TERMINAL_STATES = new Set(["completed", "failed", "canceled", "rejected"]);

type Frame = { result?: any; error?: { message: string } };
// The events stream carries bare A2A Task JSON (as the claims-agent pushed it), not JSON-RPC.
type RawFrame = any;

function snapshotOf(event: any, prev: TaskSnapshot | null): TaskSnapshot | null {
  const taskId = event?.id ?? event?.taskId ?? prev?.taskId;
  const contextId = event?.contextId ?? prev?.contextId;
  const state = event?.status?.state;
  if (!taskId || !state) return prev;
  const parts = event?.status?.message?.parts ?? [];
  let result = prev?.result ?? null;
  for (const part of [...parts].reverse()) {
    if (part.kind !== "text" || !part.text) continue;
    try {
      result = JSON.parse(part.text) as ClaimResult;
      break;
    } catch {
      // not JSON; keep looking
    }
  }
  return { taskId, contextId, state, result };
}

/** Reads SSE `data:` frames from a fetch body; ignores `: ping` comments. */
async function* frames(response: Response): AsyncGenerator<RawFrame> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      // sse-starlette ends every line with CRLF, so frames end with a blank
      // CRLF line. Normalise before looking for the blank line.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = block
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim())
          .join("\n");
        if (data) yield JSON.parse(data);
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

async function post(baseUrl: string, body: object, headers: Record<string, string>): Promise<Response> {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`claims-agent returned HTTP ${response.status}`);
  return response;
}

// Every function below builds its headers (`injectContext`) BEFORE its first
// `await`: crossing any `await` first drops the active span context. Root
// cause: Zone.js's ZoneContextManager cannot intercept a *native* async
// function's await-continuation scheduling -- V8's `await` resumes via the
// realm's intrinsic %Promise%, not the mutable global `Promise` binding
// Zone.js patches -- so `propagation.inject` would silently write nothing and
// the browser's span would never connect to anything downstream. See
// `a2a.test.ts`'s traceparent test for the regression coverage.

/** Asks this page's own server for a push URL and a fresh token (same origin). */
export async function getPushConfig(uiBase: string): Promise<PushConfig> {
  const headers = injectContext({});
  const response = await fetch(`${uiBase.replace(/\/$/, "")}/claims/push-config`, { method: "POST", headers });
  if (!response.ok) throw new Error(`push-config returned HTTP ${response.status}`);
  return (await response.json()) as PushConfig;
}

/**
 * Submits the claim on one stream and reads it to its end: the claims-agent
 * closes it at input-required or a terminal state. Returns the last snapshot.
 */
export async function streamClaim(
  baseUrl: string,
  claim: Claim,
  onEvent: (s: TaskSnapshot) => void,
  push?: PushConfig,
): Promise<TaskSnapshot> {
  const headers = injectContext({ "content-type": "application/json", accept: "text/event-stream" });
  const response = await post(baseUrl, buildStreamRequest(claim, push), headers);
  let snap: TaskSnapshot | null = null;
  for await (const frame of frames(response)) {
    if (frame.error) throw new Error(frame.error.message);
    snap = snapshotOf(frame.result, snap);
    if (snap) onEvent(snap);
  }
  if (!snap) throw new Error("the claim stream ended before the claim was decided");
  return snap;
}

/**
 * One read of the server's events stream for this claim. Null when it ended
 * with no terminal state (a drop). The token goes in a header, never a URL.
 */
async function followEvents(
  uiBase: string,
  token: string,
  onEvent: (s: TaskSnapshot) => void,
): Promise<TaskSnapshot | null> {
  const headers = injectContext({ "x-claim-token": token, accept: "text/event-stream" });
  const response = await fetch(`${uiBase.replace(/\/$/, "")}/claims/events`, { headers });
  if (!response.ok) throw new Error(`events returned HTTP ${response.status}`);
  let snap: TaskSnapshot | null = null;
  for await (const task of frames(response)) {
    snap = snapshotOf(task, snap);
    if (snap) onEvent(snap);
    if (snap && TERMINAL_STATES.has(snap.state)) return snap;
  }
  return null;
}

export async function getTask(baseUrl: string, taskId: string): Promise<TaskSnapshot> {
  const headers = injectContext({ "content-type": "application/json" });
  const response = await post(
    baseUrl,
    { jsonrpc: "2.0", id: newId(), method: "tasks/get", params: { id: taskId } },
    headers,
  );
  const body = (await response.json()) as Frame;
  if (body.error) throw new Error(body.error.message);
  const snap = snapshotOf(body.result, null);
  if (!snap) throw new Error(`tasks/get returned no task state for ${taskId}`);
  return snap;
}

/**
 * Waits for the outcome on the server's events stream, which the claims-agent's
 * push feeds. On a drop, reads the task once and reopens. Not a polling loop.
 */
export async function awaitOutcome(
  baseUrl: string,
  uiBase: string,
  taskId: string,
  token: string,
  onEvent: (s: TaskSnapshot) => void,
  { maxEmptyDrops = 5 }: { maxEmptyDrops?: number } = {},
): Promise<TaskSnapshot> {
  let emptyDrops = 0;
  for (;;) {
    const done = await followEvents(uiBase, token, onEvent);
    if (done) return done;
    const now = await getTask(baseUrl, taskId);
    onEvent(now);
    if (TERMINAL_STATES.has(now.state)) return now;
    emptyDrops += 1;
    if (emptyDrops >= maxEmptyDrops) {
      throw new Error(`lost the claim's stream ${emptyDrops} times in a row; reload the page`);
    }
  }
}
