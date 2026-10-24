import { injectContext } from "./telemetry";

export type Claim = {
  claimId: string;
  policyNumber: string;
  description: string;
  estimatedAmount: number;
};

export type ClaimResult = {
  claimId: string;
  policyNumber?: string | null;
  description?: string | null;
  incidentDate?: string | null;
  paymentId?: string | null;
  amount?: number;
  currency?: string;
  status?: string;
  narrative?: string;
  rule?: string | null;
  limit?: number | null;
  decision?: string | null;
  /** Set when status is needs_information: what the agent asks the customer. */
  question?: string;
  /** Set when status is needs_information: the claim fields still missing. */
  missing?: string[];
  error?: string;
  /** What claims-agent took the claim to be (a free-text claim's draft so far, while it asks). */
  claim?: {
    policyNumber?: string | null;
    estimatedAmount?: number | null;
    incidentDate?: string | null;
    description?: string | null;
  };
};

/** A claim as the form's fields, or as the customer's own words. */
export type ClaimInput = Claim | string;

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
      taskId?: string;
      contextId?: string;
    };
  };
};

function newId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

/**
 * A new claim. The form's fields go as a JSON object, which claims-agent
 * handles with no model; free text goes as it was typed, for its intake
 * model to read.
 */
export function buildStreamRequest(claim: ClaimInput, push?: PushConfig): JsonRpcRequest {
  const text = typeof claim === "string" ? claim : JSON.stringify(claim);
  return {
    jsonrpc: "2.0",
    id: newId(),
    method: "message/stream",
    params: {
      message: {
        role: "user",
        messageId: newId(),
        parts: [{ kind: "text", text }],
      },
      ...(push ? { configuration: { pushNotificationConfig: push } } : {}),
    },
  };
}

/**
 * The customer's answer to the agent's question, on the SAME task: the task
 * id and context id say which claim it answers. No push config: the one sent
 * with the claim is kept for the task.
 */
export function buildReplyRequest(taskId: string, contextId: string, answer: string): JsonRpcRequest {
  return {
    jsonrpc: "2.0",
    id: newId(),
    method: "message/stream",
    params: {
      message: {
        role: "user",
        messageId: newId(),
        parts: [{ kind: "text", text: answer }],
        taskId,
        contextId,
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
  claim: ClaimInput,
  onEvent: (s: TaskSnapshot) => void,
  push?: PushConfig,
): Promise<TaskSnapshot> {
  const headers = injectContext({ "content-type": "application/json", accept: "text/event-stream" });
  return readStream(await post(baseUrl, buildStreamRequest(claim, push), headers), onEvent);
}

/**
 * Sends the customer's answer on the waiting task and reads that stream to
 * its end, as streamClaim does: it ends when the agent asks again, parks the
 * claim for a reviewer, or decides it.
 */
export async function streamReply(
  baseUrl: string,
  taskId: string,
  contextId: string,
  answer: string,
  onEvent: (s: TaskSnapshot) => void,
): Promise<TaskSnapshot> {
  const headers = injectContext({ "content-type": "application/json", accept: "text/event-stream" });
  return readStream(await post(baseUrl, buildReplyRequest(taskId, contextId, answer), headers), onEvent);
}

async function readStream(response: Response, onEvent: (s: TaskSnapshot) => void): Promise<TaskSnapshot> {
  let snap: TaskSnapshot | null = null;
  for await (const frame of frames(response)) {
    if (frame.error) throw new Error(frame.error.message);
    snap = snapshotOf(frame.result, snap);
    if (snap) onEvent(snap);
  }
  if (!snap) throw new Error("the claim stream ended before the claim was decided");
  return snap;
}

/** The server refused the events stream (401: it no longer knows the token). Not a drop. */
class EventsRefused extends Error {
  constructor(status: number) {
    super(`events returned HTTP ${status}`);
  }
}

/**
 * One read of the server's events stream for this claim. Null when it ended
 * with no terminal state (a drop). The token goes in a header, never a URL.
 */
async function followEvents(
  uiBase: string,
  token: string,
  onEvent: (s: TaskSnapshot) => void,
  carrier: Record<string, string>,
): Promise<TaskSnapshot | null> {
  const headers = { ...carrier, "x-claim-token": token, accept: "text/event-stream" };
  const response = await fetch(`${uiBase.replace(/\/$/, "")}/claims/events`, { headers });
  if (!response.ok) throw new EventsRefused(response.status);
  let snap: TaskSnapshot | null = null;
  for await (const task of frames(response)) {
    snap = snapshotOf(task, snap);
    if (snap) onEvent(snap);
    if (snap && TERMINAL_STATES.has(snap.state)) return snap;
  }
  return null;
}

export async function getTask(
  baseUrl: string,
  taskId: string,
  carrier?: Record<string, string>,
): Promise<TaskSnapshot> {
  // `carrier`: trace headers a caller built before its own first await.
  const headers = { ...(carrier ?? injectContext({})), "content-type": "application/json" };
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
  // Built before the first await: every request below, including the reopens
  // after a drop, carries the wait span's context.
  const carrier = injectContext({});
  let emptyDrops = 0;
  const drop = () => {
    emptyDrops += 1;
    if (emptyDrops >= maxEmptyDrops) {
      throw new Error(`lost the claim's stream ${emptyDrops} times in a row; reload the page`);
    }
  };
  for (;;) {
    let done: TaskSnapshot | null;
    try {
      done = await followEvents(uiBase, token, onEvent, carrier);
    } catch (err) {
      // A refused stream (a customer-ui restart forgot the token) or a broken
      // one: read the task once. Terminal is the outcome.
      let snap: TaskSnapshot | null = null;
      try {
        snap = await getTask(baseUrl, taskId, carrier);
      } catch {
        throw err;
      }
      onEvent(snap);
      if (TERMINAL_STATES.has(snap.state)) return snap;
      if (err instanceof EventsRefused) throw err; // reopening would be refused again
      drop(); // a network error counts as a drop
      continue;
    }
    if (done) return done;
    const now = await getTask(baseUrl, taskId, carrier);
    onEvent(now);
    if (TERMINAL_STATES.has(now.state)) return now;
    drop();
  }
}
