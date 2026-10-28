"use client";

import { useEffect, useState } from "react";
import { SpanStatusCode, context, trace, type Span, type SpanContext } from "@opentelemetry/api";
import {
  awaitOutcome,
  getPushConfig,
  streamClaim,
  streamReply,
  type ClaimResult,
  type TaskSnapshot,
} from "../a2a";
import { PRESETS, STANDARD_CLAIM, selectedPreset } from "../presets";
import { determinationView, formatElapsed, outcomeWord, questionOf } from "../view";
import { getTracer, initTelemetry } from "../telemetry";

const CLAIMS_AGENT_URL =
  process.env.NEXT_PUBLIC_CLAIMS_AGENT_URL ?? "http://localhost:19001";

/** A claim waiting for the customer's answer to the agent's question. */
type Asking = {
  taskId: string;
  contextId: string;
  token: string;
  question: string;
  claimId: string;
  // The span of the round that asked: the answer's trace links back to it.
  askedIn: SpanContext;
};

export default function Page() {
  const [story, setStory] = useState(STANDARD_CLAIM.text);
  const [asking, setAsking] = useState<Asking | null>(null);
  const [answer, setAnswer] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<ClaimResult | null>(null);
  const [busy, setBusy] = useState(false);
  // When the claim went to input-required: the clock the waiting view shows.
  const [waitingSince, setWaitingSince] = useState<number | null>(null);

  useEffect(() => {
    initTelemetry();
  }, []);

  /**
   * What to do once a stream (the claim's, or an answer's) has ended. Shared
   * by both, because an answer can end exactly as a claim can: decided,
   * waiting for a reviewer, or asking again.
   */
  function afterStream(snap: TaskSnapshot, span: Span, token: string): { taskId: string; context: SpanContext; token: string; claimId: string } | null {
    if (snap.result) setResult(snap.result);
    const claimId = snap.result?.claimId ?? "";
    const question = questionOf(snap.state, snap.result);
    if (question !== null) {
      setAsking({
        taskId: snap.taskId,
        contextId: snap.contextId,
        token,
        question,
        claimId,
        askedIn: span.spanContext(),
      });
      setAnswer("");
      setLog((entries) => [...entries, `Northstar asks: ${question}`]);
      return null;
    }
    setAsking(null);
    if (snap.state === "input-required") {
      setWaitingSince(Date.now());
      setLog((entries) => [...entries, "Waiting for a person to decide"]);
      return { taskId: snap.taskId, context: span.spanContext(), token, claimId };
    }
    setLog((entries) => [...entries, `Claim ${outcomeWord(snap.result?.status, snap.state)}`]);
    return null;
  }

  /** The claim's id and policy number go on the span once a snapshot names them. */
  function nameClaim(span: Span, s: TaskSnapshot) {
    if (s.result?.claimId) span.setAttribute("northstar.claim.id", s.result.claimId);
    if (s.result?.policyNumber) span.setAttribute("northstar.claim.policy_number", s.result.policyNumber);
  }

  function failed(span: Span, error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    span.setStatus({ code: SpanStatusCode.ERROR, message });
    setLog((entries) => [...entries, `Failed: ${message}`]);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    setAsking(null);

    const submitted = story;
    setLog(["Submitting your claim…"]);

    // The browser owns the root span. Everything downstream is its child.
    // startActiveSpan returns the callback's value, so awaiting it awaits the
    // whole submission. This span (trace A) ends when the claim stream first
    // returns: either the claim is decided, or it is waiting for a person.
    let waiting: { taskId: string; context: SpanContext; token: string; claimId: string } | null = null;
    await getTracer().startActiveSpan("northstar.claim.submit", async (span) => {
      // The claim gets its id from claims-agent, so the span is named when
      // the first snapshot says it.
      try {
        // This server's own origin: it issues the push token and relays the push.
        const uiBase = window.location.origin;
        const push = await getPushConfig(uiBase);
        // The await above dropped the active context (Zone.js); re-enter this
        // span's so streamClaim's traceparent is trace A's, not nothing.
        const snap = await context.with(trace.setSpan(context.active(), span), () =>
          streamClaim(CLAIMS_AGENT_URL, submitted, (s) => {
            if (s.result) setResult(s.result);
            nameClaim(span, s);
          }, push),
        );
        waiting = afterStream(snap, span, push.token);
      } catch (error) {
        failed(span, error);
      } finally {
        span.end();
      }
    });

    if (waiting) await waitForPerson(waiting);
    setBusy(false);
  }

  /**
   * The customer answers the agent's question on the same task. Its own
   * trace, LINKED to the span of the round that asked: the customer may take
   * minutes to answer, and that round's trace has long since ended.
   */
  async function sendAnswer(event: React.FormEvent) {
    event.preventDefault();
    if (!asking || !answer.trim()) return;
    const { taskId, contextId, token, claimId, askedIn } = asking;
    const text = answer.trim();
    setBusy(true);
    setLog((entries) => [...entries, `You answered: ${text}`]);

    let waiting: { taskId: string; context: SpanContext; token: string; claimId: string } | null = null;
    await getTracer().startActiveSpan(
      "northstar.claim.answer",
      { links: [{ context: askedIn }], attributes: { "northstar.claim.id": claimId } },
      async (span) => {
        try {
          const snap = await streamReply(CLAIMS_AGENT_URL, taskId, contextId, text, (s) => {
            if (s.result) setResult(s.result);
          });
          waiting = afterStream(snap, span, token);
        } catch (error) {
          failed(span, error);
        } finally {
          span.end();
        }
      },
    );

    if (waiting) await waitForPerson(waiting);
    setBusy(false);
  }

  // The wait is a second root span, its own trace, LINKED to the span that
  // parked the claim, because the human decides minutes later and that trace
  // has long since ended. The wait is one read of this server's events stream,
  // fed by the claims-agent's push: no polling.
  async function waitForPerson({ taskId, context: parkedIn, token, claimId }: { taskId: string; context: SpanContext; token: string; claimId: string }) {
    await getTracer().startActiveSpan(
      "northstar.claim.wait",
      { links: [{ context: parkedIn }], attributes: { "northstar.claim.id": claimId } },
      async (wait) => {
        try {
          const final = await awaitOutcome(
            CLAIMS_AGENT_URL,
            window.location.origin,
            taskId,
            token,
            (s) => s.result && setResult(s.result),
          );
          if (final.result) setResult(final.result);
          setLog((entries) => [...entries, `Claim ${outcomeWord(final.result?.status, final.state)}`]);
        } catch (error) {
          wait.setStatus({ code: SpanStatusCode.ERROR, message: String(error) });
          setLog((entries) => [...entries, `Failed: ${String(error)}`]);
        } finally {
          wait.end();
        }
      },
    );
    setWaitingSince(null);
  }

  return (
    <main className="shell">
      <div className="intro">
        <h1>File a vehicle-damage claim</h1>
        <p>
          Tell us what happened in your own words. You will see how we read
          your claim, and its outcome, on this page.
        </p>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Claim details</h2>
          <span className="ref">Motor / First notification of loss</span>
        </div>
        <div className="panel-body">
          {/* Two prepared claims. A chip replaces the text and submits
              nothing; the text stays editable, and an edited text selects
              no chip. */}
          <div className="modes" role="group" aria-label="Prepared claims">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={[
                  "mode",
                  preset.id === "tricky" ? "is-tricky" : "",
                  selectedPreset(story) === preset.id ? "is-active" : "",
                ].filter(Boolean).join(" ")}
                aria-pressed={selectedPreset(story) === preset.id}
                onClick={() => setStory(preset.text)}
                disabled={busy}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <form onSubmit={submit}>
            <div className="fields">
              <label className="field wide">
                <span>Tell us what happened</span>
                <textarea
                  className="story"
                  value={story}
                  onChange={(e) => setStory(e.target.value)}
                  required
                />
              </label>
              <p className="fineprint wide">
                Include your policy number, the date, what was damaged and a
                rough cost in euro. If anything is missing, we will ask.
              </p>
            </div>

            <div className="actions">
              <button type="submit" disabled={busy}>
                {busy ? "Submitting…" : "Submit claim"}
              </button>
              <p className="fineprint">
                Northstar&rsquo;s claims agent reads your claim and settles
                it.
              </p>
            </div>
          </form>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Progress</h2>
        </div>
        <div className="panel-body">
          <pre>{log.join("\n")}</pre>

          {asking && (
            <form className="answer" onSubmit={sendAnswer}>
              <label className="field wide">
                <span>{asking.question}</span>
                <textarea
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  aria-label="Your answer"
                  required
                />
              </label>
              <div className="actions">
                <button type="submit" disabled={busy || !answer.trim()}>
                  {busy ? "Sending…" : "Send answer"}
                </button>
              </div>
            </form>
          )}

          {result && (
            <>
              <Determination result={result} waitingSince={waitingSince} />
              {/* `<h2>Outcome</h2>` immediately followed by `<pre>` is a
                  contract two e2e suites depend on -- they locate the JSON by
                  the adjacent-sibling selector `h2:text-is('Outcome') + pre`,
                  and they wait on `text=Outcome`, which substring-matches. So
                  the word "Outcome" must appear nowhere else on this page, and
                  nothing may come between this heading and its <pre>. */}
              <h2>Outcome</h2>
              <pre>{JSON.stringify(result, null, 2)}</pre>
            </>
          )}
        </div>
      </section>

      <p className="footnote">
        Northstar Insurance is a fictional insurer. This page is the customer
        surface of a reference implementation for agentic internal platforms.
      </p>
    </main>
  );
}

/* The result, said the way an insurer says it. A claim waiting for a person is
 * a determination in blue, and a decline in ochre rather than an error in red:
 * the platform routing a payment above the agent's limit to a human is it
 * working correctly, and the UI should not contradict that -- the
 * `northstar.policy.decision` span is deliberately not ERROR for the same reason. Red is
 * kept for a genuine fault, where the claim never got an answer at all. */
function Determination({
  result,
  waitingSince,
}: {
  result: ClaimResult;
  waitingSince: number | null;
}) {
  const { tone, heading } = determinationView(result);
  const waiting = result.status === "awaiting_approval";
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [waiting]);

  return (
    <div className={`determination ${tone}`}>
      <div className="status">{result.status ?? "unknown"}</div>
      <div className="detail">
        <strong>{heading}.</strong>{" "}
        {result.amount !== undefined && (
          <>
            Amount{" "}
            <span className="figure">
              {result.amount} {result.currency ?? "EUR"}
            </span>
            .{" "}
          </>
        )}
        {result.paymentId && (
          <>
            Payment reference <span className="figure">{result.paymentId}</span>.{" "}
          </>
        )}
        {result.narrative && result.status !== "needs_information" && <>&ldquo;{result.narrative}&rdquo;</>}
        {result.status === "needs_information" && (
          <>Claim reference <span className="figure">{result.claimId}</span>. Please answer the question below.</>
        )}
        {waiting && (
          <p className="waiting">
            A person at Northstar is reviewing this payment.
            {waitingSince !== null && <> Waiting {formatElapsed(now - waitingSince)}.</>}
            {result.rule && result.limit !== null && result.limit !== undefined && (
              <>
                {" "}
                The agent&rsquo;s limit here is{" "}
                <span className="figure">&euro;{result.limit}</span>.
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
