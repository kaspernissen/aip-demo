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

export type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: string;
  method: "message/stream";
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

export function buildStreamRequest(claim: Claim): JsonRpcRequest {
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
async function* frames(response: Response): AsyncGenerator<Frame> {
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
        if (data) yield JSON.parse(data) as Frame;
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

/** Submits the claim on one stream; returns at input-required or a terminal state. */
export async function streamClaim(
  baseUrl: string,
  claim: Claim,
  onEvent: (s: TaskSnapshot) => void,
): Promise<TaskSnapshot> {
  const headers = injectContext({ "content-type": "application/json", accept: "text/event-stream" });
  const response = await post(baseUrl, buildStreamRequest(claim), headers);
  let snap: TaskSnapshot | null = null;
  for await (const frame of frames(response)) {
    if (frame.error) throw new Error(frame.error.message);
    snap = snapshotOf(frame.result, snap);
    if (!snap) continue;
    onEvent(snap);
    if (snap.state === "input-required" || TERMINAL_STATES.has(snap.state)) return snap;
  }
  throw new Error("the claim stream ended before the claim was decided");
}

/** One tasks/resubscribe. Null when the stream ended with no terminal state (a drop). */
export async function resubscribe(
  baseUrl: string,
  taskId: string,
  onEvent: (s: TaskSnapshot) => void,
): Promise<TaskSnapshot | null> {
  const headers = injectContext({ "content-type": "application/json", accept: "text/event-stream" });
  const response = await post(
    baseUrl,
    { jsonrpc: "2.0", id: newId(), method: "tasks/resubscribe", params: { id: taskId } },
    headers,
  );
  let snap: TaskSnapshot | null = null;
  for await (const frame of frames(response)) {
    if (frame.error) throw new Error(frame.error.message);
    snap = snapshotOf(frame.result, snap);
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

/** Waits on one stream; on a drop, reads the task once and resubscribes. Not a polling loop. */
export async function awaitOutcome(
  baseUrl: string,
  taskId: string,
  onEvent: (s: TaskSnapshot) => void,
  { maxEmptyDrops = 5 }: { maxEmptyDrops?: number } = {},
): Promise<TaskSnapshot> {
  let emptyDrops = 0;
  for (;;) {
    let done: TaskSnapshot | null;
    try {
      done = await resubscribe(baseUrl, taskId, onEvent);
    } catch (err) {
      // The decision can land before the resubscribe: the SDK then refuses with
      // "is in terminal state". Read the task once; a terminal snapshot is the outcome.
      let snap: TaskSnapshot | null = null;
      try {
        snap = await getTask(baseUrl, taskId);
      } catch {
        throw err;
      }
      if (!TERMINAL_STATES.has(snap.state)) throw err;
      onEvent(snap);
      return snap;
    }
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
