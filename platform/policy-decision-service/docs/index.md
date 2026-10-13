# Policy Decision Service

**The one decision point in Northstar.** Every `tools/call` that passes through
agentgateway is referred here first, and the gateway forwards it only on a
permit.

This service is the answer to "who decided?". Nothing else in the system is
allowed to answer that question — and a hermetic test
(`tests/e2e/test_policy_denial.py::criterion_4`) walks the source of
`settlement-agent` and `payments-mcp` to prove no policy logic has leaked into
either of them.

## Responsibilities

- Answer **permit or deny** for every `tools/call`, on the caller's attested
  SPIFFE ID plus the requested amount.
- Resolve the **autonomy level** per request from the `northstar-autonomy`
  `FeatureFlag`, through the flagd sidecar — never from a compiled-in constant.
- Emit the `policy.decision` span that carries the verdict, the rule, the
  amount requested and the limit applied.

### What it explicitly does not do

- **It holds no RBAC and makes no Kubernetes API calls.** The contract it reads
  reaches it through flagd, not through the API server.
- **It does not move money.** It answers a question; `payments-api` is the only
  thing that changes payment state.
- **It is not reachable from the agent plane.** Nothing in `northstar-agents`
  can call it; only agentgateway does.

## Interfaces

| Direction | Protocol | Detail |
|---|---|---|
| In | ExtMCP over gRPC | `agentgateway.dev.ext_mcp.ExtMcp`, port `9100`. The proto is vendored verbatim at gateway v1.6.0 (byte-identical to v1.5.0). |
| Out | gRPC | flagd on `localhost:8013`, inside this same pod. |

A **denial is the `error` branch** of the response oneof; a permit is `pass`.
That distinction is why the gateway's own `Guardrail` span reports
`grpc.status = 0` on a refusal: the *call* succeeded, and the deny is in the
payload. It is how "the policy said no" stays distinguishable from "the policy
service was down".

## Fail-closed, deliberately

`EXECUTE_LIMITS` in `pds/rules.py` has **no permissive fallback**. An unknown
caller, an unknown autonomy level, an unparseable contract or an unreachable
flagd all resolve to a refusal. A PDS outage refuses payments rather than
waving them through, and the gateway is configured `failureMode: failClosed` to
match.

The limit table is keyed on `(caller, autonomy_level)`:

| Caller | Level | Execute limit |
|---|---|---|
| `settlement-agent` | `assisted` | €6,000 |
| `settlement-agent` | `autonomous` | higher — this is what the demo's flip changes |
| `senior-settlement-agent` | `assisted` | €25,000 |

`reviewer-ui` has **no row at all**, which is the stronger claim: not "it does
not try to execute payments" but "it could not if it did". That is criterion 7
in `tests/e2e/test_supervised.py`.

## Telemetry

The `policy.decision` span carries `policy.decision`, `policy.rule`,
`policy.request.amount`, `policy.limit.amount` and `policy.caller.spiffe_id`.

**Its status is deliberately not ERROR on a denial.** A refusal is the platform
working; marking it an error would page an on-call engineer for correct
behaviour.

Two or three of these spans appear per settlement — one per tool call, and a
refused settlement carries a third for `refuse_payment`.

## Run and test

```bash
cd platform/policy-decision-service
./gen-stubs.sh                      # the gRPC stubs are git-ignored on purpose
.venv/bin/python -m pytest tests -q
```

The stubs stay out of git so the image and the repo can never disagree about a
contract whose only source of truth is the vendored `proto/ext_mcp.proto`.

## Known limits

- Authorization is **exactly one hop**. The browser → claims-agent,
  claims-agent → settlement-agent and payments-mcp → payments-api hops carry no
  identity and consult no policy.
- The caller identity is attested, but `claims-agent` and `customer-ui` still
  **share one SPIFFE ID** — carried debt, measured and written down.
