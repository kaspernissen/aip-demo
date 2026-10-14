"use client";

import { useEffect, useState } from "react";
import { SpanStatusCode, type SpanContext } from "@opentelemetry/api";
import { awaitOutcome, streamClaim, type Claim, type ClaimResult } from "../a2a";
import { determinationView, formatElapsed, outcomeWord } from "../view";
import { getTracer, initTelemetry } from "../telemetry";

const CLAIMS_AGENT_URL =
  process.env.NEXT_PUBLIC_CLAIMS_AGENT_URL ?? "http://localhost:19001";

const DEFAULT_CLAIM: Claim = {
  claimId: "CLM-1842",
  policyNumber: "POL-77120",
  description: "Rear-end collision at a junction",
  estimatedAmount: 6200,
};

export default function Page() {
  const [claim, setClaim] = useState<Claim>(DEFAULT_CLAIM);
  // The amount is held as TEXT, not as a number, and parsed once at submit.
  //
  // Holding it as `value={claim.estimatedAmount}` against
  // `onChange={... Number(e.target.value)}` has a specific and very visible
  // failure: clearing the field makes `e.target.value` the empty string,
  // `Number("")` is `0`, and the controlled input immediately re-renders
  // showing `0`. The field can therefore never be emptied -- the caret sits
  // beside a zero you cannot delete -- and the next digits typed land next
  // to it, so changing 6200 to 5800 by hand produces `05800`. It still
  // parses to 5800, so nothing fails; it just looks broken at exactly the
  // point in a demo where the amount is changed live. Playwright's `fill()`
  // replaces the whole value in one step, which is why no test would catch
  // this.
  const [amountText, setAmountText] = useState(String(DEFAULT_CLAIM.estimatedAmount));
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<ClaimResult | null>(null);
  const [busy, setBusy] = useState(false);
  // When the claim went to input-required: the clock the waiting view shows.
  const [waitingSince, setWaitingSince] = useState<number | null>(null);

  useEffect(() => {
    initTelemetry();
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    setLog([`Submitting ${claim.claimId}…`]);

    const submitted: Claim = { ...claim, estimatedAmount: Number(amountText) };

    // The browser owns the root span. Everything downstream is its child.
    // startActiveSpan returns the callback's value, so awaiting it awaits the
    // whole submission. This span (trace A) ends when the claim stream first
    // returns: either the claim is decided, or it is waiting for a person.
    let waiting: { taskId: string; context: SpanContext } | null = null;
    await getTracer().startActiveSpan("claim.submit", async (span) => {
      span.setAttribute("northstar.claim.id", submitted.claimId);
      span.setAttribute("northstar.policy.number", submitted.policyNumber);
      try {
        const snap = await streamClaim(CLAIMS_AGENT_URL, submitted, (s) => s.result && setResult(s.result));
        if (snap.result) setResult(snap.result);
        if (snap.state === "input-required") {
          waiting = { taskId: snap.taskId, context: span.spanContext() };
          setWaitingSince(Date.now());
          setLog((entries) => [...entries, "Waiting for a person to decide"]);
        } else {
          setLog((entries) => [...entries, `Claim ${outcomeWord(snap.result?.status, snap.state)}`]);
        }
      } catch (error) {
        span.setStatus({
          code: SpanStatusCode.ERROR,
          message: error instanceof Error ? error.message : String(error),
        });
        setLog((entries) => [
          ...entries,
          `Failed: ${error instanceof Error ? error.message : String(error)}`,
        ]);
      } finally {
        span.end();
      }
    });

    // The wait is a second root span (trace B) LINKED to the first, because the
    // human decides minutes later and trace A has long since ended. One stream
    // carries the whole wait: no polling.
    if (waiting) {
      const { taskId, context } = waiting as { taskId: string; context: SpanContext };
      await getTracer().startActiveSpan(
        "claim.wait",
        { links: [{ context }], attributes: { "northstar.claim.id": submitted.claimId } },
        async (wait) => {
          try {
            const final = await awaitOutcome(
              CLAIMS_AGENT_URL,
              taskId,
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
    }
    setWaitingSince(null);
    setBusy(false);
  }

  return (
    <main className="shell">
      <div className="intro">
        <h1>File a vehicle-damage claim</h1>
        <p>
          Your claim is assessed as soon as it is submitted. You will see the
          determination on this page.
        </p>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Claim details</h2>
          <span className="ref">Motor / First notification of loss</span>
        </div>
        <div className="panel-body">
          <form onSubmit={submit}>
            <div className="fields">
              {/* Claim reference is deliberately the FIRST <input> on the page:
                  tests/e2e/test_policy_denial.py addresses it as
                  `input >> nth=0`. Do not introduce an input above it. */}
              <label className="field">
                <span>Claim reference</span>
                <input
                  value={claim.claimId}
                  onChange={(e) => setClaim({ ...claim, claimId: e.target.value })}
                  required
                />
              </label>

              <label className="field">
                <span>Policy number</span>
                <input
                  value={claim.policyNumber}
                  onChange={(e) =>
                    setClaim({ ...claim, policyNumber: e.target.value })
                  }
                  required
                />
              </label>

              <label className="field wide">
                <span>What happened</span>
                <textarea
                  value={claim.description}
                  onChange={(e) =>
                    setClaim({ ...claim, description: e.target.value })
                  }
                />
              </label>

              {/* The only input[type=number] on the page — the amount that
                  tests/e2e/test_policy_denial.py fills to drive the €6,200
                  refusal and the €5,800 settlement. */}
              <label className="field amount">
                <span>Estimated amount</span>
                <span className="currency" aria-hidden="true">
                  €
                </span>
                <input
                  type="number"
                  inputMode="numeric"
                  min="0"
                  step="1"
                  value={amountText}
                  onChange={(e) => setAmountText(e.target.value)}
                  aria-label="Estimated amount in euro"
                  required
                />
              </label>

              {/* Deliberately a static cell, not an input: it balances the
                  amount's row and explains the € prefix, and adding a real
                  field here would put a second control between the claim
                  reference (`input >> nth=0`) and the amount
                  (`input[type=number]`) that two e2e suites address. */}
              <div className="field">
                <span>Currency</span>
                <div className="static">EUR &mdash; euro</div>
              </div>
            </div>

            <div className="actions">
              <button type="submit" disabled={busy}>
                {busy ? "Submitting…" : "Submit claim"}
              </button>
              <p className="fineprint">
                Submitting sends this claim to Northstar&rsquo;s settlement
                agents for assessment.
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
 * `policy.decision` span is deliberately not ERROR for the same reason. Red is
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
            Amount assessed{" "}
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
        {result.narrative && <>&ldquo;{result.narrative}&rdquo;</>}
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
