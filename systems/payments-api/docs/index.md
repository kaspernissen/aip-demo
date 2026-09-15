# Payments API

The **system of record** for payments, and the one place in the whole trace
where real money state changes.

Written in Go — the only Go component here, which is why it is also the one
that exposed the W3C Trace Context Level 2 incompatibility recorded as upstream
Finding 11.

## Responsibilities

- Hold the payment ledger: propose, execute, refuse, read.
- Record **why** a payout did not happen (`POST /refuse`) and list refusals
  (`GET /payments`) for the reviewer console.

### What it explicitly does not do

- **It is a record, not a gate.** It applies no policy and checks no identity.
  It will move money for anything that can reach its Service — including the
  human-approved execution, which is authorized at the gateway like any other
  call and *not* by this ledger.

## Storage

**In-memory, per-process, no persistence.** Payment ids are sequential and
cumulative since the pod's last restart, so `PAY-000001` only appears on a fresh
pod. Never promise a specific payment id on stage.

Restarting the deployment is how the ledger is cleared between demo runs:

```bash
kubectl -n northstar-systems rollout restart deploy/payments-api
```

## Refusal records

A refusal stores both the trace id **and** the span id of the settlement-agent
span that was active when it was written (`refusedTraceId`, `refusedSpanId`).
Both are needed: a `Link` built with `span_id=0` is dropped by the
OpenTelemetry SDK before any exporter sees it. That is what makes the human
approval joinable back to the original refused trace.

## Telemetry

Uses the **stable** HTTP semantic-convention spelling
(`http.request.method`, `http.response.status_code`). Comparing this span's
attribute names against agentgateway's, in the same trace, is the fastest way
to see the naming divergence between vocabularies for yourself.

## Run and test

```bash
cd systems/payments-api && go test ./...
```
