# policy-decision-service

The Policy Decision Service (PDS). agentgateway calls it over gRPC on every MCP
`tools/call` and it answers permit or deny, so a €6,200 payout is refused on
caller identity plus amount rather than on the agent's good behaviour.

It implements `agentgateway.dev.ext_mcp.ExtMcp` on port **9100**.

## Generate the gRPC stubs first

```
cd platform/policy-decision-service && ./gen-stubs.sh
```

`proto/ext_mcp.proto` is vendored from agentgateway v1.5.0 and is the only
source of truth for the wire contract. The generated `ext_mcp_pb2.py` and
`ext_mcp_pb2_grpc.py` are **git-ignored** and produced by that script — at dev
time by hand, and again inside `docker build`, which runs the same file
(ruling R22). Committing them would let the image and the repo disagree.

Without them the test suite refuses to start: `tests/conftest.py` raises and
pytest exits 4, collecting nothing. That is deliberate. A missing stub is a
broken build, and a `skipif` would leave the suite green while the contract
went unchecked.

## Tests

```
cd platform/policy-decision-service && .venv/bin/python -m pytest tests -q
```

The service's own `.venv` is the canonical runner: bare `python3` here is
3.10.4 and `uv run pytest` picks up homebrew 3.13, because there is no root
`pyproject.toml`. Target is Python 3.13.

| file | what it holds |
|------|---------------|
| `tests/test_rules.py` | the policy, exhaustively — no gRPC, no OTel, no I/O |
| `tests/test_identity.py` | what counts as a caller |
| `tests/test_server.py` | the wire adaptation: `parse_call`, `build_result` |
| `tests/test_servicer.py` | `CheckRequest`/`CheckResponse` and the span they emit |

## Layout

| file | what it is |
|------|-----------|
| `pds/rules.py` | the policy. Pure: it never learns what a wire is. |
| `pds/identity.py` | the caller's SPIFFE ID, out of `metadata_context` |
| `pds/telemetry.py` | the `policy.decision` span and its attribute keys |
| `pds/server.py` | the ExtMCP servicer, and `python -m pds.server` |

## Two things that look like bugs and are not

**`CheckRequest` never raises.** Every internal failure becomes a deny with the
rule `internal_error`. A gRPC error would instead be routed by the gateway's
`failureMode` and reported as `mcpGuardrails checkRequest failed` — a PDS
*outage*, which is a different sentence from a policy refusal, both on stage and
in a trace.

**A deny is not an error span.** `policy.decision` leaves its status UNSET on a
refusal. Refusing is this service working; the decision is carried by the
`policy.decision` attribute, not by the span status.

## Telemetry

`policy.decision` is Gap 3 in `docs/telemetry-contract.md`: no OpenTelemetry
semantic convention exists for a policy or autonomy decision, so its nine
attribute names are ours. They are listed once, in `pds/telemetry.py`, and are
prefixed `policy.` / `northstar.` so a reader can tell at a glance which
attributes we invented and which the frameworks emit (`gen_ai.*`, `mcp.*`).

The span is a child of the caller's trace: `traceparent` is forwarded to the
guardrail hook, and `context_from_headers` extracts it with the standard W3C
propagator. agentgateway emits its own `Guardrail` CLIENT span for the call that
reaches us; we add nothing to it and invent nothing for it.
