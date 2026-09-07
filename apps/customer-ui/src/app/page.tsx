"use client";

import { useEffect, useState } from "react";
import { SpanStatusCode } from "@opentelemetry/api";
import { sendClaim, type Claim, type ClaimResult } from "../a2a";
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
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<ClaimResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    initTelemetry();
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    setLog([`Submitting ${claim.claimId}…`]);

    // The browser owns the root span. Everything downstream is its child.
    // startActiveSpan returns the callback's value, so awaiting it awaits the
    // whole submission.
    await getTracer().startActiveSpan("claim.submit", async (span) => {
      span.setAttribute("northstar.claim.id", claim.claimId);
      span.setAttribute("northstar.policy.number", claim.policyNumber);
      try {
        const settled = await sendClaim(CLAIMS_AGENT_URL, claim);
        setResult(settled);
        setLog((entries) => [...entries, `Settled: ${settled.status}`]);
        return settled;
      } catch (error) {
        span.setStatus({
          code: SpanStatusCode.ERROR,
          message: error instanceof Error ? error.message : String(error),
        });
        setLog((entries) => [
          ...entries,
          `Failed: ${error instanceof Error ? error.message : String(error)}`,
        ]);
        return null;
      } finally {
        span.end();
        setBusy(false);
      }
    });
  }

  return (
    <main style={{ maxWidth: 640 }}>
      <h1>Northstar Insurance</h1>
      <p>File a vehicle-damage claim.</p>

      <form onSubmit={submit}>
        <label style={{ display: "block", marginBottom: 8 }}>
          Claim ID
          <input
            value={claim.claimId}
            onChange={(e) => setClaim({ ...claim, claimId: e.target.value })}
            style={{ display: "block", width: "100%" }}
          />
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          Policy number
          <input
            value={claim.policyNumber}
            onChange={(e) => setClaim({ ...claim, policyNumber: e.target.value })}
            style={{ display: "block", width: "100%" }}
          />
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          Description
          <textarea
            value={claim.description}
            onChange={(e) => setClaim({ ...claim, description: e.target.value })}
            style={{ display: "block", width: "100%" }}
          />
        </label>
        <label style={{ display: "block", marginBottom: 16 }}>
          Estimated amount (EUR)
          <input
            type="number"
            value={claim.estimatedAmount}
            onChange={(e) =>
              setClaim({ ...claim, estimatedAmount: Number(e.target.value) })
            }
            style={{ display: "block", width: "100%" }}
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Submitting…" : "Submit claim"}
        </button>
      </form>

      <h2>Progress</h2>
      <pre style={{ background: "#f4f4f4", padding: 12 }}>{log.join("\n")}</pre>

      {result && (
        <>
          <h2>Outcome</h2>
          <pre style={{ background: "#f4f4f4", padding: 12 }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </>
      )}
    </main>
  );
}
