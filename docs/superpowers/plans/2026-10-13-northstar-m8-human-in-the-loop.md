# M8 "Human in the loop" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the autonomy dial mean maker-checker. At `assisted` the settlement
agent may release nothing (limit €0). At `autonomous` it may release up to €6,000.
Every other payment waits, as an A2A task in `input-required`, until a human
approves or rejects it in the reviewer-ui. The reviewer-ui releases the money
itself, through the gateway and the PDS, under its own SPIFFE ID. The customer
watches one stream until the task completes.

**Architecture:** The PDS keeps the decision: two settlement-agent rows and one
caller-scoped human row (`human-approved.execute`), which the PDS allows only
when it reads the ledger status `awaiting_approval`. payments-api renames
`refused` to `awaiting_approval`, adds `rejected` and `POST /payments/{id}/reject`,
and records the claim's A2A task id. The claims-agent uses a2a-sdk 0.3.7's own
task store, `TaskUpdater`, `message/stream` and `tasks/resubscribe`. The
reviewer-ui records a span link and one histogram, `northstar.approval.wait.duration`.
agentgateway moves to v1.6.0 first (Task 0), which changes the shape of a deny,
and gains a route-level CEL ceiling (Task 4).

**Tech Stack:** agentgateway v1.6.0, Python 3.13 services (a2a-sdk[telemetry]
0.3.7, FastAPI, httpx, OpenTelemetry SDK 1.44.0), Go payments-api, Next.js 15
static export customer-ui with vitest, SPIRE JWT-SVIDs, Cilium, OpenFeature/flagd,
Kyverno, OpenSearch, Prometheus, kind.

**Spec:** `docs/superpowers/specs/2026-10-13-northstar-m8-human-in-the-loop-design.md`.
Conventions: `docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md`,
`docs/telemetry-contract.md` (measured-counts block, gate row, `GATE_PATHS`),
`docs/demo-runbook.md` §5. Measured CEL facts: `.demo/reports/cel-spike-report.md`
(git-ignored, local).

**Branch:** `m8-human-in-the-loop`, created from `m7-bounded`'s tip (the commit
that adds this plan). Task 0 is its first code commit.

**Execution shape (lean milestone).** Sonnet implements every task. Task reviews:
**Task 0** (sonnet), **Task 1** (opus: it moves the money-releasing identity),
**Task 2** (opus: it relies on a2a-sdk concurrency read from source). Tasks 3, 4
and 5 have no task review; the whole-branch review (opus) covers them. **One gate
run**, after the whole-branch review's fix wave, with Backstage rebuilt from the
clean HEAD and warmed first. The gate row is recorded MEASURED.

---

## Global Constraints

- **Never sample.** 100% of telemetry, always. No head, tail or probabilistic
  sampling, no library default that drops spans. Task 0's patch adds
  `frontendPolicies.tracing.parentNotSampled: true` for exactly this reason.
  Never drop an SDK span to make a trace look tidier: dropping is loss, and loss
  looks like sampling.
- **Pin the latest released version, verified upstream** (GitHub releases API or
  PyPI, not a local index). agentgateway **v1.6.0** is pinned by Task 0. No other
  new dependency is added by this plan; every new client is written against
  packages each service already pins.
- **Measure, then write down what you measured. Derive figures; never retype.**
  Counts, span names, attribute keys and Prometheus series names are read off the
  running system or computed from one checked place.
- **Tests fail rather than skip.** A guard that cannot run is red.
- **Mutate every guard** with `./scripts/mutate-guard.sh` (it sets
  `PYTHONDONTWRITEBYTECODE=1` and purges `__pycache__`). Disbelieve a GREEN. Never
  pipe a test command (the pipe's exit status wins).
- **Never edit a script while `scripts/setup-kind.sh` is running.** The rollout
  for every task is `./scripts/setup-kind.sh --stage 7`.
- **Never run `scripts/teardown.sh`, never `kind delete`, never touch
  `kind-registry`.** Recreating the cluster needs explicit permission, each time.
- **If a build stalls at "load metadata"**, it is the wedged
  `docker-credential-desktop`: rerun with a throwaway `DOCKER_CONFIG` whose
  `config.json` is `{}`.
- **Never install into an existing service venv.** Each venv's contents are
  pinned and asserted in that service's `test_pins.py`.
- **Leave the contract at `assisted`** at the end of every task (the resting
  state), and leave no claim waiting (`scripts/demo reset` once it exists).
- **Commit with explicit paths** (`git add <paths>` or `git commit -- <paths>`).
  No `Co-Authored-By` trailer and no generated-by footer on any commit.
- **`.env` and credentials never appear in committed content.** JWT-SVIDs are
  minted at runtime and never printed.
- **Every commit that touches `GATE_PATHS`** keeps `docs/telemetry-contract.md`'s
  current measured-counts block true: the gate row labelled
  `PREDICTED, NOT MEASURED`, the collected count and per-service counts derived
  (`.venv/bin/pytest --collect-only -q tests/` and each service's own runner).
  `tests/unit/test_telemetry_contract_counts.py` tells you which figure is stale.
- **Words on screen and in the deck are written for beginners**: short sentences,
  the decisive word first, no unexplained acronym.

## Review Focus

1. **A resume message that arrives for a task this process is not holding**
   (claims-agent restarted, or a forged resume for a task id that never waited).
   Expected: a JSON-RPC error to the sender, no change to the task, no "Settled"
   shown to anyone. Test: Task 2 Step 3 `test_a_resume_for_a_task_not_waiting_here_is_an_error`.
2. **A forged resume while the ledger still shows `awaiting_approval`.**
   Expected: the task stays `input-required`; the customer is never told "Settled".
   Test: Task 2 Step 3 `test_a_resume_the_ledger_does_not_back_changes_nothing`.
3. **The reviewer-ui approves a payment that is no longer waiting** (already
   executed, rejected, or never refused). Expected: the PDS denies under
   `human-approved.execute`, no money moves, the reviewer sees the refusal.
   Tests: Task 1 Step 2 `test_a_human_may_only_release_a_payment_awaiting_approval`
   and Task 1 Step 15 e2e `test_execute_after_reject_is_refused`.
4. **The PDS cannot read the ledger** (payments-api down, slow, or an id that is
   not a payment id). Expected: deny, fail closed, never permit. Test: Task 1
   Step 4 `test_an_unreadable_ledger_is_no_status` and
   `test_a_payment_id_that_is_not_one_is_never_fetched`.
5. **The customer's stream drops while the claim is waiting** (network blip,
   laptop sleep). Expected: one `tasks/get`, then resubscribe; the outcome still
   arrives; no polling loop. Tests: Task 2 Step 10 vitest
   `awaitOutcome reads the task once after a drop and resubscribes` and Task 2
   Step 16 e2e `test_a_dropped_stream_reconnects_and_still_sees_the_outcome`.

---

## File Structure

**Created**

| Path | Responsibility | Task |
|---|---|---|
| `platform/policy-decision-service/pds/deny_text.py` | The one deny-message formatter. Pure. | 0 |
| `platform/policy-decision-service/tests/test_deny_text.py` | Its tests. | 0 |
| `agents/settlement-agent/policy_denial_text.py` | The one deny-message parser. Pure, so the test harness can load it too. | 0 |
| `agents/settlement-agent/tests/test_policy_denial_text.py` | Its tests. | 0 |
| `platform/policy-decision-service/pds/ledger.py` | One fail-closed ledger read for the human-approval rule. | 1 |
| `platform/policy-decision-service/tests/test_ledger.py` | Its tests. | 1 |
| `apps/reviewer-ui/gateway.py` | The reviewer-ui's MCP client to agentgateway (`execute_payment`), with its JWT-SVID. | 1 |
| `apps/reviewer-ui/levels.py` | `autonomy_level_of(rule)`: the level, derived from the PDS rule name. Pure. | 1 |
| `apps/reviewer-ui/tests/test_gateway.py`, `tests/test_levels.py` | Their tests. | 1 |
| `tests/e2e/test_human_in_the_loop.py` | The live maker-checker suite (spec §6). | 1, extended in 2 and 3 |
| `agents/claims-agent/ledger.py` | `LedgerReader`: the claims-agent's read of one payment. | 2 |
| `agents/claims-agent/tests/test_task_lifecycle.py` | The a2a-sdk task lifecycle, driven through the real `DefaultRequestHandler`. | 2 |
| `apps/reviewer-ui/claims.py` | The reviewer-ui's A2A client that resumes the claim's task. | 2 |
| `apps/reviewer-ui/tests/test_claims.py` | Its tests. | 2 |
| `apps/customer-ui/src/view.ts`, `src/view.test.ts` | The waiting view's pure logic (heading, tone, timer text). | 2 |
| `apps/reviewer-ui/approval_metrics.py` | The `northstar.approval.wait.duration` histogram. | 3 |
| `tests/e2e/test_gateway_ceiling.py` | The CEL ceiling, live. | 4 |
| `tests/unit/test_gateway_ceiling_config.py` | The ceiling rule is in the config, as an allow rule. | 4 |

**Deleted:** `deploy/k8s/senior-settlement-agent.yaml`, `tests/e2e/test_supervised.py`,
`apps/reviewer-ui/approval.py`, `apps/reviewer-ui/tests/test_approval.py`,
`agents/settlement-agent/tests/test_execute_approved.py` (all Task 1).

**Modified** (the main ones; each task's Files block is exhaustive for that task):
`pds/rules.py`, `pds/server.py`, `pds/telemetry.py`, payments-api `store.go`,
`handlers.go`, `handlers_test.go`, `contracts/openapi/payments-api.yaml`,
`contracts/mcp/payments-mcp.json`, payments-mcp `server.py`, `payments_client.py`,
settlement-agent `settlement.py`, `executor.py`, `mcp_tools.py`, claims-agent
`executor.py`, `main.py`, reviewer-ui `review.py`, `main.py`, `ledger.py`,
`identity.py`, `render.py`, customer-ui `src/a2a.ts`, `src/app/page.tsx`,
`deploy/platform/agentgateway/config.yaml`, `deploy/platform/cilium-policies.yaml`,
`deploy/platform/spire/registration.yaml`, `deploy/k8s/*.yaml`,
`scripts/demo.py`, `scripts/setup-kind.sh`, `scripts/rebuild-and-deploy.sh`,
`tests/harness/live.py`, the eval-scorer, Backstage's autonomy-contract plugin,
`docs/*`, `talk/*`.

---

## Task 0: agentgateway v1.5.0 → v1.6.0, and the deny that became a tool result

**Task review: yes (sonnet).**

On v1.6.0 a `mcpGuardrails` deny on `tools/call` comes back as **HTTP 200 with a
JSON-RPC `result`**: `{"isError": true, "content": [{"type": "text", "text":
<the rejection message>}]}`. `error.data`, where the PDS's deny marker lived, is
gone (upstream PR #3526, `crates/agentgateway/src/mcp/mod.rs` `tool_error_body`).
Only `AuthorizationError.Code == INTERNAL_ERROR` keeps the JSON-RPC error form;
**do not use it** to get the old shape back, because a policy refusal is not an
internal error.

**Decision (made):** the settlement-agent treats an `isError` tool result that
carries the PDS's deny text as the policy denial. The PDS writes the rule, the
limit and the requested amount into the deny message in **one fixed format**,
produced by one function (`pds/deny_text.py::format_deny`). The agent reads it
with one function (`policy_denial_text.py::parse_deny`). A contract test feeds
the PDS's real formatter output into the agent's real parser.

The format, documented here and in both modules' docstrings:

```
<reason> [policy-denial rule=<rule> limit=<amount|none> requested=<amount|none>]
```

`<amount>` is the number with exactly two decimals (`6000.00`); `none` means
the PDS had no figure. The tag is the **last** thing in the text, so a gateway
prefix in front of it (v1.5.0 wrote `mcp: mcpGuardrails rejected: ` before the
reason) cannot break the parse.

Gateway spans also moved to stable semantic conventions (PR #3182):
`http.request.method`, `url.path`, `client.address`, `server.address`,
`server.port`, `http.response.status_code` replace the legacy `http.*` keys. The
ExtMCP proto is byte-identical and the config validates unchanged.

Already verified on v1.6.0 before this plan: the guard, the autonomous claim, the
keystone, evidence, reset; `test_caller_identity`, `test_gateway_config_drift`,
`test_gateway_is_the_only_route` pass. `test_policy_denial` and
`test_opensearch_trace_ingest` fail on the deny shape.

**Files:**
- Apply: `.demo/agw-v1.6-bump.patch` (8 files: `catalog-info.yaml`,
  `deploy/platform/agentgateway/config.yaml`, `deploy/platform/agentgateway/deployment.yaml`,
  `platform/policy-decision-service/README.md`, `platform/policy-decision-service/docs/index.md`,
  `platform/policy-decision-service/gen-stubs.sh`, `platform/policy-decision-service/proto/ext_mcp.proto`,
  `platform/policy-decision-service/tests/test_proto_pin.py`)
- Create: `platform/policy-decision-service/pds/deny_text.py`, `platform/policy-decision-service/tests/test_deny_text.py`,
  `agents/settlement-agent/policy_denial_text.py`, `agents/settlement-agent/tests/test_policy_denial_text.py`
- Modify: `platform/policy-decision-service/pds/server.py:1-24,85-89,177-223` (docstring, `DENY_JSONRPC_CODE` comment, `build_result`),
  `platform/policy-decision-service/tests/test_server.py`, `tests/test_servicer.py` (deny-body assertions),
  `agents/settlement-agent/mcp_tools.py:86-212,235-257,370-401` (`PolicyDenial`, `_deny_marker`, `_policy_denial`, `_unwrap`, `_call`'s `except` blocks),
  `agents/settlement-agent/tests/test_mcp_tools.py`, `tests/test_settlement.py:536`, `tests/test_policy_deny_contract.py` (rewrite),
  `tests/harness/live.py:232-266` (`Outcome`), `tests/e2e/test_policy_denial.py:1564-1600`, `tests/e2e/test_opensearch_trace_ingest.py`,
  `tests/e2e/test_demo_flow.py:219-243`, `tests/e2e/test_supervised.py:220-235,985-1070`,
  `tests/e2e/test_gateway_trace.py:270-305` (vocabulary), `scripts/demo.py:403-408`, `tests/unit/test_demo_driver.py:140`,
  `tests/unit/test_doc_status_lines_name_the_current_milestone.py:31` (regex), `docs/architecture.md:1-3`,
  `docs/telemetry-contract.md` (status line, counts block, gate row, agentgateway attribute sections at `:3194-3440,3479-3620`),
  `docs/upstream-findings.md` (one new finding)

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `pds.deny_text.format_deny(reason: str, rule: str, limit: float | None, requested: float | None) -> str`; `pds.deny_text.DENY_TAG = "policy-denial"`.
  - `policy_denial_text.parse_deny(text: str) -> DenialText | None`, where
    `DenialText(reason: str, rule: str, limit: float | None, requested: float | None)` is a frozen dataclass.
  - `mcp_tools.PolicyDenial(message, *, rule, limit, requested, retryable=False, body=None)` (unchanged signature), now raised from `_unwrap`.
  - `tests.harness.live.parse_deny(text: str)` (the same parser, loaded from its file) and `Outcome.denial -> DenialText | None`.
  - Gateway span keys, as measured on v1.6.0, in `tests/e2e/test_gateway_trace.py::GATEWAY_TOOLS_CALL_SERVER_ATTRS`.

- [ ] **Step 1: Create the branch and apply the saved bump**

```bash
cd /Users/kaspernissen/kaspernissen/aip-demo
git switch m7-bounded && git status --short          # expect: clean
git switch -c m8-human-in-the-loop
git apply --check .demo/agw-v1.6-bump.patch
git apply .demo/agw-v1.6-bump.patch
git diff --stat                                        # expect: exactly the 8 files listed above
grep -n "parentNotSampled: true" deploy/platform/agentgateway/config.yaml   # expect one hit
```

- [ ] **Step 2: Bump the status lines to M8, and let the guard see this plan**

`tests/unit/test_doc_status_lines_name_the_current_milestone.py` derives the
milestone from the newest plan whose name matches `^\d{4}-\d{2}-\d{2}-m(\d+)`.
This plan is named `2026-10-13-northstar-m8-human-in-the-loop.md`, so today the
guard skips it and still demands M6. Widen the regex so it sees this plan:

```python
_PLAN_MILESTONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:northstar-)?m(\d+)(?:\.\d+)?-", re.I)
```

Then set `docs/architecture.md` line 1 to
`# Northstar Architecture — M8 (Human in the loop), on M7's Bounded` and its
`Status:` line to start `Status: **M8 (Human in the loop)**, on branch
`m8-human-in-the-loop`, on top of **M7 (Bounded)**`, keeping the rest of the
paragraph. Set `docs/telemetry-contract.md`'s `Status:` paragraph to start
`Status: **M8 (Human in the loop)** -- current milestone, branch
`m8-human-in-the-loop`, on top of **M7 (Bounded)**`.

Run: `.venv/bin/pytest tests/unit/test_doc_status_lines_name_the_current_milestone.py -q`
Expected: PASS. Then mutate: `./scripts/mutate-guard.sh` with the H1 line set
back to `M7` → the guard must go RED naming `'M8'`.

- [ ] **Step 3: Write the failing PDS formatter test**

`platform/policy-decision-service/tests/test_deny_text.py`:

```python
"""The one deny-message format. agentgateway v1.6.0 drops `error.data`, so the
rule, the limit and the requested amount travel in the message text itself."""
from pds.deny_text import DENY_TAG, format_deny


def test_the_format_is_reason_then_one_bracketed_tag_at_the_end():
    text = format_deny(
        "requested 6200 EUR exceeds the 6000 EUR limit",
        "settlement-agent.assisted.execute_limit", 6000.0, 6200.0,
    )
    assert text == (
        "requested 6200 EUR exceeds the 6000 EUR limit "
        "[policy-denial rule=settlement-agent.assisted.execute_limit "
        "limit=6000.00 requested=6200.00]"
    )


def test_a_missing_figure_is_written_none_not_zero():
    text = format_deny("cannot evaluate", "internal_error", None, None)
    assert text.endswith("[policy-denial rule=internal_error limit=none requested=none]")


def test_a_zero_limit_is_written_as_a_number():
    # M8's assisted row is EUR 0: zero is a real limit, not an absent one.
    assert "limit=0.00" in format_deny("r", "settlement-agent.assisted.execute_limit", 0.0, 5800.0)


def test_the_tag_name_is_the_documented_one():
    assert DENY_TAG == "policy-denial"
```

Run: `cd platform/policy-decision-service && .venv/bin/pytest tests/test_deny_text.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'pds.deny_text'`.

- [ ] **Step 4: Implement `pds/deny_text.py`**

```python
"""The deny message, in the one format the settlement-agent parses.

agentgateway v1.6.0 returns a `mcpGuardrails` deny on `tools/call` as an MCP
tool result, `{"isError": true, "content": [{"type": "text", "text": ...}]}`,
and drops `error.data` (upstream PR #3526, `tool_error_body` in
crates/agentgateway/src/mcp/mod.rs). The text is all that reaches the agent, so
the facts the agent needs ride in it, in a fixed shape at the very end:

    <reason> [policy-denial rule=<rule> limit=<amount|none> requested=<amount|none>]

`<amount>` has exactly two decimals. `none` means the PDS had no figure.
The parser is agents/settlement-agent/policy_denial_text.py::parse_deny, and
agents/settlement-agent/tests/test_policy_deny_contract.py feeds this function's
real output into it. Change one, and that test goes red.
"""
from __future__ import annotations

DENY_TAG = "policy-denial"


def _amount(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"


def format_deny(reason: str, rule: str, limit: float | None, requested: float | None) -> str:
    return (
        f"{reason} [{DENY_TAG} rule={rule} limit={_amount(limit)} "
        f"requested={_amount(requested)}]"
    )
```

Run: `.venv/bin/pytest tests/test_deny_text.py -q` → Expected: 4 passed.

- [ ] **Step 5: Make `build_result` write the formatted text in both places**

The gateway's rejection text is `rej.message`. Which of `AuthorizationError.reason`
and `mcp_error.message` it reads is not ours to rely on, so both carry the same
formatted string. In `pds/server.py::build_result`, replace the deny branch with:

```python
    text = format_deny(reason, rule, limit, requested)
    body = {
        "code": DENY_JSONRPC_CODE,
        "message": text,
        "data": {
            "rule": rule,
            "limit": limit,
            "requested": requested,
            "retryable": False,
            "decision": "deny",
        },
    }
    return pb.McpRequestResult(
        error=pb.AuthorizationError(
            code=pb.AuthorizationError.PERMISSION_DENIED,
            reason=text,
            mcp_error=json.dumps(body).encode("utf-8"),
        )
    )
```

Add `from pds.deny_text import format_deny`. Keep `PERMISSION_DENIED` (never
`INTERNAL_ERROR`). Rewrite the module docstring's last paragraph (`:17-23`) and
the `DENY_JSONRPC_CODE` comment (`:85-88`) to say what v1.6.0 does: the client
receives an `isError` tool result whose text is this message; `error.data` no
longer reaches it. In `tests/test_server.py` and `tests/test_servicer.py`, change
every assertion on `result.error.reason` to compare against `format_deny(...)`
and add:

```python
def test_reason_and_mcp_error_message_carry_the_same_formatted_text():
    result = build_result(permit=False, reason="r", rule="x.y", limit=0.0, requested=5800.0)
    body = json.loads(result.error.mcp_error)
    assert result.error.reason == body["message"]
    assert result.error.reason.endswith("[policy-denial rule=x.y limit=0.00 requested=5800.00]")
```

Run: `cd platform/policy-decision-service && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 6: Write the failing parser test in the settlement-agent**

`agents/settlement-agent/tests/test_policy_denial_text.py`:

```python
from policy_denial_text import DenialText, parse_deny

TAG = "[policy-denial rule=settlement-agent.assisted.execute_limit limit=0.00 requested=5800.00]"


def test_parses_rule_limit_and_requested_from_the_end_of_the_text():
    got = parse_deny(f"requested 5800 EUR exceeds the 0 EUR limit {TAG}")
    assert got == DenialText(
        reason="requested 5800 EUR exceeds the 0 EUR limit",
        rule="settlement-agent.assisted.execute_limit",
        limit=0.0,
        requested=5800.0,
    )


def test_a_gateway_prefix_in_front_does_not_matter():
    got = parse_deny(f"mcp: mcpGuardrails rejected: some reason {TAG}")
    assert got is not None and got.rule == "settlement-agent.assisted.execute_limit"


def test_none_is_none():
    got = parse_deny("r [policy-denial rule=internal_error limit=none requested=none]")
    assert got.limit is None and got.requested is None


def test_an_ordinary_tool_error_is_not_a_denial():
    # payments-api's own 409, as payments-mcp surfaces it: a malfunction, never a policy no.
    assert parse_deny("Error calling tool 'execute_payment': Client error '409 Conflict'") is None


def test_a_tag_that_is_not_at_the_end_is_not_a_denial():
    assert parse_deny(f"{TAG} and then more text") is None


def test_a_malformed_amount_is_not_a_denial():
    assert parse_deny("r [policy-denial rule=x limit=6000 requested=none]") is None
```

Run: `cd agents/settlement-agent && .venv/bin/pytest tests/test_policy_denial_text.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'policy_denial_text'`.

- [ ] **Step 7: Implement `agents/settlement-agent/policy_denial_text.py`**

```python
"""Read a PDS deny out of an MCP tool result's text.

The format is written by platform/policy-decision-service/pds/deny_text.py::
format_deny and documented there. This module is dependency-free on purpose:
the e2e harness (tests/harness/live.py) loads this exact file, so the agent and
the tests share one parser rather than two copies that can drift.

Recognition is by the tag at the very end of the text, never by the wording of
the reason. A reworded reason still parses; a tool error that does not end in
the tag is not a policy decision, and must stay a malfunction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_AMOUNT = r"none|-?\d+\.\d{2}"
_TAG_AT_END = re.compile(
    r"\s*\[policy-denial rule=(?P<rule>[A-Za-z0-9_.\-]+) "
    rf"limit=(?P<limit>{_AMOUNT}) requested=(?P<requested>{_AMOUNT})\]\s*\Z"
)


@dataclass(frozen=True)
class DenialText:
    reason: str
    rule: str
    limit: float | None
    requested: float | None


def _amount(raw: str) -> float | None:
    return None if raw == "none" else float(raw)


def parse_deny(text: str) -> DenialText | None:
    match = _TAG_AT_END.search(text)
    if match is None:
        return None
    return DenialText(
        reason=text[: match.start()].strip(),
        rule=match.group("rule"),
        limit=_amount(match.group("limit")),
        requested=_amount(match.group("requested")),
    )
```

Run: `.venv/bin/pytest tests/test_policy_denial_text.py -q` → Expected: 6 passed.

- [ ] **Step 8: Make `_unwrap` raise `PolicyDenial` for the deny text**

In `mcp_tools.py`, delete `_deny_marker` and `_policy_denial` (they read
`error.data`, which v1.6.0 no longer sends). Replace `_unwrap`'s `is_error` branch:

```python
    if getattr(result, "is_error", False):
        detail = "; ".join(
            block.text
            for block in (result.content or [])
            if getattr(block, "text", None)
        )
        denial = parse_deny(detail)
        if denial is not None:
            raise PolicyDenial(
                f"policy refused this call: {denial.reason} (rule {denial.rule})"
                " — this refusal is final. Making the same call again will be "
                "refused again. Explain to the claimant what happened instead.",
                rule=denial.rule,
                limit=denial.limit,
                requested=denial.requested,
                retryable=False,
                body={"rule": denial.rule, "limit": denial.limit,
                      "requested": denial.requested, "decision": "deny"},
            )
        raise ToolCallError(detail or "tool call failed with no error detail")
```

Add `from policy_denial_text import parse_deny`. In `_leaf_exception`, prefer a
`PolicyDenial` leaf first, then a `ToolCallError` leaf:

```python
    for wanted in (PolicyDenial, ToolCallError):
        for leaf in leaves:
            if isinstance(leaf, wanted):
                return leaf
    return leaves[0] if leaves else exc
```

In `_call`, the `except BaseExceptionGroup` block becomes
`raise _leaf_exception(eg) from eg`, and the `except MCPError` block is deleted:
a JSON-RPC error from the gateway is no longer how a policy decision arrives, so
it must propagate as the malfunction it is. Rewrite `PolicyDenial`'s docstring's
third bullet: the facts come from the deny text's tag, because v1.6.0 drops
`error.data`.

Update `tests/test_mcp_tools.py` (every `MCPError(code=-32001, ...)` fixture at
`:615-620,709,756,776,839`) and `tests/test_settlement.py:536` to build a
`CallToolResult(isError=True, content=[TextContent(type="text", text=format)])`
whose text is a literal formatted deny, and assert `PolicyDenial` with the parsed
rule, limit and requested. Keep the regression test that a `CallToolResult` with
`isError=True` and **no** tag raises `ToolCallError`, never `PolicyDenial`.

Run: `cd agents/settlement-agent && .venv/bin/pytest -q` → Expected: all pass
except `tests/test_policy_deny_contract.py` (next step).

- [ ] **Step 9: Rewrite the deny contract test against both real implementations**

`agents/settlement-agent/tests/test_policy_deny_contract.py` keeps its test-only
`sys.path` borrowing of the PDS source and venv (its docstring explains why) and
replaces the chain with the v1.6.0 one:

```python
from mcp.types import CallToolResult, TextContent

from mcp_tools import PolicyDenial, _unwrap


def _as_it_arrives_at_the_agent(pds_result) -> CallToolResult:
    """agentgateway v1.6.0's mapping, measured: a PERMISSION_DENIED rejection on
    tools/call becomes HTTP 200, `result: {isError: true, content: [{type: text,
    text: <rejection message>}]}`. The PDS puts the same text in `reason` and in
    `mcp_error.message`, so this does not depend on which one the gateway reads."""
    assert pds_result.WhichOneof("result") == "error"
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=pds_result.error.reason)],
    )


def test_the_pds_deny_is_a_policy_denial_at_the_agent():
    call = server.parse_call(MEASURED_EXECUTE_6200)
    decision = rules.decide(rules.PolicyInput(
        caller=AGENT, tool=call.tool, amount=call.amount,
        currency=call.currency, autonomy_level="assisted"))
    assert decision.permit is False
    pds_result = server.build_result(
        permit=False, reason=decision.reason, rule=decision.rule,
        limit=decision.limit, requested=call.amount)

    with pytest.raises(PolicyDenial) as raised:
        _unwrap(_as_it_arrives_at_the_agent(pds_result))

    assert raised.value.rule == decision.rule
    assert raised.value.limit == decision.limit
    assert raised.value.requested == call.amount
    assert raised.value.retryable is False


def test_the_pds_internal_error_deny_is_also_a_policy_denial():
    pds_result = server._internal_error_result()
    with pytest.raises(PolicyDenial) as raised:
        _unwrap(_as_it_arrives_at_the_agent(pds_result))
    assert raised.value.rule == "internal_error"
```

`MEASURED_EXECUTE_6200` and `AGENT` are the file's existing constants (keep them).
Rewrite the module docstring's chain to: measured bytes → `parse_call` → `decide`
→ `build_result` → v1.6.0 mapping → `CallToolResult` → `_unwrap`. The concrete
regression check becomes: change `DENY_TAG` in `pds/deny_text.py` and
`test_the_pds_deny_is_a_policy_denial_at_the_agent` fails here.

Run: `cd agents/settlement-agent && .venv/bin/pytest -q` → Expected: all pass.
Mutate with `./scripts/mutate-guard.sh`: set `DENY_TAG = "policy-deny"` in
`pds/deny_text.py` → this file must go RED.

- [ ] **Step 10: Give the harness the same parser**

In `tests/harness/live.py`, load the agent's parser from its file (test-only,
the same borrowing the contract test does), and add a property to `Outcome`:

```python
import importlib.util

_DENIAL_TEXT = REPO_ROOT / "agents" / "settlement-agent" / "policy_denial_text.py"
_spec = importlib.util.spec_from_file_location("policy_denial_text", _DENIAL_TEXT)
_denial_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_denial_text)
parse_deny = _denial_text.parse_deny
```

(`REPO_ROOT` is `Path(__file__).resolve().parents[2]`; add it if the module has
none.) In `Outcome`:

```python
    @property
    def denial(self):
        """The PDS's policy denial, if this tools/call result is one.

        agentgateway v1.6.0 returns it as HTTP 200 with an `isError` tool result
        whose text ends in the `[policy-denial ...]` tag."""
        result = self.tool_result or {}
        if not result.get("isError"):
            return None
        text = " ".join(c.get("text", "") for c in result.get("content") or [])
        return parse_deny(text)
```

Rewrite the `Outcome` docstring's v1.5.0 sentence ("every rejection case
measured") to name v1.6.0. Then change the e2e assertions that read the deny
shape:

- `tests/e2e/test_policy_denial.py:1564-1600`: `stranger.tool_result is None` and
  `stranger.jsonrpc_error` become `stranger.http_status == 200`,
  `stranger.tool_result["isError"] is True`, and `stranger.denial.rule == "unknown_caller"`.
- `tests/e2e/test_demo_flow.py:219-243`: rename to
  `test_2b_a_refusal_is_http_200_carrying_an_is_error_tool_result`; assert
  `executed.http_status == 200`, `executed.tool_result["isError"] is True`,
  `executed.denial.rule == "settlement-agent.assisted.execute_limit"`,
  `executed.denial.limit == 6000.0`.
- `tests/e2e/test_supervised.py:220-235,985-1070` (deleted in Task 1; keep the
  tree coherent until then): replace `_deny_marker(result.jsonrpc_error)` with
  `result.denial`.
- `tests/e2e/test_opensearch_trace_ingest.py`: wherever it reads the refusal off
  a JSON-RPC error, read `outcome.denial` instead.

- [ ] **Step 11: Roll the cluster out and measure the gateway's span vocabulary**

```bash
./scripts/setup-kind.sh --stage 7          # do not edit any script while this runs
kubectl --context kind-northstar -n northstar-platform get deploy agentgateway \
  -o jsonpath='{.spec.template.spec.containers[0].image}'   # expect ...:v1.6.0
./scripts/demo claim 5800                  # M7 limits still apply: executed at assisted
```

Then dump one `tools/call` SERVER span from the `agentgateway` service in Jaeger
**in full** (every tag key), from the trace the claim printed. Replace
`tests/e2e/test_gateway_trace.py::GATEWAY_TOOLS_CALL_SERVER_ATTRS` (`:270-305`)
with exactly the measured key set minus `JAEGER_ADDED_TAGS`. Expect the six
renames (`http.request.method`, `url.path`, `client.address`, `server.address`,
`server.port`, `http.response.status_code`) but **write what you read**, not this
list. Do the same for the CLIENT span set and the refused-call SERVER span. Then:

- `scripts/demo.py:403`: `status = gateway.attrs.get("http.response.status_code")`.
  `:408` says: `HTTP 200: the answer is a tool result marked isError. A no is not an HTTP failure.`
- `tests/unit/test_demo_driver.py:140`: the fake span's key becomes `"http.response.status_code": 200`.
- `docs/telemetry-contract.md`: rewrite the attribute tables under "agentgateway
  instrumentation, as measured" (`:3244-3365`) and "Attributes on a policy-denied
  `tools/call` SERVER span" (`:3545-3620`) from the measured dump, with a dated
  note that v1.6.0 moved to stable semconv. Keep `:3381-3384`'s finding as history,
  annotated "superseded at v1.6.0".
- Grep the tree for any remaining legacy key: `git grep -n -E '"http\.(status|method|path|host)"'`
  must return nothing outside historical docs.

- [ ] **Step 12: Run Task 0's verification**

```bash
.venv/bin/pytest tests/unit -q
RUN_BROWSER_TESTS=1 .venv/bin/pytest -q tests/e2e/test_policy_denial.py tests/e2e/test_gateway_trace.py \
  tests/e2e/test_opensearch_trace_ingest.py tests/e2e/test_caller_identity.py \
  tests/e2e/test_gateway_config_drift.py tests/e2e/test_gateway_is_the_only_route.py
./scripts/demo reset
./scripts/demo claim 6200 --expect refused     # assisted, M7's 6000 limit: refused
./scripts/demo reset
```

Expected: all pass; the claim prints `REFUSED` and the gateway line reads
`HTTP 200`. Read `scripts/demo claim 6200`'s narrative: the model must still
explain the refusal (that proves `PolicyDenial` reached it as a `ToolFailed`).

- [ ] **Step 13: Record the finding and the figures**

Add a finding to `docs/upstream-findings.md` (local, not filed): "v1.6.0 returns
an ExtMCP deny on `tools/call` as an `isError` tool result and drops `error.data`
(PR #3526); only `INTERNAL_ERROR` keeps the JSON-RPC error form", with the
measured wire bytes from Step 12. Mark the gate row `PREDICTED, NOT MEASURED` and
refresh the counts block (derive the numbers; run
`.venv/bin/pytest tests/unit/test_telemetry_contract_counts.py -q` until green).
The deck's "HTTP 200 means no" and `-32001` talk points are updated in **Task 5**
with the other deck work.

- [ ] **Step 14: Commit**

```bash
git add catalog-info.yaml deploy/platform/agentgateway platform/policy-decision-service \
  agents/settlement-agent tests/harness/live.py tests/e2e tests/unit scripts/demo.py \
  docs/architecture.md docs/telemetry-contract.md docs/upstream-findings.md
git commit -m "feat(gateway): agentgateway v1.6.0, and read the deny from the tool result's text"
```

---

## Task 1: Policy and ledger — the dial means maker-checker, and the human releases the money

**Task review: yes (opus).** This task changes which identity may move money.

**Deviation from spec §7, stated:** the reviewer-ui's `execute_payment` through
the gateway is built **here**, not in Task 2. Deleting the senior agent without
its replacement in the same task would leave every task boundary with no
approval path at all. Task 2 adds only the *resume* of the customer's task.

**Choice for "only when the ledger shows `awaiting_approval`" (spec §3.1):** the
PDS itself reads the ledger, once, for a `human-approved` caller only. This is the
least custom choice that is actually enforced by the platform: a check the
reviewer-ui does for itself (as the senior agent did with `get_payment`) is a
check a compromised reviewer-ui skips. The reviewer-ui still reads the ledger
first (to build the call and to show the reviewer what they approve), but the
**PDS** decides. The read is one `GET /payments/{id}` from `pds/ledger.py`
(stdlib `urllib`, 2 s timeout, `traceparent` injected so payments-api's span
joins trace B), only for callers in `HUMAN_APPROVERS`, and any failure is "no
status", which denies. The PDS reaches payments-api with no Cilium change: no
policy selects the PDS's egress or payments-api's ingress (`deploy/platform/cilium-policies.yaml`
names neither); Step 13 measures it live anyway.

`rules.py` stays pure: the server fetches the status and hands it in as
`PolicyInput.ledger_status`. The status is an input that *widens* what one
caller may do, which `rules.py`'s own comment (`:64-70`) warns against. It is
admissible here for a stated reason: it widens only for an attested identity in
`HUMAN_APPROVERS`, and the only way a payment reaches `awaiting_approval` is
`refuse_payment`, which `CALLER_SCOPED_TOOLS` restricts to the settlement-agent.
The reviewer-ui cannot write the fact that unlocks it. The comment says so.

**Files:**
- Modify: `platform/policy-decision-service/pds/rules.py:15-80,83-90,118-226`, `pds/server.py:100-175,284-361`,
  `pds/telemetry.py` (one constant), `tests/test_rules.py`, `tests/test_servicer.py`, `tests/test_server.py`
- Create: `platform/policy-decision-service/pds/ledger.py`, `tests/test_ledger.py`
- Modify: `deploy/k8s/policy-decision-service.yaml` (env `PAYMENTS_API_URL`)
- Modify: `systems/payments-api/store.go`, `handlers.go`, `handlers_test.go`, `docs/index.md`, `contracts/openapi/payments-api.yaml`
- Modify: `tools/payments-mcp/server.py:66-90`, `payments_client.py:46-77`, `tests/test_tools.py`, `tests/test_payments_client.py`, `contracts/mcp/payments-mcp.json`
- Modify: `agents/settlement-agent/settlement.py` (remove `execute_approved`, `_verify_refusal`, `UnverifiedApproval`, `_refusal_link`, `AMOUNT_EPSILON`; reply `awaiting_approval`), `executor.py` (remove the approval branch), `mcp_tools.py:428-465` (`refuse(..., a2a_task_id)`), `tests/test_settlement.py`, `tests/test_executor_reply.py`, `docs/index.md`, `catalog-info.yaml`
- Delete: `agents/settlement-agent/tests/test_execute_approved.py`
- Modify: `apps/reviewer-ui/identity.py` (JWT-SVID), `ledger.py`, `review.py`, `main.py`, `render.py`, `requirements.txt` (comments only), `bootstrap.py` (docstring), `tests/test_review.py`, `tests/test_main.py`, `tests/test_ledger.py`, `tests/test_render.py`, `docs/index.md`
- Create: `apps/reviewer-ui/gateway.py`, `levels.py`, `tests/test_gateway.py`, `tests/test_levels.py`
- Delete: `apps/reviewer-ui/approval.py`, `apps/reviewer-ui/tests/test_approval.py`
- Modify: `deploy/k8s/reviewer-ui.yaml` (env: `AGENTGATEWAY_MCP_URL`, `NORTHSTAR_GATEWAY_AUDIENCE`; remove `SENIOR_SETTLEMENT_AGENT_URL`)
- Delete: `deploy/k8s/senior-settlement-agent.yaml`
- Modify: `deploy/platform/spire/registration.yaml` (explicit `ClusterSPIFFEID` for reviewer-ui), `deploy/platform/cilium-policies.yaml` (remove the two senior policies; reviewer-ui egress: agentgateway:3000 in, senior out), `deploy/kind/stage3.yaml` (comment), `deploy/k8s/eval-scorer.yaml`, `deploy/k8s/settlement-agent.yaml` (comments), `deploy/platform/kyverno/keystone-policy.yaml:49` (comment)
- Modify: `scripts/setup-kind.sh:123-127,731,992-1030,1091`, `scripts/rebuild-and-deploy.sh:65-80,124-135,327-336`
- Modify: `platform/eval-scorer/scorer/authority.py`, `tests/test_authority_matches_the_policy.py`, `tests/fixtures.py` (senior identity → reviewer-ui)
- Modify: `platform/backstage/packages/backend/src/plugins/autonomyContract/router.ts:32-42`, `router.test.ts`, `evaluate.test.ts`, `platform/backstage/packages/app/src/modules/autonomyContract/AutonomyContractPage.tsx`, `AutonomyContractPage.test.tsx`, `platform/backstage/packages/app/src/modules/home/homeModule.tsx`
- Modify: `tests/unit/test_stage2_spire_config.py:131-170,904`, `tests/unit/test_rebuild_and_deploy_guard.py`, `tests/unit/test_stage2_pds_config.py`, `tests/unit/test_stage2_network_policy.py` (if it names senior), `tests/e2e/test_keystone.py:417-426,566-580`, `tests/e2e/test_egress_policy.py:778`, `tests/e2e/test_prometheus_metrics_ingest.py:432-436`
- Delete: `tests/e2e/test_supervised.py`
- Create: `tests/e2e/test_human_in_the_loop.py`
- Modify: `tests/harness/live.py` (`autonomy` context manager, `review`), every e2e module that expects `executed` at the resting state (Step 14 derives the list)
- Modify: `README.md`, `docs/architecture.md`, `docs/telemetry-contract.md`, `docs/demo-runbook.md` (senior removal only; the flow is Task 5), `catalog-info.yaml`, `apps/reviewer-ui/catalog-info.yaml`

**Interfaces:**
- Consumes: Task 0's `format_deny` / `parse_deny`; the deny is an `isError` tool result.
- Produces:
  - `pds.rules`: `EXECUTE_LIMITS = {(SETTLEMENT, "assisted"): 0.0, (SETTLEMENT, "autonomous"): 6000.0}`,
    `HUMAN_APPROVERS: frozenset[str]`, `AWAITING_APPROVAL = "awaiting_approval"`,
    `HUMAN_APPROVAL_RULE = "human-approved.execute"`, `PolicyInput.ledger_status: str | None = None`.
  - `pds.ledger.payment_status(payment_id: str | None, *, opener=urllib.request.urlopen) -> str | None`.
  - `pds.telemetry.ATTR_LEDGER_STATUS = "policy.ledger.status"`.
  - payments-api: statuses `proposed | awaiting_approval | executed | rejected`;
    `Payment.a2aTaskId`, `Payment.awaitingSince` (RFC3339), `Payment.rejection{reviewerId, rejectedAt}`;
    `POST /payments/{id}/refuse` body gains `a2aTaskId`; `POST /payments/{id}/reject` body `{"reviewerId"}`;
    execute of a `rejected` payment → 409 with `northstar.payment.refusal_cause=rejected`.
  - MCP tool `refuse_payment(..., a2a_task_id: str = "")`.
  - settlement-agent reply: `{"claimId", "paymentId", "amount", "currency", "status": "executed" | "awaiting_approval", "narrative", "rule", "limit"}`
    (`rule`/`limit` are `null` when executed). Claim payload accepts optional `a2aTaskId`.
  - reviewer-ui: `gateway.GatewayClient(mcp_url, audience).execute_payment(payment_id, amount, currency) -> dict`,
    raising `gateway.ExecutionRefused(text)` or `gateway.GatewayUnavailable(text)`;
    `identity.fetch_jwt_svid(audience) -> str`; `ledger.LedgerClient.list_awaiting()`, `.get()`, `.reject(payment_id, reviewer_id)`;
    `levels.autonomy_level_of(rule: str | None) -> str`;
    `review.decide(payment_id, reviewer_id, decision, *, ledger, gateway, reviewer_spiffe_id, now=None) -> ReviewResult`;
    routes `GET /`, `POST /review/{id}` (form), `GET /api/awaiting`, `POST /api/review/{id}` (JSON body `{"reviewerId","decision"}`).
  - harness: `live.autonomy(level)` (context manager), `live.review(payment_id, decision, reviewer_id) -> dict`, `live.REVIEWER_UI_URL`.

- [ ] **Step 1: Write the failing PDS rule tests**

In `platform/policy-decision-service/tests/test_rules.py`, delete the `SENIOR`
constant and every senior test, and the `TEST_SUPERVISED` criterion-4 AST test
(`:9-12` and its function; `test_supervised.py` is deleted below). Add:

```python
REVIEWER = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


def test_assisted_may_release_nothing():
    d = decide(_input(amount=0.01, autonomy_level="assisted"))
    assert d.permit is False
    assert d.rule == "settlement-agent.assisted.execute_limit"
    assert d.limit == 0.0


def test_autonomous_may_release_up_to_6000_inclusive():
    assert decide(_input(amount=6000.0, autonomy_level="autonomous")).permit is True


def test_autonomous_denies_a_cent_over_6000():
    d = decide(_input(amount=6000.01, autonomy_level="autonomous"))
    assert d.permit is False
    assert d.rule == "settlement-agent.autonomous.execute_limit"
    assert d.limit == 6000.0


def test_proposing_stays_allowed_at_every_level():
    for level in ("assisted", "autonomous"):
        assert decide(_input(tool="propose_payment", autonomy_level=level)).permit is True


def test_a_human_may_release_a_payment_awaiting_approval_at_any_level():
    for level in ("assisted", "autonomous", "some-level-nobody-published"):
        d = decide(_input(caller=REVIEWER, amount=60000.0, autonomy_level=level,
                          ledger_status="awaiting_approval"))
        assert d.permit is True
        assert d.rule == "human-approved.execute"
        assert d.limit is None


def test_a_human_may_only_release_a_payment_awaiting_approval():
    for status in ("proposed", "executed", "rejected", None):
        d = decide(_input(caller=REVIEWER, ledger_status=status))
        assert d.permit is False
        assert d.rule == "human-approved.execute"
        assert repr(status) in d.reason


def test_a_human_approval_still_refuses_a_non_amount():
    for amount in (None, 0.0, -1.0, float("nan")):
        d = decide(_input(caller=REVIEWER, amount=amount, ledger_status="awaiting_approval"))
        assert d.permit is False


def test_the_ledger_status_widens_nothing_for_the_agent():
    d = decide(_input(amount=5800.0, autonomy_level="assisted", ledger_status="awaiting_approval"))
    assert d.permit is False and d.rule == "settlement-agent.assisted.execute_limit"


def test_only_the_settlement_agent_may_record_a_refusal():
    assert decide(_input(tool="refuse_payment")).permit is True
    assert decide(_input(tool="refuse_payment", caller=REVIEWER)).permit is False


def test_no_senior_identity_is_known_any_more():
    from pds.rules import EXECUTE_LIMITS, HUMAN_APPROVERS
    named = {caller for caller, _ in EXECUTE_LIMITS} | HUMAN_APPROVERS
    assert not any("senior" in c for c in named)
```

Extend `_input`'s base dict with `ledger_status=None`. Update the existing
threshold tests for the new rows (`test_execute_above_the_threshold_is_denied`
asserts `limit == 0.0` at `assisted`; the "at" and "below" tests run at
`autonomous` with `6000.0` and `5800.0`).

Run: `cd platform/policy-decision-service && .venv/bin/pytest tests/test_rules.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'ledger_status'`).

- [ ] **Step 2: Implement the rule table**

`pds/rules.py`:

```python
EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "assisted"): 0.0,
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "autonomous"): 6000.0,
}

# Maker-checker. The agent prepares every payment; above its band, a human
# releases it. The human's tool, reviewer-ui, has no limit and no autonomy
# level: it may release exactly the payments the ledger shows waiting for a
# human, and nothing else.
#
# `ledger_status` is an input that widens what this caller may do, which the
# note above CALLER_SCOPED_TOOLS warns against. It is admissible for one
# reason: the only way a payment reaches `awaiting_approval` is refuse_payment,
# and CALLER_SCOPED_TOOLS lets only the settlement-agent call it. The caller
# this widens for cannot write the fact that widens it. server.py reads the
# status from payments-api (pds/ledger.py) and only for these callers.
HUMAN_APPROVERS: frozenset[str] = frozenset({
    "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui",
})
AWAITING_APPROVAL = "awaiting_approval"
HUMAN_APPROVAL_RULE = "human-approved.execute"
```

`CALLER_SCOPED_TOOLS["refuse_payment"]` keeps only the settlement-agent.
`PolicyInput` gains `ledger_status: str | None = None` (last, defaulted).
In `decide`, after the `LIMITED_TOOLS` check and **before** the limit lookup:

```python
    if (req.caller or "") in HUMAN_APPROVERS:
        return _human_approval(req)
```

and below `decide`:

```python
def _human_approval(req: PolicyInput) -> Decision:
    if req.amount is None or not math.isfinite(req.amount) or req.amount <= 0:
        return Decision(False, f"requested amount {req.amount!r} is not a payable amount",
                        rule="invalid_amount")
    if req.ledger_status != AWAITING_APPROVAL:
        return Decision(
            False,
            "a human approval may only release a payment awaiting approval; "
            f"the ledger shows {req.ledger_status!r}",
            rule=HUMAN_APPROVAL_RULE,
        )
    return Decision(True, "a human released a payment the ledger shows awaiting approval",
                    rule=HUMAN_APPROVAL_RULE)
```

Rewrite the long comment above the `amount > limit` test (`:185-211`): it
describes three principals and the senior/junior naming criterion. It now
describes one settlement identity at two levels, and points to `_human_approval`
for the reviewer-ui. Delete the senior paragraph from the `EXECUTE_LIMITS`
comment and `UNLIMITED_TOOLS`' `execute_approved` sentence (`:49-52`: `get_payment`
stays unlimited because the reviewer-ui and claims-agent read through it or
alongside it).

Run: `.venv/bin/pytest tests/test_rules.py -q` → Expected: all pass.

- [ ] **Step 3: Write the failing ledger-read tests**

`platform/policy-decision-service/tests/test_ledger.py`:

```python
import io
import json
import urllib.error

from pds.ledger import payment_status


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body=None, exc=None, seen=None):
    def open_(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        if exc:
            raise exc
        return _Resp(json.dumps(body).encode())
    return open_


def test_reads_the_status_off_the_ledger_record():
    seen = []
    assert payment_status("PAY-000053", opener=_opener({"status": "awaiting_approval"}, seen=seen)) == "awaiting_approval"
    request, timeout = seen[0]
    assert request.full_url.endswith("/payments/PAY-000053")
    assert timeout <= 2.0


def test_an_unreadable_ledger_is_no_status():
    for exc in (urllib.error.URLError("refused"), TimeoutError(), ValueError("bad json")):
        assert payment_status("PAY-000053", opener=_opener(exc=exc)) is None


def test_a_record_without_a_string_status_is_no_status():
    assert payment_status("PAY-000053", opener=_opener({"status": 7})) is None
    assert payment_status("PAY-000053", opener=_opener(["not", "a", "record"])) is None


def test_a_payment_id_that_is_not_one_is_never_fetched():
    seen = []
    for bad in (None, "", "../admin", "PAY-1", "PAY-000053/execute", "PAY-000053?x=1"):
        assert payment_status(bad, opener=_opener({"status": "awaiting_approval"}, seen=seen)) is None
    assert seen == []
```

Run: `.venv/bin/pytest tests/test_ledger.py -q` → Expected: FAIL (`No module named 'pds.ledger'`).

- [ ] **Step 4: Implement `pds/ledger.py`**

```python
"""The one ledger read the policy needs: is this payment awaiting a human?

Read only for callers in rules.HUMAN_APPROVERS, only for execute_payment.
Every failure (unreachable, slow, not JSON, no status, an id that is not a
payment id) returns None, and rules._human_approval denies on None. That is
fail-closed: a ledger the PDS cannot read never widens what is permitted.

stdlib urllib, not a new HTTP dependency. `traceparent` is injected by hand so
payments-api's `GET /payments/{paymentId}` span joins the decision's trace.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from opentelemetry import propagate

LEDGER_URL = os.environ.get(
    "PAYMENTS_API_URL", "http://payments-api.northstar-systems.svc.cluster.local:8080"
).rstrip("/")
TIMEOUT_S = 2.0
# payments-api's own id shape (store.go: fmt.Sprintf("PAY-%06d", ...)). Anything
# else is never put into a URL.
_PAYMENT_ID = re.compile(r"PAY-\d{6}")


def payment_status(payment_id: str | None, *, opener=urllib.request.urlopen) -> str | None:
    if not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id):
        return None
    request = urllib.request.Request(f"{LEDGER_URL}/payments/{payment_id}")
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for key, value in carrier.items():
        request.add_header(key, value)
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            record = json.load(response)
    except Exception:  # noqa: BLE001 -- every failure is "no status": deny
        return None
    status = record.get("status") if isinstance(record, dict) else None
    return status if isinstance(status, str) else None
```

Run: `.venv/bin/pytest tests/test_ledger.py -q` → Expected: 4 passed.

- [ ] **Step 5: Wire the read into the servicer**

In `pds/server.py`: `ToolCall` gains `payment_id: str | None`; `parse_call` reads
`arguments.get("payment_id")` (a `str` or `None`). In `_decide`:

```python
        ledger_status = None
        if (caller or "") in HUMAN_APPROVERS and call.tool in LIMITED_TOOLS:
            ledger_status = payment_status(call.payment_id)
            span.set_attribute(telemetry.ATTR_LEDGER_STATUS, ledger_status or "unreadable")
```

and pass `ledger_status=ledger_status` into `PolicyInput`. Add
`ATTR_LEDGER_STATUS = "policy.ledger.status"` to `pds/telemetry.py` with a comment
(set only on a human-approval decision; `unreadable` when the read failed). Add
to `tests/test_servicer.py`:

```python
def test_a_reviewer_execute_reads_the_ledger_and_permits_when_awaiting(monkeypatch, servicer, request_for):
    monkeypatch.setattr(server, "payment_status", lambda pid: "awaiting_approval")
    result = servicer.CheckRequest(request_for(caller=REVIEWER, tool="execute_payment",
                                               arguments={"payment_id": "PAY-000001", "amount": 5800.0}), None)
    assert result.WhichOneof("result") == "pass"


def test_a_reviewer_execute_denies_when_the_ledger_is_unreadable(monkeypatch, servicer, request_for):
    monkeypatch.setattr(server, "payment_status", lambda pid: None)
    result = servicer.CheckRequest(request_for(caller=REVIEWER, tool="execute_payment",
                                               arguments={"payment_id": "PAY-000001", "amount": 5800.0}), None)
    assert result.WhichOneof("result") == "error"
    assert "rule=human-approved.execute" in result.error.reason


def test_the_agent_never_triggers_a_ledger_read(monkeypatch, servicer, request_for):
    calls = []
    monkeypatch.setattr(server, "payment_status", lambda pid: calls.append(pid))
    servicer.CheckRequest(request_for(caller=AGENT, tool="execute_payment",
                                      arguments={"payment_id": "PAY-000001", "amount": 5800.0}), None)
    assert calls == []
```

Use the file's existing request-building helper; if its name differs from
`request_for`, adapt the fixture name, not the assertions. Import
`payment_status` into `server.py` as a module attribute
(`from pds.ledger import payment_status`) so `monkeypatch.setattr(server, ...)` works.
Add `PAYMENTS_API_URL` to `deploy/k8s/policy-decision-service.yaml`'s container env.

Run: `cd platform/policy-decision-service && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 6: Write the failing payments-api tests**

In `systems/payments-api/handlers_test.go`, rename every `StatusRefused` /
`"refused"` expectation to `StatusAwaitingApproval` / `"awaiting_approval"`,
delete `TestARefusedPaymentCanStillBeExecutedAfterwards` (`:698`), and add:

```go
func TestRefusingRecordsTheTaskIdAndWhenTheWaitStarted(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := s.propose("CLM-1", 5800, "EUR")
	body := `{"rule":"settlement-agent.assisted.execute_limit","limit":0,"requested":5800,` +
		`"narrative":"n","refusedTraceId":"t","refusedSpanId":"s","a2aTaskId":"task-1"}`
	rec := do(router, "POST", "/payments/"+p.PaymentID+"/refuse", body)
	if rec.Code != http.StatusOK {
		t.Fatalf("refuse: %d %s", rec.Code, rec.Body)
	}
	got, _ := s.get(p.PaymentID)
	if got.Status != StatusAwaitingApproval || got.A2ATaskID != "task-1" || got.AwaitingSince == "" {
		t.Fatalf("got %+v", got)
	}
}

func TestRejectMovesAwaitingToRejectedAndRecordsTheReviewer(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := awaiting(t, s, router)
	rec := do(router, "POST", "/payments/"+p.PaymentID+"/reject", `{"reviewerId":"alex.mercer"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("reject: %d %s", rec.Code, rec.Body)
	}
	got, _ := s.get(p.PaymentID)
	if got.Status != StatusRejected || got.Rejection == nil || got.Rejection.ReviewerID != "alex.mercer" || got.Rejection.RejectedAt == "" {
		t.Fatalf("got %+v", got)
	}
}

func TestRejectOfAPaymentNotAwaitingIsAConflict(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := s.propose("CLM-1", 5800, "EUR")
	if rec := do(router, "POST", "/payments/"+p.PaymentID+"/reject", `{"reviewerId":"a"}`); rec.Code != http.StatusConflict {
		t.Fatalf("want 409, got %d", rec.Code)
	}
}

func TestRejectWithoutAReviewerIsABadRequest(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := awaiting(t, s, router)
	if rec := do(router, "POST", "/payments/"+p.PaymentID+"/reject", `{}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("want 400, got %d", rec.Code)
	}
}

func TestExecuteRefusesARejectedPaymentAndSaysWhy(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := awaiting(t, s, router)
	do(router, "POST", "/payments/"+p.PaymentID+"/reject", `{"reviewerId":"a"}`)
	rec := do(router, "POST", "/payments/"+p.PaymentID+"/execute", `{"amount":5800,"currency":"EUR"}`)
	if rec.Code != http.StatusConflict {
		t.Fatalf("want 409, got %d", rec.Code)
	}
	got, _ := s.get(p.PaymentID)
	if got.Status != StatusRejected {
		t.Fatalf("a rejected payment changed state: %+v", got)
	}
}

func TestExecuteReleasesAPaymentAwaitingApproval(t *testing.T) {
	s := newStore()
	router := newRouter(s)
	p := awaiting(t, s, router)
	if rec := do(router, "POST", "/payments/"+p.PaymentID+"/execute", `{"amount":5800,"currency":"EUR"}`); rec.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", rec.Code)
	}
}
```

with helpers (add them if the file lacks equivalents; reuse existing ones if it has them):

```go
func do(router http.Handler, method, path, body string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(method, path, strings.NewReader(body)))
	return rec
}

func awaiting(t *testing.T, s *store, router http.Handler) Payment {
	t.Helper()
	p := s.propose("CLM-1", 5800, "EUR")
	body := `{"rule":"r","limit":0,"requested":5800,"narrative":"n","refusedTraceId":"t","refusedSpanId":"s","a2aTaskId":"task-1"}`
	if rec := do(router, "POST", "/payments/"+p.PaymentID+"/refuse", body); rec.Code != http.StatusOK {
		t.Fatalf("refuse: %d %s", rec.Code, rec.Body)
	}
	return p
}
```

Add `TestTheRejectedRefusalCauseIsDistinguishable` beside
`TestTheTwoRefusalCausesAreDistinguishable` (`:470`): the execute-after-reject
409's span carries `northstar.payment.refusal_cause = "rejected"`, distinct from
the other two.

Run: `cd systems/payments-api && go test ./...` → Expected: FAIL to compile
(`undefined: StatusAwaitingApproval`).

- [ ] **Step 7: Implement the ledger states**

`store.go`:

```go
const (
	StatusProposed         = "proposed"
	StatusAwaitingApproval = "awaiting_approval"
	StatusExecuted         = "executed"
	StatusRejected         = "rejected"
)

var (
	ErrNotFound       = errors.New("payment not found")
	ErrExecuted       = errors.New("payment already executed")
	ErrAlreadyRefused = errors.New("payment already awaiting approval")
	ErrRejected       = errors.New("payment was rejected by a reviewer")
	ErrNotAwaiting    = errors.New("payment is not awaiting approval")
)

type Payment struct {
	PaymentID     string     `json:"paymentId"`
	ClaimID       string     `json:"claimId"`
	Amount        float64    `json:"amount"`
	Currency      string     `json:"currency"`
	Status        string     `json:"status"`
	A2ATaskID     string     `json:"a2aTaskId,omitempty"`
	AwaitingSince string     `json:"awaitingSince,omitempty"`
	Refusal       *refusal   `json:"refusal,omitempty"`
	Rejection     *rejection `json:"rejection,omitempty"`
}

type rejection struct {
	ReviewerID string `json:"reviewerId"`
	RejectedAt string `json:"rejectedAt"`
}
```

`refusal.RefusedAt` is **removed**: `AwaitingSince` is the one clock for "how long
the human took" (the reviewer-ui was its only reader). `store.refuse(paymentID,
r refusal, a2aTaskID, now string)` sets `Status = StatusAwaitingApproval`,
`A2ATaskID`, `AwaitingSince = now`, and returns `ErrRejected` for a rejected payment.
`store.execute` returns `ErrRejected` for `StatusRejected` (before the executed
check). New:

```go
func (s *store) reject(paymentID, reviewerID, now string) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	if payment.Status != StatusAwaitingApproval {
		return Payment{}, ErrNotAwaiting
	}
	payment.Status = StatusRejected
	payment.Rejection = &rejection{ReviewerID: reviewerID, RejectedAt: now}
	s.payments[paymentID] = payment
	return payment, nil
}
```

`handlers.go`: `refuseRequest` gains `A2ATaskID string json:"a2aTaskId"`; the
refuse handler passes `time.Now().UTC().Format(time.RFC3339)` as `now`. Add the
third execute cause `refusalRejected = "rejected"` and its branch in the execute
handler's switch (`refuse(r.Context(), w, refusalRejected, "payment was rejected by a reviewer")`);
update `northstarRefusalCause`'s comment from "two causes" to three. New route:

```go
	handle("POST /payments/{paymentId}/reject", "POST /payments/{paymentId}/reject",
		func(w http.ResponseWriter, r *http.Request) {
			var req struct {
				ReviewerID string `json:"reviewerId"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.ReviewerID == "" {
				writeError(w, http.StatusBadRequest, "reviewerId is required")
				return
			}
			payment, err := s.reject(r.PathValue("paymentId"), req.ReviewerID,
				time.Now().UTC().Format(time.RFC3339))
			switch {
			case errors.Is(err, ErrNotFound):
				writeError(w, http.StatusNotFound, "payment not found")
			case errors.Is(err, ErrNotAwaiting):
				writeError(w, http.StatusConflict, "payment is not awaiting approval")
			case err != nil:
				writeError(w, http.StatusInternalServerError, "unexpected error")
			default:
				writeJSON(w, http.StatusOK, payment)
			}
		})
```

Rewrite `store.go`'s status comment (`:10-19`) for the four states and their
transitions: proposed → executed (agent within its band); proposed →
awaiting_approval (refuse); awaiting_approval → executed (a human, through the
PDS) or → rejected (`/reject`); rejected and executed are terminal. Update
`contracts/openapi/payments-api.yaml` (enum, the new fields, the reject route,
the refuse body's `a2aTaskId`, the `GET /payments?status=awaiting_approval`
example) and `systems/payments-api/docs/index.md`.

Run: `go test ./...` → Expected: PASS. Mutate: delete the `StatusRejected` check
in `store.execute` → `TestExecuteRefusesARejectedPaymentAndSaysWhy` must go RED.

- [ ] **Step 8: payments-mcp carries the task id**

`server.py::refuse_payment` gains `a2a_task_id: str = ""` (last parameter) and
passes it on; `payments_client.py::refuse` gains keyword `a2a_task_id: str` and
sends `"a2aTaskId": a2a_task_id`. Docstrings: `get_payment` is now read by the
reviewer-ui flow's callers, not `execute_approved`. Update
`contracts/mcp/payments-mcp.json` with the new parameter. Tests:

```python
def test_refuse_sends_the_a2a_task_id(...):  # in tests/test_payments_client.py, using the file's MockTransport pattern
    client.refuse("PAY-000001", rule="r", limit=0.0, requested=5800.0, narrative="n",
                  refused_trace_id="t", refused_span_id="s", a2a_task_id="task-1")
    assert sent_json["a2aTaskId"] == "task-1"
```

Run: `cd tools/payments-mcp && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 9: The settlement-agent answers "awaiting approval"**

In `settlement.py`: delete `UnverifiedApproval`, `AMOUNT_EPSILON`,
`_verify_refusal`, `execute_approved`, `_refusal_link` and their imports
(`Link`, `SpanContext`, `TraceFlags`, `otel_context`, `set_claim_id` if unused).
`_validate` returns a fourth value, the optional `a2aTaskId` (a `str`, default `""`).
`_record_refusal(payments, payment_id, refused, *, narrative, a2a_task_id)` passes
`a2a_task_id=` to `payments.refuse`. The refused branch returns:

```python
        return {
            "amount": proposed.get("amount", amount),
            "currency": proposed.get("currency", currency),
            "status": "awaiting_approval",
            "paymentId": proposed.get("paymentId"),
            "narrative": result.output,
            "rule": refused.rule,
            "limit": refused.limit,
        }
```

and the executed branch adds `"rule": None, "limit": None`. Update the
`decide_settlement` docstring: a policy denial is no longer final for the claim;
it routes the payment to a human. The model's instructions are unchanged.
In `executor.py`: delete `_validate_approval`, `_execute_approved` and the
`"approval" in payload` branch; the reply JSON adds `"rule"` and `"limit"`. Rewrite
the module docstring's last paragraph: this image runs one Deployment only.
`mcp_tools.PaymentsTools.refuse` gains `a2a_task_id: str` and sends it.

Tests: delete `tests/test_execute_approved.py`. In `tests/test_settlement.py`,
the refusal test asserts `status == "awaiting_approval"`, `rule`, `limit`, and
that `payments.refuse` received `a2a_task_id="task-1"` when the claim carried
`"a2aTaskId": "task-1"`. In `tests/test_executor_reply.py`, delete the approval
cases and assert the reply carries `rule` and `limit`.

Run: `cd agents/settlement-agent && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 10: Write the failing reviewer-ui gateway-client tests**

`apps/reviewer-ui/tests/test_gateway.py`:

```python
import json

import httpx
import pytest

import gateway


def _sse(body: dict) -> str:
    return f"event: message\ndata: {json.dumps(body)}\n\n"


def _transport(tool_result: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append((payload.get("method"), dict(request.headers)))
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1", "content-type": "text/event-stream"},
                                  text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}))
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": tool_result}))
    return httpx.MockTransport(handler)


@pytest.fixture
def svid(monkeypatch):
    async def fake(audience):
        assert audience == "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"
        return "header.payload.sig"
    monkeypatch.setattr(gateway, "fetch_jwt_svid", fake)


async def test_execute_presents_the_svid_and_returns_the_ledger_record(svid):
    seen = []
    client = gateway.GatewayClient("http://gw/mcp", transport=_transport(
        {"isError": False, "structuredContent": {"result": {"paymentId": "PAY-000001", "status": "executed"}}}, seen))
    record = await client.execute_payment("PAY-000001", 5800.0, "EUR")
    assert record == {"paymentId": "PAY-000001", "status": "executed"}
    assert [m for m, _ in seen] == ["initialize", "notifications/initialized", "tools/call"]
    assert all(h["authorization"] == "Bearer header.payload.sig" for _, h in seen)
    assert seen[2][1]["mcp-session-id"] == "s1"


async def test_a_policy_denial_is_execution_refused_with_its_text(svid):
    text = "the ledger shows 'executed' [policy-denial rule=human-approved.execute limit=none requested=5800.00]"
    client = gateway.GatewayClient("http://gw/mcp", transport=_transport(
        {"isError": True, "content": [{"type": "text", "text": text}]}, []))
    with pytest.raises(gateway.ExecutionRefused) as raised:
        await client.execute_payment("PAY-000001", 5800.0, "EUR")
    assert str(raised.value) == text


async def test_an_http_failure_is_gateway_unavailable(svid):
    client = gateway.GatewayClient("http://gw/mcp", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="authentication failure")))
    with pytest.raises(gateway.GatewayUnavailable):
        await client.execute_payment("PAY-000001", 5800.0, "EUR")
```

Run: `cd apps/reviewer-ui && .venv/bin/pytest tests/test_gateway.py -q`
Expected: FAIL (`No module named 'gateway'`).

- [ ] **Step 11: Implement the reviewer-ui's JWT-SVID and gateway client**

`identity.py` gains a JWT-SVID fetch, the same shape as
`agents/settlement-agent/svid.py` (cache until 30 s before expiry; one seam for
tests):

```python
_jwt_cache: dict[str, tuple[str, float]] = {}
_REFRESH_MARGIN_SECONDS = 30


async def _fetch_jwt_from_workload_api(audience: str) -> tuple[str, float]:
    socket_path = os.getenv("SPIFFE_ENDPOINT_SOCKET", _DEFAULT_ENDPOINT_SOCKET)

    def _fetch() -> tuple[str, float]:
        client = WorkloadApiClient(socket_path=socket_path)
        try:
            svid = client.fetch_jwt_svid(audience={audience})
            return svid.token, float(svid.expiry)
        finally:
            client.close()

    return await asyncio.to_thread(_fetch)


async def fetch_jwt_svid(audience: str) -> str:
    cached = _jwt_cache.get(audience)
    if cached is not None and cached[1] - time.time() > _REFRESH_MARGIN_SECONDS:
        return cached[0]
    try:
        token, expiry = await _fetch_jwt_from_workload_api(audience)
    except Exception as exc:  # noqa: BLE001 -- see IdentityUnavailable
        raise IdentityUnavailable(f"could not fetch a JWT-SVID for {audience!r}: {exc}") from exc
    _jwt_cache[audience] = (token, expiry)
    return token
```

(add `import time`). Rewrite the module docstring's first paragraph: the
reviewer-ui now presents a JWT-SVID to agentgateway to release approved payments.
Add `tests/test_identity.py` cases mirroring `agents/settlement-agent/tests/test_svid.py`'s
cache and failure tests (seam replaced, no network).

`gateway.py`:

```python
"""The reviewer-ui's own call to execute_payment, through agentgateway.

The same governed path the settlement-agent uses: a JWT-SVID in the
Authorization header (identity.fetch_jwt_svid), jwtAuth at the gateway, the
PDS's decision on the tools/call. The PDS permits this caller only under
`human-approved.execute`, and only when it reads the ledger status
`awaiting_approval` itself.

Raw streamable-HTTP JSON-RPC over httpx (initialize, notifications/initialized,
tools/call), the technique tests/harness/live.py::call_tool_directly already
uses, rather than a new dependency on the `mcp` SDK. agentgateway answers as
`text/event-stream`, so the body is read from its `data:` line.

`traceparent` is injected by HTTPXClientInstrumentor (bootstrap.py) and also
put into `params._meta` (SEP-414), exactly as the settlement-agent does, so the
gateway, the PDS and payments-mcp join review.decision's trace.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from opentelemetry import propagate

from identity import fetch_jwt_svid

GATEWAY_AUDIENCE = os.getenv(
    "NORTHSTAR_GATEWAY_AUDIENCE",
    "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway",
)
MCP_PROTOCOL_VERSION = "2025-06-18"


class ExecutionRefused(RuntimeError):
    """The platform refused the release (an isError tool result). The text is
    shown to the reviewer as it came back."""


class GatewayUnavailable(RuntimeError):
    """The gateway did not answer with an MCP result at all."""


def _message(response: httpx.Response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    return response.json()


class GatewayClient:
    def __init__(self, mcp_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self._url = mcp_url
        self._transport = transport
        self._timeout = timeout

    async def execute_payment(self, payment_id: str, amount: float, currency: str) -> dict[str, Any]:
        token = await fetch_jwt_svid(GATEWAY_AUDIENCE)
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                init = await client.post(self._url, headers=headers, json={
                    "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "initialize",
                    "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                               "clientInfo": {"name": "reviewer-ui", "version": "1"}}})
                init.raise_for_status()
                session = {**headers, "mcp-session-id": init.headers["mcp-session-id"]}
                await client.post(self._url, headers=session, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized"})
                meta: dict[str, str] = {}
                propagate.inject(meta)
                called = await client.post(self._url, headers=session, json={
                    "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
                    "params": {"name": "execute_payment", "_meta": meta,
                               "arguments": {"payment_id": payment_id, "amount": amount,
                                             "currency": currency}}})
                called.raise_for_status()
                body = _message(called)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GatewayUnavailable(f"agentgateway did not complete execute_payment: {exc}") from exc

        if "error" in body:
            raise GatewayUnavailable(f"agentgateway returned a JSON-RPC error: {body['error']}")
        result = body.get("result") or {}
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content") or [])
            raise ExecutionRefused(text)
        structured = result.get("structuredContent") or {}
        return structured.get("result", structured)
```

Run: `.venv/bin/pytest tests/test_gateway.py tests/test_identity.py -q` → Expected: all pass.

- [ ] **Step 12: Rewrite `review.decide` for the human release and the reject**

`levels.py`:

```python
"""The autonomy level a refusal happened at, read off the PDS rule name.

The rule shape `settlement-agent.<level>.execute_limit` is composed in exactly
one place (platform/policy-decision-service/pds/rules.py::decide). Deriving the
level from it means the ledger needs no second copy of the same fact.
"""
from __future__ import annotations

import re

_RULE = re.compile(r"settlement-agent\.(?P<level>[a-z0-9_-]+)\.execute_limit")


def autonomy_level_of(rule: str | None) -> str:
    match = _RULE.fullmatch(rule or "")
    return match.group("level") if match else "unknown"
```

`tests/test_levels.py`:

```python
from levels import autonomy_level_of


def test_reads_the_level_out_of_the_rule():
    assert autonomy_level_of("settlement-agent.assisted.execute_limit") == "assisted"
    assert autonomy_level_of("settlement-agent.autonomous.execute_limit") == "autonomous"


def test_anything_else_is_unknown_not_a_guess():
    for rule in (None, "", "human-approved.execute", "settlement-agent.assisted.execute_limit.x"):
        assert autonomy_level_of(rule) == "unknown"
```

`ledger.py`: rename `list_refused` to `list_awaiting` (query
`status=awaiting_approval`); add:

```python
    async def reject(self, payment_id: str, reviewer_id: str) -> dict[str, Any]:
        """POST /payments/{id}/reject. 409 (not awaiting) is PaymentNotAwaiting."""
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            try:
                response = await client.post(f"{self._base_url}/payments/{payment_id}/reject",
                                             json={"reviewerId": reviewer_id})
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(f"could not reach payments-api to reject {payment_id!r}: {exc}") from exc
        if response.status_code == 404:
            raise PaymentNotFound(f"payment {payment_id!r} not found")
        if response.status_code == 409:
            raise PaymentNotAwaiting(f"payment {payment_id!r} is not awaiting approval")
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LedgerUnavailable(f"payments-api rejected {payment_id!r} oddly: {exc}") from exc
```

with `class PaymentNotAwaiting(RuntimeError)` beside `PaymentNotFound`. Rewrite
the module docstring: reviewer-ui reads and rejects on payments-api directly
(moving no money); it **releases** money only through `gateway.py`.

`review.py`: `ApproverLike` becomes `GatewayLike` with
`execute_payment(payment_id, amount, currency)`; `LedgerLike` gains `reject`.
`PaymentNotAwaitingReview`'s check is `record.get("status") != "awaiting_approval"`.
`ReviewResult` replaces `approved_execution`/`approval_error` with
`executed: dict | None`, `rejected: dict | None`, `error: str | None`, and adds
`autonomy_level: str`. `_elapsed_ms` reads `record["awaitingSince"]`. The body:

```python
        record = await ledger.get(payment_id)
        if record.get("status") != AWAITING_APPROVAL:
            raise PaymentNotAwaitingReview(
                f"payment {payment_id!r}: ledger status is {record.get('status')!r}, "
                "not 'awaiting_approval' -- nothing to review")
        refusal = record.get("refusal") or {}
        elapsed_ms = _elapsed_ms(record.get("awaitingSince", ""), now or datetime.now(timezone.utc))
        span.set_attribute("review.refused.trace_id", refusal.get("refusedTraceId") or "")
        span.set_attribute("review.elapsed_ms", elapsed_ms)
        result = ReviewResult(payment_id=payment_id, decision=decision, reviewer_id=reviewer_id,
                              reviewer_spiffe_id=reviewer_spiffe_id, elapsed_ms=elapsed_ms,
                              autonomy_level=autonomy_level_of(refusal.get("rule")))
        try:
            if decision == "approve":
                result.executed = await gateway.execute_payment(
                    payment_id, record["amount"], record["currency"])
            else:
                result.rejected = await ledger.reject(payment_id, reviewer_id)
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            result.error = str(exc)
        return result
```

Rewrite `tests/test_review.py`'s fixtures: `AWAITING_PAYMENT` (status
`awaiting_approval`, `awaitingSince`, `a2aTaskId`, refusal without `refusedAt`),
a `FakeGateway` that records calls and can raise `ExecutionRefused`, a
`FakeLedger` with `reject`. Required cases: approve calls `execute_payment`
with the **ledger's** amount and currency (never a form field); reject calls
`ledger.reject` and never the gateway; a refused release sets `error`, leaves
`executed` None and marks the span ERROR; a non-awaiting record raises
`PaymentNotAwaitingReview` inside the span; `elapsed_ms` is measured from
`awaitingSince`; `autonomy_level` is read off the refusal's rule.

`main.py`: build `gateway_client = gateway.GatewayClient(os.environ.get(
"AGENTGATEWAY_MCP_URL", "http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp"))`
and pass it as `gateway=`; delete `approval_client`. Map exceptions as today
plus `ledger.PaymentNotAwaiting` → 409. Add the JSON routes the demo driver
and e2e use:

```python
@app.get("/api/awaiting")
async def api_awaiting():
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("review.queue", kind=SpanKind.SERVER):
        return await ledger_client.list_awaiting()


class ReviewRequest(BaseModel):
    reviewerId: str
    decision: str


@app.post("/api/review/{payment_id}")
async def api_review(payment_id: str, body: ReviewRequest):
    reviewer_spiffe_id = await identity.workload_spiffe_id()
    result = await review.decide(payment_id, body.reviewerId, body.decision,
                                 ledger=ledger_client, gateway=gateway_client,
                                 reviewer_spiffe_id=reviewer_spiffe_id)
    return {"paymentId": result.payment_id, "decision": result.decision,
            "reviewerId": result.reviewer_id, "elapsedMs": result.elapsed_ms,
            "autonomyLevel": result.autonomy_level, "executed": result.executed,
            "rejected": result.rejected, "error": result.error}
```

with the same exception-to-status mapping as the form route (a shared helper
`_review_or_status(...)`; 400/404/409/502 as JSON `{"error": ...}`).
`render.py`: the card shows `awaiting approval`, the rule, the limit, the
narrative and "waiting since <awaitingSince>"; the result page says "Paid by
the platform after your approval" / "Rejected" / the refusal text. Keep the
literal `Review recorded` on the result page (e2e tests wait on it).

Run: `cd apps/reviewer-ui && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 13: Remove the senior agent everywhere, and admit the reviewer-ui**

Derive the list; do not trust this plan's copy:

```bash
git grep -l -i "senior" -- ':!docs/superpowers' ':!talk/reports' ':!docs/upstream-findings.md'
```

For each hit: delete code, manifests and tests that exist for the senior agent;
rewrite prose that describes the old approval path (README, `docs/architecture.md`,
`docs/telemetry-contract.md`'s `settlement.execute_approved` section becomes a
dated "retired in M8" note, `docs/demo-runbook.md`'s Beat 4). Historical specs
and plans are **annotated, not rewritten**. `talk/` is Task 5's. Specifically:

- `deploy/platform/spire/registration.yaml`: add an explicit `ClusterSPIFFEID`
  `northstar-reviewer-ui` (same template as `northstar-settlement-agent`,
  `podSelector app: reviewer-ui`, `namespace northstar-agents`, `fallback: false`),
  with a comment: this identity now releases money, so it is named in-tree
  rather than left to the chart's fallback. Update the `:141` paragraph.
  `tests/unit/test_stage2_spire_config.py` replaces `SENIOR_SETTLEMENT_SPIFFE_ID`
  with the reviewer-ui's ID in its allow-list and asserts the new CR.
- `deploy/platform/cilium-policies.yaml`: delete
  `senior-settlement-agent-egress-allowlist` and
  `senior-settlement-agent-ingress-allowlist`; in `reviewer-ui-egress-allowlist`
  replace the senior rule with `northstar-platform` / `app: agentgateway` /
  port `3000`. Rewrite the reviewer-ui comments: payments-api (read, reject) and
  the gateway (release).
- `scripts/setup-kind.sh` and `scripts/rebuild-and-deploy.sh`: remove the senior
  apply, restart and rollout lines and the `settlement-agent:senior-settlement-agent`
  shared-image entry; `tests/unit/test_rebuild_and_deploy_guard.py` follows.
- `platform/eval-scorer/scorer/authority.py`: `PUBLISHED_EXECUTE_LIMITS` becomes
  the two settlement rows (`0.0`, `6000.0`); add
  `REVIEWER_UI = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/reviewer-ui"` and
  `PUBLISHED_HUMAN_APPROVERS = frozenset({REVIEWER_UI})`.
  `tests/test_authority_matches_the_policy.py` adds the same AST read for
  `HUMAN_APPROVERS` (a `frozenset({...})` call whose single argument is a set
  literal of strings) and asserts equality. `tests/fixtures.py`: senior → reviewer-ui.
- Backstage `router.ts`: remove `'senior-settlement-agent'` from
  `TARGETED_IDENTITIES`; its tests and the card follow. Run
  `cd platform/backstage && yarn test --watchAll=false` for both packages touched.
- `tests/e2e/test_keystone.py:417-426,566-580`: retarget the impersonation checks
  from `senior-settlement-agent` to `reviewer-ui` (an agent that could run a pod
  as, or mint a token for, the reviewer-ui could forge a human approval; spec §3.5).
- `tests/e2e/test_egress_policy.py:778`, `tests/e2e/test_prometheus_metrics_ingest.py:432-436`: drop the senior from the prose.

Then `git grep -n -i senior -- ':!docs/superpowers' ':!talk' ':!docs/upstream-findings.md'`
returns only dated history notes.

- [ ] **Step 14: Move every e2e test that needs a self-settled payment to `autonomous`**

At the resting state every positive amount now waits. Add to
`tests/harness/live.py`:

```python
@contextlib.contextmanager
def autonomy(level: str):
    """The flag at `level` for the duration, always restored to assisted.

    M8's resting state releases nothing; a test that needs the agent to settle
    a payment itself runs inside `with live.autonomy(contract.AUTONOMOUS):`.
    """
    from tests.harness import contract
    contract.set_targeted_branch(level)
    try:
        yield
    finally:
        contract.set_targeted_branch(contract.ASSISTED)


REVIEWER_UI_URL = os.environ.get("REVIEWER_UI_URL", "http://localhost:8081")


def review(payment_id: str, decision: str, reviewer_id: str) -> dict:
    """A reviewer's decision through reviewer-ui's own JSON route."""
    response = httpx.post(f"{REVIEWER_UI_URL}/api/review/{payment_id}",
                          json={"reviewerId": reviewer_id, "decision": decision}, timeout=60)
    response.raise_for_status()
    return response.json()
```

Derive the affected modules and change each one deliberately:

```bash
git grep -n -E '"executed"|== .executed.|PERMITTED_AMOUNT|REQUESTED_AMOUNT = 5800' -- tests/e2e
```

Each test that submits a claim and expects the agent to execute it wraps that
submission (module fixture) in `with live.autonomy(contract.AUTONOMOUS):` at an
amount ≤ 6,000 (keep `5800`). Each test that expects a refusal asserts
`status == "awaiting_approval"`, and any literal `"exceeds the 6000 EUR limit"`
at `assisted` becomes `"exceeds the 0 EUR limit"` (derive the limit from
`rules.py` via the decision span's `policy.limit.amount`, not a retyped number,
where the test already reads the span). `test_golden_path.py` and
`test_progressive_autonomy.py` change `AMOUNT` from `6200.0` to `5800.0`: the
story "the same claim waits at assisted and settles at autonomous" now holds
at €5,800. `test_demo_flow.py` is rewritten in Task 5; until then, change only
its amounts so it stays green.

- [ ] **Step 15: Write the live maker-checker suite (first half)**

Delete `tests/e2e/test_supervised.py`. Create `tests/e2e/test_human_in_the_loop.py`
(gated like every live module; fail, never skip, when the cluster is absent):

```python
"""M8: the agent prepares, a human releases. Live, stage 7.

Task 1 covers the ledger and the release; Task 2 adds the A2A task states and
the stream; Task 3 adds trace B and the histogram.
"""
import uuid

import pytest

from tests.harness import contract, live

REVIEWER = "alex.mercer"
REVIEWER_UI_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


def _claim(amount: float) -> dict:
    claim_id = f"CLM-HITL-{uuid.uuid4().hex[:8]}"
    contract.assert_assisted("before a maker-checker claim")
    with live.claims_agent_route() as url:
        run = live.submit_claim(claim_id, amount, claims_agent_url=url)
    return {"claim_id": claim_id, **run}


@pytest.fixture(scope="module")
def approved():
    run = _claim(5800.0)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    return {**run, "payment_id": payment_id,
            "review": live.review(payment_id, "approve", REVIEWER)}


@pytest.fixture(scope="module")
def rejected():
    run = _claim(5800.0)
    payment_id = run["reply"]["paymentId"]
    return {**run, "payment_id": payment_id,
            "review": live.review(payment_id, "reject", REVIEWER)}


def test_assisted_5800_waits_with_rule_and_a_zero_limit(approved):
    reply = approved["reply"]
    assert reply["rule"] == "settlement-agent.assisted.execute_limit"
    assert reply["limit"] == 0.0


def test_approve_releases_the_payment_through_the_pds(approved):
    assert approved["review"]["error"] is None, approved["review"]
    assert approved["review"]["executed"]["status"] == "executed"


def test_reject_records_the_reviewer_and_moves_no_money(rejected):
    assert rejected["review"]["error"] is None, rejected["review"]
    assert rejected["review"]["rejected"]["status"] == "rejected"
    assert rejected["review"]["rejected"]["rejection"]["reviewerId"] == REVIEWER


def _execute_as_reviewer_ui(payment_id: str):
    """execute_payment straight at the gateway, under reviewer-ui's own SVID
    (minted in the reviewer-ui pod, the way test_keystone mints tokens)."""
    with live.gateway_port_forward() as gateway_url:
        token = live.mint_svid(live.GATEWAY_AUDIENCE, subject=REVIEWER_UI_SPIFFE_ID)
        return live.call_tool_directly(
            gateway_url, {"authorization": f"Bearer {token}"}, "execute_payment",
            {"payment_id": payment_id, "amount": 5800.0, "currency": "EUR"})


def test_execute_after_reject_is_refused(rejected):
    # The reviewer-ui itself: the ledger no longer shows awaiting_approval -> 409.
    with pytest.raises(httpx.HTTPStatusError) as raised:
        live.review(rejected["payment_id"], "approve", REVIEWER)
    assert raised.value.response.status_code == 409
    # The platform: even a direct call under reviewer-ui's identity is denied.
    outcome = _execute_as_reviewer_ui(rejected["payment_id"])
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "human-approved.execute"


def test_the_release_was_decided_under_the_human_rule(approved):
    # The payment is already executed, so the human rule must now deny it.
    outcome = _execute_as_reviewer_ui(approved["payment_id"])
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "human-approved.execute"
```

Add `import httpx` at the top. `live.mint_svid` and `live.call_tool_directly`
exist today. If `mint_svid` does not take a `subject` (it mints in a given pod),
pass the reviewer-ui pod in the way `tests/e2e/test_keystone.py` does. Change the
call, not the assertions.

- [ ] **Step 16: Roll out and verify Task 1**

```bash
./scripts/setup-kind.sh --stage 7
kubectl --context kind-northstar -n northstar-agents get deploy senior-settlement-agent   # expect: NotFound
POD=$(kubectl --context kind-northstar -n northstar-platform get pod -l app=policy-decision-service -o name | head -1)
kubectl --context kind-northstar -n northstar-platform exec "$POD" -c policy-decision-service -- \
  python -c "from pds.ledger import payment_status; print(payment_status('PAY-000001'))"
# expect: a status string or None for an unknown id -- and NOT a timeout (that would be a Cilium drop)
.venv/bin/pytest tests/unit -q
for d in platform/policy-decision-service agents/settlement-agent apps/reviewer-ui tools/payments-mcp platform/eval-scorer; do (cd $d && .venv/bin/pytest -q) || break; done
(cd systems/payments-api && go test ./...)
RUN_BROWSER_TESTS=1 .venv/bin/pytest -q tests/e2e/test_human_in_the_loop.py tests/e2e/test_keystone.py \
  tests/e2e/test_progressive_autonomy.py tests/e2e/test_policy_denial.py tests/e2e/test_egress_policy.py
./scripts/demo reset
```

Expected: all pass; the PDS probe returns without a timeout. Mutation: comment
out `ledger_status = payment_status(call.payment_id)` in `server.py`, rebuild
only that image (`scripts/rebuild-and-deploy.sh`), and
`test_approve_releases_the_payment_through_the_pds` must go RED (human denied on
`None`). Restore and rebuild.

- [ ] **Step 17: Docs, counts, commit**

`docs/architecture.md`: the policy table (spec §3.1), the ledger state machine
(spec §3.2), "the reviewer-ui is the only path for a human approval". Add
`policy.ledger.status` to the telemetry contract's `policy.decision` attribute
table and its owned-attribute inventory (`tests/unit/test_telemetry_contract_attribute_counts.py`
reports what to fix). Refresh the counts block. Then:

```bash
git add -A platform/policy-decision-service systems/payments-api tools/payments-mcp contracts \
  agents/settlement-agent apps/reviewer-ui deploy scripts/setup-kind.sh scripts/rebuild-and-deploy.sh \
  platform/eval-scorer platform/backstage/packages tests README.md docs catalog-info.yaml
git status --short     # read it: nothing unrelated staged
git commit -m "feat(m8): the dial means maker-checker; reviewer-ui releases approved payments through the PDS"
```

(`git add -A <paths>` stages the deletions inside those paths; it never stages
outside them.)

---

## Task 2: The waiting claim — A2A's own task lifecycle

**Task review: yes (opus).** Everything below was read from a2a-sdk 0.3.7's
installed source (`agents/claims-agent/.venv/lib/python3.13/site-packages/a2a/`)
and is **measured in Step 2 before it is relied on**.

What the source says, and why the design is shaped this way:

1. `DefaultRequestHandler.on_resubscribe_to_task` (`server/request_handlers/default_request_handler.py:510-545`)
   needs the task's **event queue to still exist** (`self._queue_manager.tap(task.id)`,
   else `TaskNotFoundError`). The queue is created when the first `message/stream`
   starts and is closed and removed by `_cleanup_producer` (`:445-454`) when the
   agent's `execute()` returns. So **the first `execute()` must stay running
   while the task is `input-required`**: it parks on an in-process future. That
   is why a claims-agent restart loses waiting claims (spec §3.2 already accepts it).
2. `EventConsumer.consume_all` (`server/events/event_consumer.py:105-130`) ends a
   stream on a `TaskStatusUpdateEvent` with `final=True`, and then closes **the
   root queue with `immediate=True`**, which clears every tapped child queue
   (`server/events/event_queue.py` `close`). A resubscriber's child queue can lose
   the `completed` event that way. So the completion is published with
   **`final=False`**, and the stream ends when the parked `execute()` returns and
   `_run_event_stream` closes the root queue **gracefully** (drained first).
   Clients end on the task **state** (`completed`), not on the `final` flag.
3. The input-required update is also `final=False`, or the initial
   `message/stream` consumer would close the root queue the same way.
4. A blocking `message/send` (`consume_and_break_on_interrupt`, `server/tasks/result_aggregator.py:97-167`)
   interrupts only on `auth_required`. A blocking send of a claim would hang for
   the whole wait. **Claims are submitted with `message/stream`.** The resume is
   sent with `configuration.blocking: false`, so its cleanup runs in the
   background (an inline `_cleanup_producer` would race the parked producer's
   own cleanup for the same queue and raise `NoTaskQueue` back to the caller).
5. **Trace A cannot end at `input-required` with this SDK.** `DefaultRequestHandler`
   is `@trace_class(kind=SERVER)` (`:60`), which wraps every non-dunder method,
   including `_run_event_stream`. Its span lives as long as the parked
   `execute()`, i.e. until the human decides. Spec §3.4 says trace A ends at
   `input-required`. **STOP at Step 2 and ask Kasper** if Step 2 confirms this:
   either accept that trace A carries the SDK's producer span for the length of
   the wait (documented as measured), or amend the spec. Do not drop the span
   (never-sample rule) and do not fall back to polling.

**How the browser reaches the claims-agent (measured from the tree):** the
customer-ui is a static Next.js export served by nginx (`apps/customer-ui/next.config.mjs`
`output: "export"`, `nginx.conf`). The browser calls the claims-agent directly at
`NEXT_PUBLIC_CLAIMS_AGENT_URL`, default `http://localhost:19001`
(`src/app/page.tsx:8-9`), which is `kubectl port-forward svc/claims-agent 19001:9001`
(`scripts/start-demo.sh:251`). There is no Next server route and no agentgateway
on that path. The hops are: browser → kubectl port-forward → kubelet stream →
the claims-agent pod's uvicorn → sse-starlette. sse-starlette 3.4.11 sends a
`: ping` comment every 15 s by default (`DEFAULT_PING_INTERVAL = 15`), which is
the keep-alive spec §3.4 names.

**Choice for the claims-agent's ledger check:** a plain REST read of
`GET /payments/{id}` from the claims-agent (a new `ledger.py`, the reviewer-ui's
technique), not an MCP `get_payment` through the gateway. It moves no money and
needs no SVID; the gateway path would add an identity, an MCP client and a PDS
decision to a read. It needs one Cilium egress rule (claims-agent → payments-api:8080).
The resume's own content is never trusted: the outcome is whatever the ledger says.

**Files:**
- Modify: `agents/claims-agent/executor.py`, `main.py`, `tests/test_main.py`, `docs/index.md`, `NOTES.md`
- Create: `agents/claims-agent/ledger.py`, `agents/claims-agent/tests/test_task_lifecycle.py`
- Modify: `contracts/agent-cards/claims-agent.json` (no change needed: `capabilities.streaming` is already `true`; Step 6 asserts it)
- Modify: `deploy/k8s/claims-agent.yaml` (env `PAYMENTS_API_URL`), `deploy/platform/cilium-policies.yaml` (claims-agent → payments-api:8080; reviewer-ui → claims-agent:9001)
- Create: `apps/reviewer-ui/claims.py`, `apps/reviewer-ui/tests/test_claims.py`
- Modify: `apps/reviewer-ui/review.py`, `main.py`, `render.py`, `tests/test_review.py`, `deploy/k8s/reviewer-ui.yaml` (env `CLAIMS_AGENT_URL`)
- Modify: `apps/customer-ui/src/a2a.ts`, `src/a2a.test.ts`, `src/app/page.tsx`, `src/app/globals.css`
- Create: `apps/customer-ui/src/view.ts`, `src/view.test.ts`
- Modify: `tests/harness/live.py` (`submit_claim` over `message/stream`, `resubscribe`, `get_task`), `tests/e2e/test_human_in_the_loop.py`, `tests/e2e/test_policy_denial.py` (browser text), `scripts/demo.py:448-478` (read the new reply)

**Interfaces:**
- Consumes: Task 1's settlement reply (`status`, `rule`, `limit`, `paymentId`), `a2aTaskId` on the claim payload and on the ledger, `GET /payments/{id}` returning `status`, the reviewer-ui's `review.decide` and `ReviewResult`.
- Produces:
  - claims-agent: `ClaimsAgentExecutor(delegates=None, ledger=None)`; task states `submitted → input-required → completed`, or `submitted → completed`. Every status message is one JSON text part:
    `{"claimId","policyNumber","paymentId","amount","currency","status","narrative","rule","limit","decision"}`
    with `status ∈ {"executed","awaiting_approval","rejected"}` and `decision ∈ {null,"approved","rejected"}`.
  - `agents/claims-agent/ledger.py::LedgerReader(base_url).get(payment_id) -> dict`.
  - reviewer-ui: `claims.ClaimsClient(base_url).resume(task_id: str, payload: dict) -> str` (the task state after the resume was accepted), raising `claims.ResumeFailed`;
    `review.decide(..., claims=...)`; `ReviewResult.resumed_state: str | None`, `ReviewResult.resume_error: str | None`.
  - customer-ui `src/a2a.ts`: `streamClaim(baseUrl, claim, onEvent) -> Promise<TaskSnapshot>`, `resubscribe(baseUrl, taskId, onEvent) -> Promise<TaskSnapshot | null>`,
    `getTask(baseUrl, taskId) -> Promise<TaskSnapshot>`, `awaitOutcome(baseUrl, taskId, onEvent, opts?) -> Promise<TaskSnapshot>`,
    `type TaskSnapshot = { taskId: string; contextId: string; state: string; result: ClaimResult | null }`, `TERMINAL_STATES`.
  - customer-ui `src/view.ts`: `determinationView(result: ClaimResult) -> { tone: string; heading: string }`, `formatElapsed(ms: number) -> string`.
  - harness: `live.submit_claim(...) -> {"trace_id","task_id","context_id","state","reply"}` (returns at the first of `input-required` or a terminal state),
    `live.resubscribe(task_id, *, timeout, drop_after=None) -> {"state","reply","events","ended_without_terminal"}`, `live.get_task(task_id) -> {"state","reply","context_id"}`.

- [ ] **Step 1: Measure a 10-minute stream across the real path. If it fails, STOP and report.**

Before writing any Task 2 code, prove a stream survives 10 minutes on every hop
the browser uses. Run a throwaway SSE endpoint **inside the running claims-agent
pod** (same image, same uvicorn, same sse-starlette, same kubelet and
port-forward path), then hold it from the host:

```bash
mkdir -p .demo/m8-stream-probe
POD=$(kubectl --context kind-northstar -n northstar-agents get pod -l app=claims-agent -o jsonpath='{.items[0].metadata.name}')
kubectl --context kind-northstar -n northstar-agents exec "$POD" -c claims-agent -- python -c '
import asyncio, uvicorn
from starlette.applications import Starlette
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse
async def gen():
    await asyncio.sleep(600)
    yield {"data": "done-after-600s"}
async def probe(request):
    return EventSourceResponse(gen())
uvicorn.run(Starlette(routes=[Route("/", probe, methods=["POST"])]), host="127.0.0.1", port=9099)
' > .demo/m8-stream-probe/server.log 2>&1 &
SERVER=$!
kubectl --context kind-northstar -n northstar-agents port-forward "pod/$POD" 19099:9099 \
  > .demo/m8-stream-probe/forward.log 2>&1 &
FORWARD=$!
sleep 3
/usr/bin/time -p curl -sN --max-time 700 -X POST http://localhost:19099/ \
  > .demo/m8-stream-probe/stream.txt; echo "curl exit $?"
kill $FORWARD $SERVER
grep -c "^: ping" .demo/m8-stream-probe/stream.txt     # expect about 40
grep "done-after-600s" .demo/m8-stream-probe/stream.txt # expect one line
```

Then repeat the hold **from a browser** (the hop curl does not exercise): with
the same probe running, open `http://localhost:8080` (customer-ui) and in its
DevTools console run

```js
const t0 = Date.now(); const r = await fetch("http://localhost:19099/", {method: "POST"});
const rd = r.body.getReader(); let s = ""; for (;;) { const {value, done} = await rd.read(); if (done) break; s += new TextDecoder().decode(value); }
console.log((Date.now() - t0) / 1000, s.includes("done-after-600s"));
```

(the probe sends no CORS headers; if the browser blocks the read for that reason,
add `allow_origins=["http://localhost:8080"]` CORS middleware to the probe app,
which mirrors the real claims-agent's `CORSMiddleware`).

Expected: curl exits 0 after ~600 s with the final event; the browser logs
`~600 true`. Record both in `agents/claims-agent/NOTES.md` (a new dated section:
what was held, for how long, every hop, the ping count). **If either cut the
stream**: try the hop's own timeout first (record which hop and its setting); if
that is not configurable, **stop and report to Kasper**. The spec requires an
amendment before polling is built. Do not build a polling fallback.

- [ ] **Step 2: Measure the SDK's resubscribe and completion behaviour in-process. STOP if it contradicts the design.**

Write `agents/claims-agent/tests/test_task_lifecycle.py` **first** (Step 3) and
run it against a **minimal executor** that only does the parking (no delegate):
the tests are the measurement. Record in `NOTES.md` (a) whether `tasks/resubscribe`
on a parked `input-required` task streams and then receives `completed`; (b)
whether a resubscribe to a task whose `execute()` has returned raises
`TaskNotFoundError` (expected: yes); (c) the span list for one claim from an
`InMemorySpanExporter`, and the duration of `DefaultRequestHandler._run_event_stream`
across a parked wait (expected: as long as the wait). **If (c) is confirmed,
stop and ask Kasper** about trace A (point 5 above) before continuing; record
the ruling in `NOTES.md`.

- [ ] **Step 3: Write the failing task-lifecycle tests**

`agents/claims-agent/tests/test_task_lifecycle.py`, driving the real
`DefaultRequestHandler` with `InMemoryTaskStore` (no HTTP, no cluster):

```python
import asyncio
import json
import uuid

import pytest
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (Message, MessageSendConfiguration, MessageSendParams, Part,
                       Role, Task, TaskIdParams, TaskQueryParams, TaskState,
                       TaskStatusUpdateEvent, TextPart)
from a2a.utils.errors import ServerError

from executor import ClaimsAgentExecutor

CLAIM = {"claimId": "CLM-T1", "policyNumber": "POL-1", "estimatedAmount": 5800}


class FakeDelegate:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    async def send(self, payload):
        self.sent.append(payload)
        return {**self.reply}


class FakeLedger:
    def __init__(self, status):
        self.status = status

    async def get(self, payment_id):
        return {"paymentId": payment_id, "status": self.status, "amount": 5800.0, "currency": "EUR"}


def _awaiting():
    return {"paymentId": "PAY-000001", "amount": 5800.0, "currency": "EUR",
            "status": "awaiting_approval", "narrative": "A colleague will review this.",
            "rule": "settlement-agent.assisted.execute_limit", "limit": 0.0}


def _msg(text, task_id=None, context_id=None):
    return Message(role=Role.user, message_id=uuid.uuid4().hex, task_id=task_id,
                   context_id=context_id, parts=[Part(root=TextPart(text=text))])


def _handler(reply, ledger_status="executed"):
    executor = ClaimsAgentExecutor(delegates={"settlement-agent": FakeDelegate(reply)},
                                   ledger=FakeLedger(ledger_status))
    return DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()), executor


def _state(event):
    if isinstance(event, Task):
        return event.status.state
    if isinstance(event, TaskStatusUpdateEvent):
        return event.status.state
    return None


async def _until_input_required(handler):
    stream = handler.on_message_send_stream(MessageSendParams(message=_msg(json.dumps(CLAIM))))
    seen = []
    async for event in stream:
        seen.append(event)
        if _state(event) == TaskState.input_required:
            break
    await stream.aclose()   # the customer-ui closes its claim stream here
    task = next(e for e in seen if isinstance(e, Task))
    return task, seen


async def _resume(handler, task, decision="approved"):
    return await handler.on_message_send(MessageSendParams(
        message=_msg(json.dumps({"decision": decision, "paymentId": "PAY-000001"}),
                     task_id=task.id, context_id=task.context_id),
        configuration=MessageSendConfiguration(blocking=False)))


async def test_a_claim_within_the_band_completes_without_waiting():
    handler, _ = _handler({**_awaiting(), "status": "executed", "rule": None, "limit": None})
    states = [_state(e) async for e in handler.on_message_send_stream(
        MessageSendParams(message=_msg(json.dumps(CLAIM))))]
    assert states[-1] == TaskState.completed
    assert TaskState.input_required not in states


async def test_a_claim_above_the_band_waits_with_the_rule_and_the_limit():
    handler, _ = _handler(_awaiting())
    _, seen = await _until_input_required(handler)
    update = seen[-1]
    body = json.loads(update.status.message.parts[0].root.text)
    assert body["status"] == "awaiting_approval"
    assert body["rule"] == "settlement-agent.assisted.execute_limit" and body["limit"] == 0.0
    assert update.final is False


async def test_the_task_id_is_passed_down_to_the_settlement_agent():
    handler, executor = _handler(_awaiting())
    task, _ = await _until_input_required(handler)
    assert executor._delegates["settlement-agent"].sent[0]["a2aTaskId"] == task.id


async def test_resubscribe_stays_open_and_delivers_completed_after_the_resume():
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    got = []

    async def listen():
        async for event in handler.on_resubscribe_to_task(TaskIdParams(id=task.id)):
            got.append(event)

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    assert not listener.done()          # open, waiting on the human
    await _resume(handler, task, "approved")
    await asyncio.wait_for(listener, timeout=5)
    completed = [e for e in got if _state(e) == TaskState.completed]
    assert completed, got
    body = json.loads(completed[-1].status.message.parts[0].root.text)
    assert body["status"] == "executed" and body["decision"] == "approved"


async def test_a_rejection_completes_the_task_declined():
    handler, _ = _handler(_awaiting(), ledger_status="rejected")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "rejected")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed
    body = json.loads(stored.status.message.parts[0].root.text)
    assert body["status"] == "rejected" and body["decision"] == "rejected"


async def test_a_resume_the_ledger_does_not_back_changes_nothing():
    handler, _ = _handler(_awaiting(), ledger_status="awaiting_approval")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_resume_for_a_task_not_waiting_here_is_an_error():
    handler, _ = _handler(_awaiting())
    with pytest.raises(ServerError):
        await _resume(handler, Task(id="no-such-task", context_id="c",
                                    status={"state": "input-required"}))


async def test_tasks_get_after_completion_reads_completed():
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed
```

If a constructor detail differs in 0.3.7 (for example `Task(status=...)` needs a
`TaskStatus`), fix the **test's construction** to the installed types; do not
weaken an assertion.

Run: `cd agents/claims-agent && .venv/bin/pytest tests/test_task_lifecycle.py -q`
Expected: FAIL (`TypeError: ClaimsAgentExecutor.__init__() got an unexpected keyword argument 'ledger'`).

- [ ] **Step 4: Implement the claims-agent's ledger read**

`agents/claims-agent/ledger.py`:

```python
"""The claims-agent's one read of the ledger: what happened to this payment?

Used only when a task is resumed. The resume message is never trusted; the
customer is told what payments-api says. Plain REST, like reviewer-ui's
ledger.py: a read moves no money, so it does not need the governed MCP path.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = os.environ.get(
    "PAYMENTS_API_URL", "http://payments-api.northstar-systems.svc.cluster.local:8080")


class LedgerReader:
    def __init__(self, base_url: str = DEFAULT_URL, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    async def get(self, payment_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/payments/{payment_id}")
            response.raise_for_status()
            return response.json()
```

Add `PAYMENTS_API_URL` to `deploy/k8s/claims-agent.yaml`, and to
`claims-agent-egress-allowlist` a rule to `northstar-systems` / `app: payments-api`
/ port `8080`, namespace-pinned like its siblings, with a comment saying why.

- [ ] **Step 5: Implement the executor**

`agents/claims-agent/executor.py`:

```python
"""A2A executor for claims-agent.

Every claim is an A2A task. If the settlement-agent settles it, the task
completes at once. If the platform routes the payment to a human, the task
moves to input-required and this execute() stays running, parked on an
in-process future, until the reviewer-ui resumes the same task id.

Why it must stay running (a2a-sdk 0.3.7, read from source, measured in
NOTES.md): tasks/resubscribe needs the task's event queue, and the SDK removes
that queue when execute() returns.

Why both status updates are final=False: the SDK's EventConsumer closes the root
queue immediately on a final update, which clears every resubscriber's queue
before it reads the event. The stream ends instead when this execute() returns
and the SDK closes the queue gracefully. Clients end on the task state.

The resume is never trusted. The outcome the customer sees is whatever the
ledger says: executed -> Settled, rejected -> Declined by a reviewer, anything
else -> the resume is refused and the task keeps waiting.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart
from a2a.utils import new_task
from opentelemetry import context as otel_context
from opentelemetry import trace

from correlation import set_claim_id
from delegation import ALLOWED_AGENTS, A2ADelegate
from intake import parse_claim
from ledger import LedgerReader

logger = logging.getLogger(__name__)

AWAITING_APPROVAL = "awaiting_approval"
OUTCOMES = {"executed": "approved", "rejected": "rejected"}


@dataclass
class _Waiting:
    reply: dict[str, Any]
    outcome: asyncio.Future
    published: asyncio.Event = field(default_factory=asyncio.Event)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaimsAgentExecutor(AgentExecutor):
    def __init__(self, delegates: dict[str, A2ADelegate] | None = None,
                 ledger: LedgerReader | None = None):
        self._delegates = delegates if delegates is not None else {
            name: A2ADelegate(url, name) for name, url in ALLOWED_AGENTS.items()}
        self._ledger = ledger or LedgerReader()
        self._waiting: dict[str, _Waiting] = {}

    def _delegate(self, name: str) -> A2ADelegate:
        try:
            return self._delegates[name]
        except KeyError:
            raise RuntimeError(
                f"claims-agent is not permitted to delegate to {name!r}; "
                f"permitted: {sorted(self._delegates)}") from None

    async def execute(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        task = request_context.current_task
        if task is not None and task.status.state == TaskState.input_required:
            await self._resume(task.id)
            return
        await self._claim(request_context, event_queue)

    async def _claim(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        claim = parse_claim(request_context.get_user_input())
        task = new_task(request_context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        span = trace.get_current_span()
        span.set_attribute("northstar.a2a.task.id", task.id)

        token = otel_context.attach(set_claim_id(claim["claimId"]))
        try:
            settlement = await self._delegate("settlement-agent").send({
                "claimId": claim["claimId"], "amount": claim["estimatedAmount"],
                "currency": "EUR", "a2aTaskId": task.id})
        finally:
            otel_context.detach(token)

        reply = {
            "claimId": claim["claimId"], "policyNumber": claim["policyNumber"],
            "paymentId": settlement.get("paymentId"), "amount": settlement.get("amount"),
            "currency": settlement.get("currency"), "status": settlement.get("status"),
            "narrative": settlement.get("narrative"), "rule": settlement.get("rule"),
            "limit": settlement.get("limit"), "decision": None,
        }
        if reply["status"] != AWAITING_APPROVAL:
            span.set_attribute("northstar.a2a.task.state", TaskState.completed.value)
            await updater.complete(updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]))
            return

        waiting = _Waiting(reply=reply, outcome=asyncio.get_running_loop().create_future())
        self._waiting[task.id] = waiting
        try:
            span.set_attribute("northstar.a2a.task.state", TaskState.input_required.value)
            await updater.update_status(
                TaskState.input_required,
                message=updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]),
                final=False)
            outcome, resume_context = await waiting.outcome
            # Publish under the resume's context (trace B), never trace A's.
            token = otel_context.attach(resume_context)
            try:
                await event_queue.enqueue_event(TaskStatusUpdateEvent(
                    task_id=task.id, context_id=task.context_id, final=False,
                    status=TaskStatus(
                        state=TaskState.completed, timestamp=_now(),
                        message=updater.new_agent_message(
                            [Part(root=TextPart(text=json.dumps(outcome)))]))))
            finally:
                otel_context.detach(token)
            waiting.published.set()
        finally:
            self._waiting.pop(task.id, None)

    async def _resume(self, task_id: str) -> None:
        waiting = self._waiting.get(task_id)
        if waiting is None:
            raise RuntimeError(
                f"task {task_id} is not waiting in this claims-agent process "
                "(it may have restarted); nothing was resumed")
        record = await self._ledger.get(waiting.reply["paymentId"])
        decision = OUTCOMES.get(record.get("status"))
        if decision is None:
            raise RuntimeError(
                f"task {task_id}: the ledger shows {record.get('status')!r} for "
                f"{waiting.reply['paymentId']}, so there is no human decision to report")
        trace.get_current_span().set_attribute("northstar.a2a.task.state", TaskState.completed.value)
        outcome = {**waiting.reply, "status": record["status"], "decision": decision}
        waiting.outcome.set_result((outcome, otel_context.get_current()))
        await waiting.published.wait()

    async def cancel(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("claims-agent does not support cancellation")
```

`main.py` passes nothing new (`ClaimsAgentExecutor()` builds its own
`LedgerReader`). Add `"accept"` to `CORSMiddleware.allow_headers` only if Step 1's
browser probe showed it is needed (`Accept` is CORS-safelisted; do not add it on
speculation).

Run: `cd agents/claims-agent && .venv/bin/pytest -q` → Expected: all pass.
Mutate (`./scripts/mutate-guard.sh`): change the completion's `final=False` to
`final=True` → `test_resubscribe_stays_open_and_delivers_completed_after_the_resume`
must go RED (if it stays green, Step 2's reading of the SDK was wrong: record it).
Mutate: replace `decision = OUTCOMES.get(...)` with `decision = "approved"` →
`test_a_resume_the_ledger_does_not_back_changes_nothing` must go RED.

- [ ] **Step 6: Pin the agent card's streaming capability**

Add to `agents/claims-agent/tests/test_main.py`:

```python
def test_the_card_declares_streaming():
    card = json.loads((REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json").read_text())
    assert card["capabilities"]["streaming"] is True
```

(`REPO_ROOT = Path(__file__).resolve().parents[3]`.) It already is `true`; the
test keeps it so.

- [ ] **Step 7: Write the failing reviewer-ui resume tests**

`apps/reviewer-ui/tests/test_claims.py`:

```python
import json

import httpx
import pytest

import claims


def _transport(get_result, send_result, seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body["method"] == "tasks/get":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **get_result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **send_result})
    return httpx.MockTransport(handler)


TASK = {"result": {"id": "task-1", "contextId": "ctx-1", "kind": "task",
                   "status": {"state": "input-required"}}}


async def test_resume_reads_the_context_then_sends_on_the_same_task_without_blocking():
    seen = []
    client = claims.ClaimsClient("http://claims", transport=_transport(TASK, TASK, seen))
    state = await client.resume("task-1", {"decision": "approved", "paymentId": "PAY-000001"})
    assert state == "input-required"
    assert [b["method"] for b in seen] == ["tasks/get", "message/send"]
    message = seen[1]["params"]["message"]
    assert message["taskId"] == "task-1" and message["contextId"] == "ctx-1"
    assert seen[1]["params"]["configuration"]["blocking"] is False


async def test_a_task_that_is_gone_is_resume_failed():
    client = claims.ClaimsClient("http://claims", transport=_transport(
        {"error": {"code": -32001, "message": "Task not found"}}, TASK, []))
    with pytest.raises(claims.ResumeFailed):
        await client.resume("task-1", {})


async def test_a_task_that_is_not_waiting_is_resume_failed():
    done = {"result": {**TASK["result"], "status": {"state": "completed"}}}
    client = claims.ClaimsClient("http://claims", transport=_transport(done, TASK, []))
    with pytest.raises(claims.ResumeFailed):
        await client.resume("task-1", {})
```

Run: `cd apps/reviewer-ui && .venv/bin/pytest tests/test_claims.py -q` → Expected: FAIL (`No module named 'claims'`).

- [ ] **Step 8: Implement `claims.py` and resume after the decision**

```python
"""Resume the customer's waiting task: A2A's way to give an input-required
task its input is a message on the same task id (and context id).

tasks/get first, for two reasons: the context id (the ledger stores only the
task id), and to say plainly when the task is gone (claims-agent restarted).
The message is sent non-blocking (configuration.blocking=false): the
claims-agent completes the task on its own stream, and a blocking send would
race the SDK's cleanup of that task's queue.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


class ResumeFailed(RuntimeError):
    """The customer's task could not be resumed. The money decision stands."""


class ClaimsClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self._url = base_url.rstrip("/") + "/"
        self._transport = transport
        self._timeout = timeout

    async def _call(self, client: httpx.AsyncClient, method: str, params: dict) -> dict:
        response = await client.post(self._url, json={
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params})
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise ResumeFailed(f"claims-agent {method}: {body['error'].get('message')}")
        return body["result"]

    async def resume(self, task_id: str, payload: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                task = await self._call(client, "tasks/get", {"id": task_id})
                if task["status"]["state"] != "input-required":
                    raise ResumeFailed(f"task {task_id} is {task['status']['state']!r}, not waiting")
                accepted = await self._call(client, "message/send", {
                    "message": {"role": "user", "messageId": uuid.uuid4().hex,
                                "taskId": task_id, "contextId": task["contextId"],
                                "parts": [{"kind": "text", "text": json.dumps(payload)}]},
                    "configuration": {"blocking": False}})
        except httpx.HTTPError as exc:
            raise ResumeFailed(f"could not reach claims-agent to resume {task_id}: {exc}") from exc
        return (accepted.get("status") or {}).get("state", "unknown")
```

In `review.decide`, add keyword `claims: ClaimsLike` and, after a **successful**
execute or reject (never after a failed one), resume:

```python
        if result.error is None and record.get("a2aTaskId"):
            try:
                result.resumed_state = await claims.resume(record["a2aTaskId"], {
                    "decision": "approved" if decision == "approve" else "rejected",
                    "paymentId": payment_id, "reviewerId": reviewer_id})
            except Exception as exc:  # noqa: BLE001 -- the money decision stands; say so
                result.resume_error = str(exc)
```

`main.py` builds `claims_client = claims.ClaimsClient(os.environ.get(
"CLAIMS_AGENT_URL", "http://claims-agent.northstar-agents.svc.cluster.local:9001"))`
and passes it; the JSON route adds `"resumedState"` and `"resumeError"`;
`render.py`'s result page says "The customer has been told" or "The customer could
not be told: <resume_error>". Add `CLAIMS_AGENT_URL` to `deploy/k8s/reviewer-ui.yaml`
and, to `reviewer-ui-egress-allowlist`, `northstar-agents` / `app: claims-agent` /
port `9001`. Extend `tests/test_review.py`: a `FakeClaims` records resumes;
approve and reject each resume once with the ledger's `a2aTaskId`; a failed
execute does not resume; a `ResumeFailed` sets `resume_error` and leaves
`executed` set.

Run: `cd apps/reviewer-ui && .venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 9: Write the failing customer-ui tests**

`apps/customer-ui/src/view.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { determinationView, formatElapsed } from "./view";

describe("determinationView", () => {
  it("says awaiting human approval while the task waits", () => {
    expect(determinationView({ claimId: "C", status: "awaiting_approval" })).toEqual({
      tone: "is-waiting",
      heading: "Awaiting human approval",
    });
  });
  it("says settled when executed", () => {
    expect(determinationView({ claimId: "C", status: "executed" }).heading).toBe("Settled");
  });
  it("says declined by a reviewer when rejected", () => {
    expect(determinationView({ claimId: "C", status: "rejected" }).heading).toBe(
      "Declined by a reviewer",
    );
  });
});

describe("formatElapsed", () => {
  it("is minutes and seconds", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65_000)).toBe("1:05");
    expect(formatElapsed(600_000)).toBe("10:00");
  });
});
```

Add to `apps/customer-ui/src/a2a.test.ts` (keep the existing traceparent test,
retargeted at `streamClaim`):

```ts
function sseResponse(frames: object[], { ping = false } = {}): Response {
  const text =
    (ping ? ": ping\n\n" : "") +
    frames.map((f) => `data: ${JSON.stringify({ jsonrpc: "2.0", id: "1", result: f })}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const task = (state: string, result?: object) => ({
  kind: "task",
  id: "t1",
  contextId: "c1",
  status: { state, message: result ? { parts: [{ kind: "text", text: JSON.stringify(result) }] } : undefined },
});
const update = (state: string, result: object) => ({
  kind: "status-update",
  taskId: "t1",
  contextId: "c1",
  final: false,
  status: { state, message: { parts: [{ kind: "text", text: JSON.stringify(result) }] } },
});

describe("streamClaim", () => {
  it("sends message/stream and returns at input-required with the reply", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([task("submitted"), update("input-required", { claimId: "C", status: "awaiting_approval" })], { ping: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const snap = await streamClaim("http://agent", { claimId: "C", policyNumber: "P", description: "", estimatedAmount: 5800 }, () => {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).method).toBe("message/stream");
    expect(snap).toMatchObject({ taskId: "t1", contextId: "c1", state: "input-required" });
    expect(snap.result?.status).toBe("awaiting_approval");
  });
});

describe("awaitOutcome", () => {
  it("ends when the resubscribe stream delivers a terminal state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([update("completed", { claimId: "C", status: "executed" })])));
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    expect(snap.state).toBe("completed");
    expect(snap.result?.status).toBe("executed");
  });

  it("reads the task once after a drop and resubscribes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([])) // the stream ends with no terminal state: a drop
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("input-required") })))
      .mockResolvedValueOnce(sseResponse([update("completed", { claimId: "C", status: "rejected" })]));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    const methods = fetchMock.mock.calls.map((c) => JSON.parse(c[1].body).method);
    expect(methods).toEqual(["tasks/resubscribe", "tasks/get", "tasks/resubscribe"]);
    expect(snap.result?.status).toBe("rejected");
  });

  it("returns at once when the read after a drop is already terminal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("completed", { claimId: "C", status: "executed" }) })));
    vi.stubGlobal("fetch", fetchMock);
    const snap = await awaitOutcome("http://agent", "t1", () => {});
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(snap.state).toBe("completed");
  });

  it("gives up loudly after repeated drops with nothing in between", async () => {
    const fetchMock = vi.fn().mockImplementation((_u: string, init: RequestInit) =>
      Promise.resolve(
        JSON.parse(String(init.body)).method === "tasks/get"
          ? new Response(JSON.stringify({ jsonrpc: "2.0", id: "1", result: task("input-required") }))
          : sseResponse([]),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(awaitOutcome("http://agent", "t1", () => {}, { maxEmptyDrops: 3 })).rejects.toThrow(/lost the claim's stream/);
  });

  it("surfaces a JSON-RPC error from resubscribe instead of retrying", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(`data: ${JSON.stringify({ jsonrpc: "2.0", id: "1", error: { code: -32001, message: "Task not found" } })}\n\n`, { status: 200 }),
    ));
    await expect(awaitOutcome("http://agent", "t1", () => {})).rejects.toThrow(/Task not found/);
  });
});
```

Run: `cd apps/customer-ui && npm test` → Expected: FAIL (`streamClaim`/`awaitOutcome`/`./view` not found).

- [ ] **Step 10: Implement the customer-ui client and the waiting view**

`src/view.ts`:

```ts
import type { ClaimResult } from "./a2a";

export function determinationView(result: ClaimResult): { tone: string; heading: string } {
  switch ((result.status ?? "").toLowerCase()) {
    case "awaiting_approval":
      return { tone: "is-waiting", heading: "Awaiting human approval" };
    case "executed":
      return { tone: "is-executed", heading: "Settled" };
    case "rejected":
      return { tone: "is-refused", heading: "Declined by a reviewer" };
    default:
      return { tone: "is-other", heading: "Recorded" };
  }
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
```

`src/a2a.ts` (keep `Claim`, `buildSendMessageRequest` renamed
`buildStreamRequest` with `method: "message/stream"`, and the `injectContext`
discipline: headers are built **before** the first `await`):

```ts
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

export type TaskSnapshot = { taskId: string; contextId: string; state: string; result: ClaimResult | null };
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
      buffer += decoder.decode(value, { stream: true });
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
  const response = await post(baseUrl, { jsonrpc: "2.0", id: newId(), method: "tasks/resubscribe", params: { id: taskId } }, headers);
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
  const response = await post(baseUrl, { jsonrpc: "2.0", id: newId(), method: "tasks/get", params: { id: taskId } }, headers);
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
    const done = await resubscribe(baseUrl, taskId, onEvent);
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
```

`frames`' decoded bytes come from `response.body`; in vitest, `new Response(text)`
provides a body, so the same code runs in both.

`src/app/page.tsx`: `submit` runs `claim.submit` around `streamClaim` and ends
that span when it returns (trace A). If the snapshot is `input-required`, it
records `waitStarted = Date.now()`, renders the waiting view, and runs a second
root span **linked** to the first:

```tsx
      const submitContext = span.spanContext();
      // ... after span.end():
      if (snap.state === "input-required") {
        setWaitingSince(Date.now());
        await getTracer().startActiveSpan(
          "claim.wait",
          { links: [{ context: submitContext }], attributes: { "northstar.claim.id": submitted.claimId } },
          async (wait) => {
            try {
              const final = await awaitOutcome(CLAIMS_AGENT_URL, snap.taskId, (s) => s.result && setResult(s.result));
              if (final.result) setResult(final.result);
            } catch (error) {
              wait.setStatus({ code: SpanStatusCode.ERROR, message: String(error) });
              setLog((entries) => [...entries, `Failed: ${String(error)}`]);
            } finally {
              wait.end();
              setWaitingSince(null);
            }
          },
        );
      }
```

`Determination` uses `determinationView(result)` for tone and heading, and when
`status === "awaiting_approval"` adds: "A person at Northstar is reviewing this
payment. Waiting <formatElapsed(now - waitingSince)>." with a 1-second
`setInterval` that clears on unmount, plus the rule and the limit (`€0` at
assisted). Add the `.is-waiting` tone to `globals.css`. Keep the
`<h2>Outcome</h2>` + `<pre>` contract and the input order (comments at
`:96-98,129-131,149-153`).

Run: `cd apps/customer-ui && npm test && npm run build` → Expected: all pass; the static export builds.

- [ ] **Step 11: Move the harness and the demo driver to `message/stream`**

`tests/harness/live.py`: `submit_claim` POSTs `message/stream` with
`httpx.stream(..., timeout=httpx.Timeout(timeout, read=timeout))`, reads SSE
`data:` lines, and returns at the first `input-required` or terminal state:

```python
TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


def _sse_results(response: httpx.Response):
    for line in response.iter_lines():
        if line.startswith("data:"):
            frame = json.loads(line[len("data:"):].strip())
            if "error" in frame:
                raise AssertionError(f"claims-agent stream error: {frame['error']}")
            yield frame["result"]


def _snapshot(event: dict, prev: dict) -> dict:
    status = event.get("status") or {}
    snap = dict(prev)
    snap["task_id"] = event.get("id") or event.get("taskId") or prev.get("task_id")
    snap["context_id"] = event.get("contextId") or prev.get("context_id")
    if status.get("state"):
        snap["state"] = status["state"]
    for part in reversed((status.get("message") or {}).get("parts") or []):
        if part.get("kind") == "text":
            try:
                snap["reply"] = json.loads(part["text"])
                break
            except (KeyError, json.JSONDecodeError):
                continue
    return snap
```

`submit_claim` returns `{"trace_id", "task_id", "context_id", "state", "reply"}`
(it closes the stream at `input-required`, as the customer-ui does). Add
`resubscribe(task_id, *, timeout, drop_after=None)` (POST `tasks/resubscribe`;
returns at a terminal state, or with `ended_without_terminal=True`; if
`drop_after` seconds pass first it closes the stream and returns with
`dropped=True`) and `get_task(task_id)` (POST `tasks/get`). `scripts/demo.py`'s
`cmd_claim` reads `run["state"]` and `run["reply"]["status"]`: `awaiting_approval`
prints `WAITING  for a human. Nothing was paid.`; `--expect` accepts
`awaiting_approval` (rename the choice from `refused`); the `problems` checks treat
`awaiting_approval` as `deny` with no execute span, and require
`requested > limit` exactly as they did for `refused`.

- [ ] **Step 12: Extend the live suite with task states and the stream**

Add to `tests/e2e/test_human_in_the_loop.py`:

```python
def test_assisted_5800_is_input_required_then_approve_completes_it(approved):
    assert approved["state"] == "input-required"
    assert approved["review"]["resumedState"] is not None, approved["review"]
    with live.claims_agent_route():
        task = live.get_task(approved["task_id"])
    assert task["state"] == "completed"
    assert task["reply"]["status"] == "executed" and task["reply"]["decision"] == "approved"


def test_reject_completes_the_task_declined(rejected):
    with live.claims_agent_route():
        task = live.get_task(rejected["task_id"])
    assert task["state"] == "completed" and task["reply"]["status"] == "rejected"


def test_autonomous_5800_completes_with_no_wait_and_6200_waits():
    with live.autonomy(contract.AUTONOMOUS):
        settled = _claim_at_current_level(5800.0)
        waits = _claim_at_current_level(6200.0)
    assert settled["state"] == "completed" and settled["reply"]["status"] == "executed"
    assert waits["state"] == "input-required"
    assert waits["reply"]["rule"] == "settlement-agent.autonomous.execute_limit"
    assert waits["reply"]["limit"] == 6000.0
    live.review(waits["reply"]["paymentId"], "reject", "demo-cleanup")


def test_a_resubscribe_held_open_ten_minutes_delivers_completed():
    run = _claim(5800.0)
    result = {}
    with live.claims_agent_route():
        listener = threading.Thread(target=lambda: result.update(
            live.resubscribe(run["task_id"], timeout=900)))
        listener.start()
        time.sleep(600)
        assert listener.is_alive(), f"the stream ended before the decision: {result}"
        live.review(run["reply"]["paymentId"], "approve", REVIEWER)
        listener.join(timeout=60)
    assert result.get("state") == "completed", result
    assert result["reply"]["status"] == "executed"


def test_a_dropped_stream_reconnects_and_still_sees_the_outcome():
    run = _claim(5800.0)
    with live.claims_agent_route():
        first = live.resubscribe(run["task_id"], timeout=60, drop_after=3)
        assert first.get("dropped"), first
        live.review(run["reply"]["paymentId"], "reject", REVIEWER)
        once = live.get_task(run["task_id"])          # the one read after a drop
        if once["state"] not in live.TERMINAL_STATES:
            once = live.resubscribe(run["task_id"], timeout=60)
    assert once["state"] == "completed" and once["reply"]["status"] == "rejected"
```

`_claim_at_current_level` is `_claim` without the `assert_assisted` line. Import
`threading` and `time`. **The 10-minute test adds 10 minutes to the gate.** That
is the spec's requirement (§6); do not shorten it or gate it out.

Update `tests/e2e/test_policy_denial.py`'s browser criterion: after submitting
€6,200, the page shows `Awaiting human approval` (wait on that text) and the
`Outcome` JSON has `"status": "awaiting_approval"`.

- [ ] **Step 13: Roll out and verify Task 2**

```bash
./scripts/setup-kind.sh --stage 7
./scripts/start-demo.sh            # in its own terminal; leave it alone
RUN_BROWSER_TESTS=1 .venv/bin/pytest -q tests/e2e/test_human_in_the_loop.py tests/e2e/test_policy_denial.py \
  tests/e2e/test_claims_chain_trace.py tests/e2e/test_browser_root_span.py
./scripts/demo reset
```

Then by hand, in the browser: submit €5,800 at `http://localhost:8080`, see
"Awaiting human approval" with the timer running; approve it at
`http://localhost:8081`; the customer tab flips to "Settled" without a reload.
Repeat with reject → "Declined by a reviewer". Expected: as described, and every
listed test passes.

- [ ] **Step 14: Docs, counts, commit**

`agents/claims-agent/NOTES.md` carries Steps 1–2's measurements;
`docs/architecture.md` gets the task lifecycle (spec §3.3) as implemented,
including the parked `execute()` and why. Refresh the counts block. Then:

```bash
git add agents/claims-agent apps/reviewer-ui apps/customer-ui deploy tests scripts/demo.py docs
git commit -m "feat(m8): the waiting claim is an A2A task the customer streams until a human decides"
```

---

## Task 3: Trace B, the link, and the histogram

**No task review** (the whole-branch review covers it).

**Files:**
- Modify: `apps/reviewer-ui/review.py` (span link, claim id), `apps/reviewer-ui/main.py`
- Create: `apps/reviewer-ui/approval_metrics.py`, `apps/reviewer-ui/tests/test_approval_metrics.py`
- Modify: `apps/reviewer-ui/tests/test_review.py`
- Modify: `platform/eval-scorer/scorer/scores.py` (`score_identity_had_authority`), `tests/test_scores.py`, `tests/test_anti_vacuity.py`, `tests/e2e/test_policy_scores.py` (compliant and violating fixtures)
- Modify: `tests/harness/live.py` (`evidence_for`: approver and wait), `tests/e2e/test_human_in_the_loop.py`, `tests/e2e/test_prometheus_metrics_ingest.py` (reviewer-ui control)
- Modify: `deploy/observability/collector-config.yaml`, `collector-config-dash0.yaml` (verify only; change only if Step 6 finds a gap)
- Modify: `docs/telemetry-contract.md` (new rows), `docs/architecture.md`

**Interfaces:**
- Consumes: Task 2's `review.decide(..., claims=...)`, `ReviewResult.autonomy_level`, the ledger's `refusal.refusedTraceId/refusedSpanId`, `awaitingSince`, `claimId`.
- Produces:
  - `approval_metrics.record_wait(seconds: float, *, decision: str, autonomy_level: str) -> None`; instrument name `northstar.approval.wait.duration`, unit `s`, attributes `decision` (`approved` | `rejected`) and `autonomy.level`.
  - `review.decision` carries a span link to the refusal span and the attribute `northstar.claim.id`; baggage `northstar.claim.id` is set for the gateway call and the resume.
  - `live.evidence_for(...)` row keys `approved_by` and `human_wait_s`.

- [ ] **Step 1: Write the failing metric test**

`apps/reviewer-ui/tests/test_approval_metrics.py`:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import approval_metrics


def test_records_one_sample_in_seconds_with_decision_and_level():
    reader = InMemoryMetricReader()
    approval_metrics.use_meter(MeterProvider(metric_readers=[reader]).get_meter("test"))
    approval_metrics.record_wait(92.5, decision="approved", autonomy_level="assisted")
    metrics = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    (metric,) = [m for m in metrics if m.name == "northstar.approval.wait.duration"]
    assert metric.unit == "s"
    (point,) = metric.data.data_points
    assert point.count == 1 and point.sum == 92.5
    assert dict(point.attributes) == {"decision": "approved", "autonomy.level": "assisted"}


def test_no_reviewer_id_ever_becomes_an_attribute():
    import inspect
    assert "reviewer" not in inspect.signature(approval_metrics.record_wait).parameters
```

Run: `cd apps/reviewer-ui && .venv/bin/pytest tests/test_approval_metrics.py -q` → Expected: FAIL.

- [ ] **Step 2: Implement `approval_metrics.py`**

```python
"""How long humans take: one OpenTelemetry histogram, recorded at the decision.

northstar.approval.wait.duration, seconds, from the ledger's awaitingSince to the
moment the reviewer's decision took effect. Attributes: decision and
autonomy.level. The reviewer's id is deliberately absent: one series per person
would make the metric high-cardinality. Not sampled; the default aggregation is
kept (bootstrap.py installs the provider, with no views).
"""
from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("reviewer-ui")
_histogram = _meter.create_histogram(
    "northstar.approval.wait.duration", unit="s",
    description="Time from a payment awaiting approval to a human's decision")


def use_meter(meter) -> None:
    """Tests only: bind the instrument to a meter with an in-memory reader."""
    global _histogram
    _histogram = meter.create_histogram(
        "northstar.approval.wait.duration", unit="s",
        description="Time from a payment awaiting approval to a human's decision")


def record_wait(seconds: float, *, decision: str, autonomy_level: str) -> None:
    _histogram.record(seconds, {"decision": decision, "autonomy.level": autonomy_level})
```

Run: `.venv/bin/pytest tests/test_approval_metrics.py -q` → Expected: 2 passed.

- [ ] **Step 3: Record it, link the refusal, carry the claim id**

In `review.decide`, inside `review.decision`, right after the ledger read:

```python
        trace_id, span_id = refusal.get("refusedTraceId"), refusal.get("refusedSpanId")
        if trace_id and span_id:
            span.add_link(SpanContext(trace_id=int(trace_id, 16), span_id=int(span_id, 16),
                                      is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
        if record.get("claimId"):
            span.set_attribute("northstar.claim.id", record["claimId"])
            claim_token = otel_context.attach(baggage.set_baggage("northstar.claim.id", record["claimId"]))
```

(detach `claim_token` in a `finally` around the gateway call and the resume).
The link is added after start because the ids come from the ledger read, which
must itself run inside the span (the module docstring's reason); the SDK's
`Span.add_link` (opentelemetry-sdk 1.44.0, `sdk/trace/__init__.py:934`) keeps a
valid link and drops an invalid one, and the exporter reads links at span end.
After a successful execute or reject:

```python
            approval_metrics.record_wait(
                elapsed_ms / 1000.0,
                decision="approved" if decision == "approve" else "rejected",
                autonomy_level=result.autonomy_level)
```

A failed execute records nothing (no decision took effect). Tests in
`tests/test_review.py` (with an `InMemorySpanExporter` and an
`InMemoryMetricReader`): the finished `review.decision` span has exactly one link
whose ids equal the ledger's; no link when the ids are empty; one histogram
sample per successful decision, none for a refused execute; the span carries
`northstar.claim.id`.

Run: `.venv/bin/pytest -q` → Expected: all pass.

- [ ] **Step 4: Let the auditor score a human release**

In `platform/eval-scorer/scorer/scores.py::score_identity_had_authority`, before
the published-limit lookup:

```python
        if execution.caller in PUBLISHED_HUMAN_APPROVERS:
            if execution.rule != "human-approved.execute" or not trajectory.approving_reviews():
                unknown.append(
                    f"{execution.caller!r} released money without a human approval in this "
                    f"trajectory under rule {execution.rule!r} ({execution.where()})")
            continue
```

(read `execution.rule` from the decision span's `policy.rule`; add the field to
the trajectory's execution record if it is not there). Tests in
`tests/test_scores.py`: a reviewer-ui execution under `human-approved.execute`
with an approving `review.decision` scores 1.0; the same execution with no review
scores 0.0 `unknown-identity`. In `tests/e2e/test_policy_scores.py`: the compliant
claim is €5,800 at `assisted`, approved through `live.review`; the **violating**
claim is a fresh claim id at €5,800 that, once waiting, is sent **again, directly
to the settlement-agent's A2A endpoint** (`SETTLEMENT_AGENT_URL`, the same claim
id), so the refused identity proposes and tries to execute again: a payment call
after a refusal, with nothing modified. Its assertion is
`stopped_when_refused == 0.0`. Rewrite the fixture docstrings accordingly.

- [ ] **Step 5: Evidence shows who approved and how long the human took**

`live.evidence_for` adds `approved_by` (`review.reviewer.id` of the approving
`review.decision`) and `human_wait_s` (`review.elapsed_ms / 1000` of that span),
both `None` when no human was involved. `executing_identity` now legitimately
differs from `caller_identity` for a human release (reviewer-ui vs
settlement-agent): `scripts/demo.py::cmd_evidence`'s "executed by someone other
than the caller" problem becomes "executed by someone other than the caller
**and no human approved it**".

- [ ] **Step 6: Confirm the metric reaches Prometheus, then measure the series name**

The reviewer-ui already installs a `MeterProvider` exporting OTLP
(`apps/reviewer-ui/bootstrap.py:104-123`), and the collector's `metrics`
pipeline already exports to Prometheus at stage ≥ 4
(`deploy/observability/collector-config.yaml:282-294`, `otlp_http/prometheus`).
So no collector change is expected. Verify, do not assume:

```bash
./scripts/setup-kind.sh --stage 7
./scripts/demo claim 5800        # waits at assisted
curl -s -X POST http://localhost:8081/api/review/<paymentId> -H 'content-type: application/json' \
  -d '{"reviewerId":"alex.mercer","decision":"approve"}'
sleep 75                         # one 60 s export interval, plus slack
curl -s http://localhost:9090/api/v1/label/__name__/values | tr ',' '\n' | grep -i approval
```

Expected: one series name. Write the name you read (predicted
`northstar_approval_wait_duration_seconds_*`) into the test below and the
contract, never the prediction. If it is absent, find the gap (collector
`debug` exporter output, the reviewer-ui's export errors) and fix the config;
keep `collector-config.yaml` and `collector-config-dash0.yaml` in step
(`tests/unit/test_collector_configs_in_sync.py`). Add to
`tests/e2e/test_human_in_the_loop.py`:

```python
def test_the_histogram_has_a_sample_for_the_approval(approved):
    series = live.prometheus_series('<measured_name>_count{job="reviewer-ui",decision="approved"}')
    assert series, "no approval-wait sample reached Prometheus"
```

(`live.prometheus_series` wraps the port-forward and `/api/v1/series` query that
`test_prometheus_metrics_ingest.py` already uses: move those helpers into the
harness rather than copying them.)

- [ ] **Step 7: Trace B and the link, live**

Add to `tests/e2e/test_human_in_the_loop.py`:

```python
def test_trace_b_is_rooted_at_review_decision_and_links_to_trace_a(approved):
    jaeger = JaegerClient(JAEGER_API)
    trace_b = jaeger.wait_for_operation("reviewer-ui", "review.decision",
                                        attrs={"review.payment.id": approved["payment_id"]}, timeout=90)
    root = trace_b.root()
    assert root.operation == "review.decision"
    assert any(link.trace_id == approved["trace_id"] for link in root.links), root.links
    decisions = [s for s in trace_b.spans if s.operation == "policy.decision"]
    assert [d.attrs["policy.rule"] for d in decisions] == ["human-approved.execute"]
    assert decisions[0].attrs["policy.ledger.status"] == "awaiting_approval"
    ops = {s.operation for s in trace_b.spans}
    assert "POST /payments/{paymentId}/execute" in ops


def test_trace_a_holds_the_deny_and_no_execute(approved):
    trace_a = JaegerClient(JAEGER_API).wait_for_trace_id(approved["trace_id"], services={"policy-decision-service"}, timeout=90)
    denies = [s for s in trace_a.spans if s.operation == "policy.decision" and s.attrs.get("policy.decision") == "deny"]
    assert denies and denies[0].attrs["policy.limit.amount"] == 0.0
    assert "POST /payments/{paymentId}/execute" not in {s.operation for s in trace_a.spans}
```

Use the harness's real `JaegerClient` methods (`tests/harness/jaeger.py`); if
`wait_for_operation` or `Span.links` do not exist, add them there (one place),
with a unit test in `tests/unit/test_jaeger_assertions.py`.

The customer's wait is asserted in the browser, because only the browser emits
`claim.wait`. Add to `tests/e2e/test_policy_denial.py`'s browser run (the €6,200
waiting claim), after it approves through `live.review` and the page shows
`Settled`:

```python
    jaeger = JaegerClient(JAEGER_API)
    wait = jaeger.wait_for_operation("customer-ui", "claim.wait",
                                     attrs={"northstar.claim.id": claim_id}, timeout=90)
    span = wait.root()
    submit = jaeger.wait_for_operation("customer-ui", "claim.submit",
                                       attrs={"northstar.claim.id": claim_id}, timeout=90).root()
    assert span.trace_id != submit.trace_id            # the wait is its own trace
    assert [l.trace_id for l in span.links] == [submit.trace_id]
    assert span.duration_s >= approved_at_s - submitted_at_s - 1.0
```

`submitted_at_s` and `approved_at_s` are `time.time()` taken by the test just
before it clicks Submit and just after `live.review` returns.

- [ ] **Step 8: The contract rows**

In `docs/telemetry-contract.md`, add sections, each with a table read off a live
span or series (dump it in full, then write): "reviewer-ui's
`northstar.approval.wait.duration` (M8 Task 3)"; "Trace B: the human release"
(the span tree from Step 7, with `policy.ledger.status`); "The customer's wait:
`claim.wait` and `tasks/resubscribe`"; "A2A task states on claims-agent spans"
(`northstar.a2a.task.id`, `northstar.a2a.task.state`, and whatever the a2a-sdk
spans show). Add the new owned attributes to "Domain attributes we own" and let
`tests/unit/test_telemetry_contract_attribute_counts.py` say what else moved.
Each row is **MEASURED in the gate**: the gate run in Task 5 is what proves them.
Refresh the counts block.

- [ ] **Step 9: Verify and commit**

```bash
for d in apps/reviewer-ui platform/eval-scorer; do (cd $d && .venv/bin/pytest -q) || break; done
.venv/bin/pytest tests/unit -q
RUN_BROWSER_TESTS=1 .venv/bin/pytest -q tests/e2e/test_human_in_the_loop.py tests/e2e/test_policy_scores.py \
  tests/e2e/test_evidence_view.py tests/e2e/test_prometheus_metrics_ingest.py tests/e2e/test_policy_denial.py
./scripts/demo reset
git add apps/reviewer-ui platform/eval-scorer tests deploy/observability scripts/demo.py docs
git commit -m "feat(m8): trace B links to the claim, and a histogram says how long humans take"
```

Mutation: delete the `span.add_link(...)` line → `test_trace_b_is_rooted_at_review_decision_and_links_to_trace_a`
and the unit link test must go RED (the unit one is enough for `mutate-guard.sh`;
the live one is checked once by hand).

---

## Task 4: A guardrail in agentgateway itself — the CEL ceiling

**No task review.** Not part of the live demo (spec §5: it lives on the deck).

The rule, exactly as the spec gives it, as a route-level `authorization` **allow**
rule (a `deny:` rule fails open on a string amount; measured in
`.demo/reports/cel-spike-report.md` §4 finding 4):

```
mcp.methodName != "tools/call" || mcp.tool.name != "execute_payment" || (mcp.tool.arguments.amount > 0 && mcp.tool.arguments.amount <= 50000)
```

It applies to every caller. It is only valid on v1.6.0+: on v1.5.0 the route
policy has no MCP context and this rule denies everything (spike §2.4). The spike
did **not** measure the MCP client's optional `GET` (server-to-client SSE) or
`DELETE` on `/mcp`, where `mcp.methodName` may be absent and the allow rule would
deny. Step 4 measures it; if those requests are denied and something breaks, the
fix is the prefix `request.method != "POST" || `, recorded as a measured deviation.

**Files:**
- Modify: `deploy/platform/agentgateway/config.yaml` (one `authorization` block on the route)
- Create: `tests/unit/test_gateway_ceiling_config.py`, `tests/e2e/test_gateway_ceiling.py`
- Modify: `scripts/demo.py` (a `ceiling` command, not part of the flow), `tests/unit/test_demo_driver.py`
- Modify: `docs/architecture.md`, `docs/telemetry-contract.md` (the 403's spans), `docs/demo-runbook.md` (appendix only)

**Interfaces:**
- Consumes: Task 0's v1.6.0 gateway.
- Produces: `CEILING_EUR = 50000` (read from the config by the tests, never retyped); `scripts/demo ceiling` (off-stage probe).

- [ ] **Step 1: Write the failing config test**

`tests/unit/test_gateway_ceiling_config.py`:

```python
"""The gateway's own guardrail: a hard ceiling on execute_payment, as CEL."""
import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "platform" / "agentgateway" / "config.yaml"
RULE = ('mcp.methodName != "tools/call" || mcp.tool.name != "execute_payment" '
        '|| (mcp.tool.arguments.amount > 0 && mcp.tool.arguments.amount <= 50000)')


def _route():
    config = yaml.safe_load(CONFIG.read_text())
    (bind,) = config["binds"]
    (listener,) = bind["listeners"]
    (route,) = listener["routes"]
    return route


def test_the_ceiling_is_one_allow_rule_on_the_route():
    rules = _route()["policies"]["authorization"]["rules"]
    assert len(rules) == 1
    rule = rules[0]
    text = rule if isinstance(rule, str) else rule.get("allow")
    assert text is not None, "the ceiling must be an allow rule: a deny rule fails open on a string amount"
    assert " ".join(text.split()) == RULE


def test_the_ceiling_is_above_every_published_agent_limit():
    rules_py = (CONFIG.parents[3] / "platform" / "policy-decision-service" / "pds" / "rules.py").read_text()
    limits = [float(x.replace("_", "")) for x in re.findall(r'"\):\s*([0-9_.]+),', rules_py)]
    assert limits and max(limits) < 50000


def test_the_pds_hook_is_still_there():
    assert _route()["policies"]["mcpGuardrails"]["processors"][0]["failureMode"] == "failClosed"
```

Run: `.venv/bin/pytest tests/unit/test_gateway_ceiling_config.py -q` → Expected: FAIL (`KeyError: 'authorization'`).

- [ ] **Step 2: Add the rule**

In `deploy/platform/agentgateway/config.yaml`, under the route's `policies:`
(sibling of `jwtAuth` and `mcpGuardrails`):

```yaml
        # A hard ceiling, enforced by the gateway itself, before the PDS is
        # asked: no caller may execute a payment above EUR 50,000, or of a
        # non-positive amount. Written as an ALLOW rule on purpose: a `deny:`
        # rule fails OPEN on a value it cannot evaluate (a string "60000"
        # executed in the spike), while an allow rule that errors denies.
        # v1.6.0+ only: on v1.5.0 this policy has no MCP context and denies
        # every request. Measured: .demo/reports/cel-spike-report.md §2.2, §4.
        # A denial is `403 text/plain "authorization failed"` and leaves no
        # policy.decision span: the PDS is never asked.
        authorization:
          rules:
          - allow: 'mcp.methodName != "tools/call" || mcp.tool.name != "execute_payment" || (mcp.tool.arguments.amount > 0 && mcp.tool.arguments.amount <= 50000)'
```

Validate against the pinned image, from `deploy/platform/agentgateway`:

```bash
docker run --rm -v .:/c cr.agentgateway.dev/agentgateway:v1.6.0 --validate-only -f /c/config.yaml
```

Expected: it reaches the JWKS fetch stage (the validator's last), as the file's
own comment describes. Run the unit test → Expected: 3 passed. Mutate: change
`allow:` to `deny:` → `test_the_ceiling_is_one_allow_rule_on_the_route` RED.

- [ ] **Step 3: Write the live test**

`tests/e2e/test_gateway_ceiling.py`:

```python
"""The gateway's ceiling, live: EUR 60,000 is stopped before the PDS is asked."""
import pytest

from tests.harness import live
from tests.harness.jaeger import JaegerClient

JAEGER_API = "http://localhost:16686"


@pytest.fixture(scope="module")
def gateway():
    with live.gateway_port_forward() as url:
        yield url, {"authorization": f"Bearer {live.mint_svid(live.GATEWAY_AUDIENCE)}"}


def test_60000_is_a_403_from_the_gateway(gateway):
    url, headers = gateway
    outcome = live.call_tool_directly(url, headers, "execute_payment",
                                      {"payment_id": "PAY-999999", "amount": 60000.0, "currency": "EUR"})
    assert outcome.http_status == 403, outcome
    assert outcome.body.strip() == "authorization failed", outcome


def test_the_403_leaves_no_policy_decision_span(gateway):
    url, headers = gateway
    traceparent_trace_id = live.new_trace_id()
    live.call_tool_directly(url, {**headers, "traceparent": f"00-{traceparent_trace_id}-{'1' * 16}-01"},
                            "execute_payment", {"payment_id": "PAY-999999", "amount": 60000.0, "currency": "EUR"})
    trace = JaegerClient(JAEGER_API).wait_for_trace_id(traceparent_trace_id, services={"agentgateway"}, timeout=60)
    assert not [s for s in trace.spans if s.operation == "policy.decision"], trace.spans


def test_initialize_and_tools_list_still_pass(gateway):
    url, headers = gateway
    outcome, tools = live.list_tools_directly(url, headers)
    assert outcome.http_status == 200 and "execute_payment" in tools, outcome


def test_a_string_amount_is_denied_not_waved_through(gateway):
    url, headers = gateway
    outcome = live.call_tool_directly(url, headers, "execute_payment",
                                      {"payment_id": "PAY-999999", "amount": "60000", "currency": "EUR"})
    assert outcome.http_status == 403, outcome
```

`call_tool_directly` initializes first; the ceiling must let `initialize`
through and stop only the `tools/call`. `live.new_trace_id()` is
`uuid.uuid4().hex` (add it if absent). The `traceparent` header is honoured by the
gateway (`randomSampling: true`, `parentNotSampled: true`), and the Jaeger wait
fails, never skips, when the trace does not arrive.

- [ ] **Step 4: Roll out and measure the rest of the MCP traffic**

```bash
./scripts/setup-kind.sh --stage 7
RUN_BROWSER_TESTS=1 .venv/bin/pytest -q tests/e2e/test_gateway_ceiling.py tests/e2e/test_human_in_the_loop.py \
  tests/e2e/test_gateway_trace.py tests/e2e/test_caller_identity.py
kubectl --context kind-northstar -n northstar-platform logs deploy/agentgateway -c agentgateway --since=10m \
  | grep -c '"http.status":403'
```

Expected: all pass; every 403 in the log is one of this test's calls. Check
the log for any `GET` or `DELETE` on `/mcp` answered 403: if one exists and
nothing failed, record it (the client tolerates it); if a test failed because of
it, apply the `request.method != "POST" || ` prefix, re-validate, re-run, and
record the measured reason in the config comment.

- [ ] **Step 5: `scripts/demo ceiling`, off stage**

Add `cmd_ceiling` to `scripts/demo.py`: mint the settlement-agent's SVID, call
`execute_payment` for €60,000 through the gateway, print
`HTTP 403  authorization failed  -- the gateway said no, the PDS was never asked`,
and exit non-zero unless it was a 403. Register it as `ceiling` with help
"the gateway's own CEL ceiling (not in the five-minute flow)".
`tests/unit/test_demo_driver.py::test_the_subcommands_are_exactly_the_five_minute_flow_plus_reset`
changes its expected set in Task 5 (with `approve` and `reject`).

- [ ] **Step 6: Docs, counts, commit**

`docs/architecture.md`: "Two ways to say no" (spec §5's table, with the measured
figures and their sources). `docs/telemetry-contract.md`: the 403's span as read
from Jaeger (`POST /*`, `error.type=Authorization`, no `policy.decision`).
Runbook: an appendix entry for `scripts/demo ceiling`. Refresh counts.

```bash
git add deploy/platform/agentgateway/config.yaml tests scripts/demo.py docs
git commit -m "feat(m8): a CEL ceiling in agentgateway, stopped before the PDS is asked"
```

---

## Task 5: Demo, runbook, deck — then the review, then one gate

**No task review.** The whole-branch review follows this task.

**Files:**
- Modify: `scripts/demo.py` (`approve`, `reject`, claim output, flow docstring, `reset`), `scripts/demo` (unchanged shim; verify)
- Modify: `tests/e2e/test_demo_flow.py` (rewrite to spec §4), `tests/unit/test_demo_driver.py`
- Modify: `docs/demo-runbook.md` (§5 five-minute flow, §7 reset, §8 triage, Beat 4)
- Modify: `talk/slides/30-live.html`, `talk/slides/30-live.css`, `talk/notes/30-live.json`,
  `talk/slides/31-act3b.html`, `talk/slides/31-act3b.css`, `talk/notes/31-act3b.json`,
  `talk/slides/30-act3a.html` (if it names the senior), `talk/notes/90-thanks.json`, `talk/PLAN.md` (regenerated), `talk/index.html` (rebuilt)
- Modify: `docs/telemetry-contract.md` (gate row, counts)

**Interfaces:**
- Consumes: everything above: `live.submit_claim`, `live.review`, `live.autonomy`, `/api/awaiting`, `/api/review/{id}`, `evidence_for`'s `approved_by`/`human_wait_s`, `cmd_ceiling`.
- Produces: `scripts/demo approve <claim>` / `reject <claim>` (`<claim>` is a claim id from this run's state, or `last`); `reset` rejects every open approval as `demo-reset`.

- [ ] **Step 1: Write the failing driver unit tests**

In `tests/unit/test_demo_driver.py`:

```python
def test_the_subcommands_are_the_m8_flow_plus_reset_and_the_ceiling():
    out = subprocess.run([str(ROOT / "scripts" / "demo"), "--help"], capture_output=True, text=True, timeout=30)
    for command in ("guard", "claim", "approve", "reject", "autonomy", "keystone", "evidence", "reset", "ceiling"):
        assert command in out.stdout, out.stdout


def test_approve_resolves_the_claim_to_its_payment_from_this_runs_state(monkeypatch, tmp_path):
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    demo.save_state({"run_id": "r", "window_start_ms": 0, "claims": [
        {"id": "CLM-DEMO-AAA", "payment_id": "PAY-000007", "status": "awaiting_approval", "amount": 5800.0,
         "level": "assisted", "trace_id": "t"}]})
    sent = []
    monkeypatch.setattr(demo.live, "review", lambda pid, decision, reviewer: sent.append((pid, decision, reviewer))
                        or {"error": None, "executed": {"status": "executed"}, "elapsedMs": 4200,
                            "resumedState": "input-required", "resumeError": None, "rejected": None})
    assert demo.main(["approve", "last"]) == 0
    assert sent == [("PAY-000007", "approve", demo.STAGE_REVIEWER)]


def test_reset_rejects_every_open_approval_as_demo_reset(monkeypatch):
    demo = _driver()
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [{"paymentId": "PAY-000001"}, {"paymentId": "PAY-000002"}])
    sent = []
    monkeypatch.setattr(demo.live, "review", lambda pid, d, r: sent.append((pid, d, r)) or {"error": None})
    monkeypatch.setattr(demo, "delete_overgrant", lambda: None)
    monkeypatch.setattr(demo, "overgrant_present", lambda: False)
    monkeypatch.setattr(demo.contract, "set_targeted_branch", lambda level: None)
    monkeypatch.setattr(demo, "flag_level", lambda: "assisted")
    assert demo.main(["reset"]) == 0
    assert sent == [("PAY-000001", "reject", "demo-reset"), ("PAY-000002", "reject", "demo-reset")]
```

Replace
`test_the_subcommands_are_exactly_the_five_minute_flow_plus_reset` with the first
test. Update `test_a_refused_claim_that_executes_would_fail_the_run` to the
`awaiting_approval` vocabulary.

Run: `.venv/bin/pytest tests/unit/test_demo_driver.py -q` → Expected: FAIL.

- [ ] **Step 2: Implement `approve`, `reject`, and the reset**

In `scripts/demo.py`:

```python
REVIEWER_UI = os.environ.get("REVIEWER_UI_URL", "http://localhost:8081")
STAGE_REVIEWER = "kasper.on.stage"


def awaiting_payments() -> list[dict]:
    response = requests.get(f"{REVIEWER_UI}/api/awaiting", timeout=15)
    response.raise_for_status()
    return response.json()


def _claim_from_state(ref: str) -> dict:
    claims = load_state().get("claims") or []
    waiting = [c for c in claims if c.get("status") == "awaiting_approval"]
    if ref == "last":
        if not waiting:
            raise Fail("no claim in this run is waiting for a human")
        return waiting[-1]
    for claim in claims:
        if claim["id"] == ref:
            return claim
    raise Fail(f"{ref!r} is not a claim in this run: {[c['id'] for c in claims]}")


def _decide(args: argparse.Namespace, decision: str) -> None:
    claim = _claim_from_state(args.claim)
    verb = "APPROVE" if decision == "approve" else "REJECT"
    title(f"{verb}  {claim['id']}   EUR {claim['amount']:,.0f}")
    say("A person decides. The reviewer-ui acts under its own identity, through the gateway.")
    doing(f"reviewer-ui  POST /api/review/{claim['payment_id']}  decision={decision}")
    result = live.review(claim["payment_id"], decision, STAGE_REVIEWER)
    if result.get("error"):
        raise Fail(f"the reviewer-ui could not {decision}: {result['error']}")
    seconds = result["elapsedMs"] / 1000
    if decision == "approve":
        out(f"  Outcome  {word('PAID', 'go')}  released by a human after {seconds:0.0f}s")
    else:
        out(f"  Outcome  {word('DECLINED', 'stop')}  by a human after {seconds:0.0f}s. Nothing was paid.")
    if result.get("resumeError"):
        raise Fail(f"the customer's claim could not be told: {result['resumeError']}")
    out(f"  The customer's claim was told: {word('completed', 'info')}")
    claim["status"] = "executed" if decision == "approve" else "rejected"
    state = load_state()
    state["claims"] = [claim if c["id"] == claim["id"] else c for c in state["claims"]]
    save_state(state)


def cmd_approve(args: argparse.Namespace) -> None:
    _decide(args, "approve")


def cmd_reject(args: argparse.Namespace) -> None:
    _decide(args, "reject")
```

Register `approve <claim>` and `reject <claim>` (help: "a human releases /
declines a waiting claim (claim id or 'last')"). `cmd_reset` first rejects
every `awaiting_payments()` entry with reviewer `demo-reset`, prints
`open approvals  NONE (rejected N)`, then fails if `awaiting_payments()` is still
non-empty. Rewrite the module docstring's command list and the flow line to
spec §4. `cmd_claim` prints the waiting state as Task 2 Step 11 set it, plus
`approve with: scripts/demo approve <claim-id>`.

Run: `.venv/bin/pytest tests/unit/test_demo_driver.py -q` → Expected: all pass.

- [ ] **Step 3: Rewrite the five-minute flow e2e to spec §4**

`tests/e2e/test_demo_flow.py` drives `scripts/demo` exactly as the stage does:

1. `guard` — unchanged assertions.
2. `claim 5800` at `assisted` → `WAITING`; then `approve last` → `PAID`, and
   `live.get_task(task_id)["state"] == "completed"` (the browser half is
   `test_policy_denial.py`'s; this file drives the stage fallback).
3. `autonomy autonomous` → `claim 5800 --expect executed` → `claim 6200
   --expect awaiting_approval` (the decision span's limit is `6000.0`, read off
   the span) → `autonomy assisted`.
4. `keystone` — unchanged.
5. `evidence` — every claim row names who decided, the rule, who approved (the
   stage reviewer for step 2's claim, `none` for step 3's settled claim) and the
   human wait in seconds for the approved one.
6. `reset` — the flag reads `assisted`, the over-grant is absent, and
   `awaiting_payments()` is empty (step 3's €6,200 claim was rejected as
   `demo-reset`).

Keep `test_2b_...is_error_tool_result` from Task 0. Run it after Step 7's rollout.

- [ ] **Step 4: The runbook**

`docs/demo-runbook.md` §5 "The five-minute flow": replace the five steps with
spec §4's table and DO / SHOW / SAY per step, written for a room:

- Step 2 DO: in the customer tab, claim €5,800; in the reviewer tab, read the
  rule (`assisted`), the limit (€0) and the model's narrative; approve. SHOW: the
  customer tab flips to "Settled" without a reload. SAY: "The agent did the work.
  A human signed. The trace shows how long the human took." Fallback:
  `scripts/demo claim 5800` then `scripts/demo approve last`.
- Step 3 DO: `scripts/demo autonomy autonomous`, `scripts/demo claim 5800`,
  `scripts/demo claim 6200`. SAY: "One live dial. The band widens; the human stays."
- Step 5 SAY: "For each claim: who decided, the rule, who approved, how long
  the human took."

§4 pre-flight: no open approvals (`curl -s localhost:8081/api/awaiting` → `[]`).
§7 reset: `scripts/demo reset` rejects open approvals as `demo-reset`. §8 triage:
"the customer tab never flips" → check `scripts/start-demo.sh`'s claims-agent
forward, then `scripts/demo approve last` (the stage fallback); "a claims-agent
restart" → waiting tasks are lost (in memory, by design); reject them with
`scripts/demo reset`. Beat 4 ("the human overturns it") is rewritten for the
reviewer-ui path. The ceiling is an appendix item, not a beat.

- [ ] **Step 5: The deck**

All text written for beginners. Every changed slide keeps its leading
`<!-- id · ORIGIN · what -->` comment, and every slide keeps its note in the
matching `talk/notes/*.json` entry (same order, `secs` set).

1. **Replace "Why not a CEL rule"** (`talk/slides/31-act3b.html:173`, note index 7
   in `31-act3b.json`) with a slide labelled **"Two ways to say no"**, showing
   spec §5's table verbatim (agentgateway CEL vs PDS: code to write, sees tool
   arguments, changes live, says why, in the trace). Note (KASPER, ~40 s): "There
   are two ways for the platform to say no. The gateway can do it alone, with one
   line of config: no payment over fifty thousand, for anyone. It answers 403 and
   says nothing else. The decision service is a service we wrote: it has the live
   dial, the rule's name, a reason the model can explain, and its own span. We use
   both. The gateway holds the hard ceiling; the service makes the decision."
2. **Rename the existing hide/refuse slide** (`:251`, currently also labelled
   "Two ways to say no") to **"Hide or refuse"**, so no two slides share a label.
   Its note keeps its content.
3. **Add "Guardrails for model traffic"** after the new "Two ways to say no": the
   list spec §5 gives (regex and personal-data masking, moderation, a webhook
   hook, the Bedrock, Azure Content Safety and Google Model Armor connectors),
   read from `crates/agentgateway/src/llm/policy/` at the v1.6.0 tag (cite the
   path on the slide), and one plain line: "Northstar's model calls do not go
   through the gateway today, so none of these are in the demo."
4. **"HTTP 200 means no"** (`:88`) and `30-live.html:64`: the footnote becomes
   "The no is **HTTP 200**: a tool result marked `isError`, with the rule in its
   text." Every `-32001` in `talk/slides` and `talk/notes` goes (derive with
   `git grep -n "32001" -- talk/slides talk/notes`).
5. **The demo block** (`30-live.html`): Step 2 becomes "a human signs" (customer
   tab waiting with a timer, reviewer tab with rule/limit/narrative, the flip to
   Settled); Step 3 becomes "one live dial" with 5,800 settled and 6,200 waiting;
   the fallback slides show the terminal output of `scripts/demo claim 5800` +
   `scripts/demo approve last` and of Step 3, pasted from a real run. Notes: the
   spec §4 talk points, ~90 s and ~75 s.
6. **"A human can overturn it"** (`31-act3b.html:338`): becomes the maker-checker
   slide: trace A (the agent did everything up to the money), trace B (the human
   released it), the link, and the histogram's p50/p95 per autonomy level (an
   example query `histogram_quantile(0.95, sum by (le, autonomy_level) (rate(<measured series>_bucket[1h])))`).
7. Remove every remaining senior reference in `talk/slides` and `talk/notes`
   (`git grep -n -i senior -- talk/slides talk/notes talk/index.html`).

Then, from `talk/`:

```bash
python3 build/build.py
python3 build/plan.py
python3 build/plan.py --check     # expect exit 0
```

Read the regenerated `PLAN.md`'s running total: the live block must still fit
about five minutes (spec §4: ~5:00). If it does not, say so in the commit
message rather than trimming notes silently.

- [ ] **Step 6: Commit Task 5's work**

```bash
git add scripts/demo.py tests docs talk
git commit -m "docs(m8): the human in the five-minute flow, the runbook, and two ways to say no"
```

- [ ] **Step 7: The whole-branch review, and its fix wave**

Dispatch the whole-branch review (opus) over `m7-bounded..m8-human-in-the-loop`
with the spec and this plan. Fix everything it requires; commit each fix with
explicit paths. **Do not run the gate before the fix wave is committed**: the
fix wave changes gate paths and would demote a measured row.

- [ ] **Step 8: Prepare the cluster for the one gate run**

```bash
git status --short                              # expect: empty (a dirty tree marks Backstage's image -dirty)
./scripts/setup-kind.sh --stage 7               # rebuilds every image from this HEAD, Backstage included
./scripts/start-demo.sh                         # its own terminal; leave it alone
until curl -sf http://localhost:7007/ >/dev/null && curl -sf http://localhost:7007/autonomy-contract >/dev/null; do sleep 5; done
curl -s http://localhost:8081/api/awaiting      # expect []
./scripts/demo reset
```

Warm Backstage by opening its home page and the autonomy-contract page once in a
browser (a cold pod fails the band walk, M7's record).

- [ ] **Step 9: Run the gate, once**

```bash
./scripts/smoke-test.sh > .demo/m8-gate.log 2>&1; echo "exit $?"
grep -c SKIPPED .demo/m8-gate.log            # expect 0
tail -5 .demo/m8-gate.log
```

(`smoke-test.sh` sets `RUN_BROWSER_TESTS=1` itself.) It now includes the
10-minute stream test, so expect roughly ten minutes more than M7's 10m43s.
Expected: exit 0, zero skips, `<N> passed, <M> xfailed` where N + M equals the
collection count the contract publishes.

- [ ] **Step 10: Record the MEASURED gate row and commit it**

In `docs/telemetry-contract.md`'s current block: `Gate row: **MEASURED** — M8's
gate run was at commit `<sha>``, the command, the date, the cluster stage,
"Backstage rebuilt from that clean tree and warmed", the exit code, the
pass/xfail figures and the duration, all copied from `.demo/m8-gate.log`. Remove
the `PREDICTED, NOT MEASURED` label. Mark every M8 contract row from Task 3 as
measured by this run. Then:

```bash
.venv/bin/pytest tests/unit/test_telemetry_contract_counts.py -q   # expect: pass, label gone
git commit -m "docs(m8): record the measured gate row" -- docs/telemetry-contract.md
```

If the gate fails: fix, commit, and run it again; record every run in the
contract, as M7 did (`:495-512`).

---

## Self-review

**Spec coverage.** §1 dial table → Task 1 Steps 1–2. §2 (as-is) → used to scope
deletions. §3.1 → Task 1 Steps 1–5, 10–13 (choice stated at the top of Task 1).
§3.2 → Task 1 Steps 6–8 (`awaitingSince` replaces `refusedAt`; `a2aTaskId`;
reject; execute refuses rejected). §3.3 → Task 2 (task store, `input-required`,
streaming, resubscribe, resume, a declined claim is `completed`). §3.4 → Task 2
Step 1 (10-minute measurement), Task 3 (link, histogram, resubscribe span, rows).
§3.5 → Task 1 Steps 12–13 (reviewer name stays a form field; reviewer-ui SVID;
keystone retargeted). §4 → Task 5 Steps 2–4. §5 → Task 4 and Task 5 Step 5.
§6 → Task 1 Steps 1, 6, 15; Task 2 Steps 3, 9, 12; Task 3 Steps 6–8; Task 4
Step 3; Task 5 Step 3. §7 → Tasks 1–5 (with the stated move of the reviewer-ui's
execute into Task 1). §8 → nothing builds reviewer sign-in, model-traffic
routing, persistent storage, expiry or push notifications.

**Gaps found and handled in the plan:** the eval-scorer and Backstage both carry
the senior identity (Task 1 Step 13); every e2e test that relied on €5,800
settling at `assisted` (Task 1 Step 14); the 10-minute test's cost to the gate
(Task 2 Step 12, Task 5 Step 9); the doc-status guard could not see this plan's
filename (Task 0 Step 2).

**Placeholder scan.** `<measured_name>` in Task 3 Step 6 and `<sha>`/figures in
Task 5 Step 10 are values that must be measured, by this repo's rule, not
predicted; each step says where to read them.

**Type consistency.** `format_deny`/`parse_deny`/`DenialText`; `PolicyInput.ledger_status`;
`HUMAN_APPROVERS`, `HUMAN_APPROVAL_RULE`, `AWAITING_APPROVAL`; `payment_status`;
`GatewayClient.execute_payment`; `ClaimsClient.resume`; `ReviewResult.executed / rejected / error / autonomy_level / resumed_state / resume_error`;
`record_wait(seconds, *, decision, autonomy_level)`; harness `submit_claim` →
`{"trace_id","task_id","context_id","state","reply"}`, `resubscribe`, `get_task`,
`review`, `autonomy`; customer-ui `TaskSnapshot`, `streamClaim`, `resubscribe`,
`getTask`, `awaitOutcome`, `determinationView`, `formatElapsed`. Checked across tasks.
