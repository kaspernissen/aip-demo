# Payments API

The **system of record** for payments, and the one place in the whole trace
where real money state changes.

Written in Go — the only Go component here, which is why it is also the one
that exposed the W3C Trace Context Level 2 incompatibility recorded in
docs/upstream-findings.md.

## Responsibilities

- Hold the payment ledger: propose, execute, refuse, reject, read.
- Record **why** a payout is waiting for a human (`POST /refuse`), list what is
  waiting (`GET /payments?status=awaiting_approval`) for the reviewer console,
  and record a human's rejection (`POST /reject`).

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

## Payment states

`proposed` becomes `executed` when the agent releases it within its band, or
`awaiting_approval` when the policy refused (`POST /refuse`). A human then
releases it (`executed`, through the gateway and the policy decision service)
or rejects it (`rejected`, `POST /reject`). `executed` and `rejected` are final.
Refuse also stores the customer's A2A task id and stamps `awaitingSince`.

## Refusal records

A refusal stores both the trace id **and** the span id of the settlement-agent
span that was active when it was written (`refusedTraceId`, `refusedSpanId`).
Both are needed: a `Link` built with `span_id=0` is dropped by the
OpenTelemetry SDK before any exporter sees it. That is what makes the refusal joinable to its trace.

## Telemetry

Uses the **stable** HTTP semantic-convention spelling
(`http.request.method`, `http.response.status_code`). Comparing this span's
attribute names against agentgateway's, in the same trace, is the fastest way
to see the naming divergence between vocabularies for yourself.

## Run and test

```bash
cd systems/payments-api && go test ./...
```
