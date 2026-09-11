"""M3 "Supervised"'s exit criteria 1-7, one test each, named for the criterion
it discharges (`docs/superpowers/specs/2026-09-10-northstar-m3-supervised-design.md`
§7).

**What each criterion costs, and why.** One browser-driven denial
(`denial_run`, CLM-1842 @ EUR 6,200 -- the same claim and amount M2.2's
`test_policy_denial.py` uses, because criterion 1 is explicitly a regression
test on that exact promise) and one browser-driven approval click on
reviewer-ui (`approval_run`, no model call: `execute_approved` makes none).
Criteria 1 and 2 read `denial_run`; criteria 3, 4 and 5 read `approval_run`
(which itself depends on `denial_run`); criterion 6 reads both, directly, for
its own probe. Criterion 7 needs neither -- no model call, no browser, just a
minted JWT-SVID against the gateway, the same shape as
`test_policy_denial.py`'s own criterion 5. So a full run of this file spends
exactly one settlement's worth of Anthropic tokens, never more, matching this
suite's own convention.

**Machinery reused, not reimplemented.** `_drive_browser`, `claims_agent_route`
and the trace-shape/attribute constants come from `test_policy_denial.py` --
M2.2's own one-test-per-criterion file, and the closest analogue to this one.
`gateway_url`, `mint_svid`, `valid_svid`, `call_tool_directly`,
`CALLER_SPIFFE_ID` and `GATEWAY_AUDIENCE` come from `test_caller_identity.py`,
the same way `test_policy_denial.py` itself imports them. Re-implementing any
of this would mean a second, drifting copy of the SPIRE-minting/streamable-HTTP
dance or the browser-driving dance; importing it is this project's own
established convention for exactly this situation.

**The anti-bypass pair (criteria 6 and 7) were written first**, per the plan's
own instruction, and verified capable of going red by mutation before this
file was finalised -- see this task's report for the mutations run (both
temporary edits to `platform/policy-decision-service/pds/rules.py`, redeployed
live, reverted after) and their measured RED/GREEN evidence. Neither
criterion's *fix* is new: Task 3's fix round already shipped the caller-scoped
`EXECUTE_LIMITS` table this milestone's whole anti-bypass claim rests on. What
this file adds is the proof.

**Criterion 5's span link, exactly as measured (ruling R8) rather than as
originally sketched.** The plan's own sketch built a link from a
`SpanContext` with `span_id=0`; measured against the installed
`opentelemetry-sdk==1.44.0`, `Span._new_links()` drops such a link before any
exporter sees it (`settlement.py::_refusal_link`'s own docstring has the full
measurement). What shipped instead, and what this file asserts, is a real
`refusedSpanId` on the ledger's refusal record -- **settlement-agent's own A2A
server span**, active when the refusal was recorded, not the PDS's
`policy.decision` span (ruling R13; `settlement.py`, `store.go`'s own
`RefusedSpanID` doc comment). Precisely *which* of settlement-agent's several
`SpanKind.SERVER` spans that is (a2a-sdk 0.3.7 marks nearly all of its own
internal spans SERVER, not just the ASGI entry point) is itself a measured
fact, not an assumption -- see `SETTLEMENT_AGENT_REFUSAL_SPAN`'s own comment
below for what this file's first live run found and why an earlier draft's
guess (`test_settlement_agent_trace.py`'s own "POST /", built for a different
purpose) was wrong. `tests/harness/jaeger.py`'s `Span`/`Trace`
classes only track `CHILD_OF` references (all `assert_no_orphans`/
`assert_single_root` need) and drop every other reference type, including an
OTel span *link* -- so this file reads the raw Jaeger span dict directly for
that one assertion (`_raw_span_for`/`_span_links` below) rather than widening
that shared harness for one criterion's sake, keeping this file's footprint to
exactly what Task 5A was asked to touch.

**Reviewer-ui's own port (the fifth), and its skip/fail discriminator.**
Unlike the four ports `scripts/smoke-test.sh` already manages
(claims-agent/settlement-agent/payments-mcp/payments-api, each a plain
ClusterIP reached by an ephemeral `kubectl port-forward`), reviewer-ui's
Service is `NodePort` and `deploy/kind/stage3.yaml` maps host port 8081
straight to it -- so in the ordinary case this fixture finds it already alive
and starts nothing. `reviewer_ui_route` still mirrors
`test_policy_denial.py::claims_agent_route`'s adopt-if-up/establish-if-not/
tear-down-only-what-it-started shape for the case where that mapping is
somehow gone (a fresh kind cluster on a laptop that predates stage 3, say),
with the same two-tier skip/fail split
`test_gateway_is_the_only_route.py::policy_present` and
`test_gateway_config_drift.py`'s own fixture use: "not a stage-3 cluster at
all" (no `senior-settlement-agent` Deployment -- a genuinely different object
than reviewer-ui itself) is a legitimate SKIP; reviewer-ui being unreachable
*despite* that discriminator being present is this file's own regression and
FAILS, never skips. Verified by mutation -- see the report.

**Reference traces are cited, not asserted against**, same rule
`test_policy_denial.py` states: every assertion below runs against a trace
this file itself causes on the run.

**Credential hygiene.** Criterion 7 mints a real JWT-SVID. It is minted at
runtime, never committed, and never printed; assertion messages carry decoded
claims and never a token.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import uuid
from html import escape
from types import SimpleNamespace

import pytest
import requests

from tests.harness.jaeger import JaegerClient, assert_no_orphans, assert_single_root

# Wholesale reuse, not reimplementation -- see the module docstring.
from tests.e2e.test_caller_identity import (  # noqa: F401 -- fixtures used by name
    CALLER_SPIFFE_ID,
    GATEWAY_AUDIENCE,
    _claims,
    call_tool_directly,
    gateway_url,
    mint_svid,
    valid_svid,
)
from tests.e2e.test_policy_denial import (  # noqa: F401 -- fixtures used by name
    ALL_SERVICES,
    ATTR_DECISION,
    ATTR_POLICY_AMOUNT,
    ATTR_POLICY_CALLER,
    ATTR_POLICY_LIMIT,
    ATTR_POLICY_RULE,
    DENIED_AMOUNT,
    DENIED_CLAIM_ID,
    EXECUTE_TOOL,
    JAEGER_URL,
    PAYMENTS_API_URL,
    PROPOSE_TOOL,
    _decisions,
    _drive_browser,
    browser_test,
    claims_agent_route,
)

KUBE_CONTEXT = "kind-northstar"
AGENTS_NAMESPACE = "northstar-agents"

# M3's second privileged identity and its would-be attacker, both real
# workloads (deploy/k8s/senior-settlement-agent.yaml, deploy/k8s/reviewer-ui.yaml)
# whose SPIFFE ID is `spiffe://{trust domain}/ns/{namespace}/sa/{service account}`
# -- read here as a literal, the same way test_caller_identity.py's own
# CALLER_SPIFFE_ID is, rather than assembled, because there is nothing in this
# test to assemble it from.
SENIOR_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent"
REVIEWER_UI_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

# Every service the approved-execution trace must contain -- reviewer-ui's own
# root plus the same governed MCP path the denial already exercised, minus
# customer-ui/claims-agent (this flow never touches either: reviewer-ui calls
# senior-settlement-agent directly over A2A).
APPROVAL_SERVICES = {
    "reviewer-ui",
    "settlement-agent",
    "agentgateway",
    "policy-decision-service",
    "payments-mcp",
    "payments-api",
}

# The span active in settlement-agent's own process at the moment a refusal
# is recorded, and therefore what `refusedSpanId` actually carries -- MEASURED
# on this file's own first live run, not assumed from
# `test_settlement_agent_trace.py::ENTRY_SPAN` ("POST /"), which names a
# *different* span for a *different* purpose (proving inbound `traceparent`
# propagation into the ASGI layer, not identifying what is current deep
# inside the agent-execution task). `settlement.py::_record_refusal` runs
# after `settlement_agent.run()` returns, inside `AgentExecutor.execute()`,
# which a2a-sdk 0.3.7 runs as a background task under its OWN span --
# `DefaultRequestHandler._run_event_stream` -- a sibling of "POST /", not its
# child and not the same span. `agents/settlement-agent/NOTES.md` §3
# documents this whole family as `SpanKind.SERVER` (a2a-sdk decorates
# `DefaultRequestHandler`/`JSONRPCHandler` with `@trace_class(kind=
# SpanKind.SERVER)` on every method, not just the ASGI entry point), so "POST
# /" and `_run_event_stream` are both legitimately "settlement-agent's own A2A
# server span" in isolation -- but only one of them is the span
# `trace.get_current_span()` actually returns when the refusal is recorded,
# and the first run of this file measured that it is this one: the ledger's
# own `refusedSpanId` matched `_run_event_stream`'s id exactly, and did not
# match "POST /"'s.
SETTLEMENT_AGENT_REFUSAL_SPAN = (
    "a2a.server.request_handlers.default_request_handler."
    "DefaultRequestHandler._run_event_stream"
)
REVIEW_DECISION_SPAN = "review.decision"
EXECUTE_APPROVED_SPAN = "settlement.execute_approved"

# The reviewer name typed into the form -- reviewer-ui's own honesty rule
# treats it as a claim, not a verified credential (review.py's own docstring),
# so any non-empty string proves the same thing. Matches the worked example in
# docs/telemetry-contract.md's own attribute table, for continuity with the
# document a reader might have open beside this file -- with a per-run unique
# suffix. **Measured, not assumed:** payments-api's store is in-memory and its
# payment ids are sequential *within one process lifetime* (ruling 32), so a
# redeploy resets the counter -- this file's own first live run collided with
# a stale `review.decision` trace from an unrelated, much earlier manual test
# that happened to be tagged `review.payment.id=PAY-000004` too, because both
# runs were the fourth payment proposed since their respective pod's last
# restart. `review.payment.id` alone is therefore not the unique key
# `approval_run`'s own tag search assumed it was; the reviewer name -- unique
# per invocation of this file -- is the second half of that key.
REVIEWER_NAME_PREFIX = "alex.mercer"


def _kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _get_payment(payment_id: str) -> dict:
    """Read a payment straight back from the ledger it lives in -- the same
    honesty rule every other file in this suite follows: the ledger is the
    authority on status, amount and the refusal record, never the reply a
    settlement or an approval happened to carry."""
    response = requests.get(f"{PAYMENTS_API_URL}/payments/{payment_id}", timeout=10)
    assert response.status_code == 200, (
        f"could not read payment {payment_id} back from the ledger at "
        f"{PAYMENTS_API_URL} (HTTP {response.status_code}): {response.text[:300]}"
    )
    return response.json()


def _deny_marker(error: dict | None) -> dict:
    """The structured `{"decision": "deny", ...}` marker `mcp_tools._deny_marker`
    builds, read back off a JSON-RPC error exactly the way
    `test_policy_denial.py::test_criterion_5...` does: agentgateway parses the
    PDS's own JSON-RPC error object whole into `error.data`, so the marker can
    sit one level down or, if the gateway ever stops double-wrapping, at
    `error.data` itself. Both positions are checked."""
    assert error is not None, "expected a JSON-RPC error carrying a deny marker, got none"
    data = error.get("data")
    if isinstance(data, dict):
        for candidate in (data.get("data"), data):
            if isinstance(candidate, dict) and candidate.get("decision") == "deny":
                return candidate
    raise AssertionError(
        f"no structured deny marker on this JSON-RPC error, so the refusal "
        f"cannot be distinguished from a transport error or an outage: {error!r}"
    )


def _raw_span_for(trace, operation: str, service: str) -> dict:
    """The raw Jaeger span dict for the single span named `operation` in
    `service`. `tests/harness/jaeger.py`'s own `Span` dataclass drops every
    reference except `CHILD_OF` (all its own assertions need); reading the raw
    dict here is what makes the span-*link* assertion below possible without
    widening that shared harness for this one criterion."""
    service_of = {
        pid: proc.get("serviceName")
        for pid, proc in trace._raw.get("processes", {}).items()
    }
    matches = [
        s
        for s in trace._raw.get("spans", [])
        if s.get("operationName") == operation
        and service_of.get(s.get("processID")) == service
    ]
    assert len(matches) == 1, (
        f"expected exactly one {operation!r} span in service {service!r}, "
        f"found {len(matches)}: "
        f"{[(s.get('operationName'), service_of.get(s.get('processID'))) for s in trace._raw.get('spans', [])]}"
    )
    return matches[0]


def _span_links(raw_span: dict) -> list[tuple[str, str]]:
    """Every reference on `raw_span` that is not a `CHILD_OF` parent edge --
    i.e. an OTel span *link*, exported as an extra Jaeger reference the same
    way a parent is, just tagged differently. Returns `(traceID, spanID)`
    pairs exactly as Jaeger reports them: comparing one against a locally
    known id must normalise both sides first (`.lower().zfill(...)`), the same
    as every other cross-trace id comparison in this suite -- see
    `test_settlement_agent_trace.py`'s own comment on why Jaeger can drop a
    leading zero nibble."""
    return [
        (ref["traceID"], ref["spanID"])
        for ref in raw_span.get("references", [])
        if ref.get("refType") != "CHILD_OF"
    ]


def _norm_trace_id(value: str) -> str:
    return value.lower().zfill(32)


def _norm_span_id(value: str) -> str:
    return value.lower().zfill(16)


# --- the fifth port-forward --------------------------------------------------

REVIEWER_UI_URL = os.environ.get("REVIEWER_UI_URL", "http://localhost:8081")
REVIEWER_UI_SERVICE = "reviewer-ui"
# The stage-3 discriminator: a DIFFERENT object than reviewer-ui itself
# (Step 2's own mandate, and test_gateway_config_drift.py's pattern one
# milestone earlier). "no senior-settlement-agent Deployment" means this
# cluster genuinely predates M3 and there is nothing to say -- SKIP. Once that
# Deployment is confirmed present, reviewer-ui being unreachable is this
# file's own regression -- FAIL, never skip.
STAGE3_DISCRIMINATOR_DEPLOYMENT = "senior-settlement-agent"


def _reviewer_ui_alive() -> bool:
    try:
        requests.get(f"{REVIEWER_UI_URL}/healthz", timeout=3)
    except requests.RequestException:
        return False
    return True


def _stage3_workload_present() -> bool:
    proc = _kubectl(
        "-n", AGENTS_NAMESPACE, "get", "deployment",
        STAGE3_DISCRIMINATOR_DEPLOYMENT, "-o", "name",
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


@pytest.fixture(scope="module")
def reviewer_ui_route():
    """The route to reviewer-ui's own port -- adopted if already up
    (`deploy/kind/stage3.yaml`'s host-port mapping, the ordinary case),
    established here and torn down again if not. See the module docstring for
    the two-tier skip/fail discriminator this mirrors from
    `test_gateway_is_the_only_route.py::policy_present`.
    """
    # The cluster-reachability probe, ahead of and separate from the stage-3
    # discriminator below -- same split test_gateway_config_drift.py's
    # `live_gateway_config_text` makes. `_stage3_workload_present()` returns
    # `False` on ANY non-zero `kubectl get deployment` exit, which includes an
    # unreachable cluster, a wrong context or a missing `kubectl` binary, not
    # only a genuinely absent Deployment -- and the skip message below it
    # positively asserts "this is not a stage-3 cluster", which would be false
    # in exactly those cases. Checking `cluster-info` first, and skipping with
    # an honest reason when THAT is what's actually wrong, is what makes the
    # stage-3 skip message beneath it a true statement whenever it fires.
    reachable = _kubectl("cluster-info")
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'}); "
            "run ./scripts/setup-kind.sh --stage 3"
        )

    if not _stage3_workload_present():
        pytest.skip(
            f"no {STAGE3_DISCRIMINATOR_DEPLOYMENT} Deployment in "
            f"{AGENTS_NAMESPACE}, so this is not a stage-3 cluster and "
            "reviewer-ui was never deployed; run ./scripts/setup-kind.sh "
            "--stage 3 and deploy the M3 workloads"
        )

    if _reviewer_ui_alive():
        yield REVIEWER_UI_URL
        return

    port = urllib.parse.urlsplit(REVIEWER_UI_URL).port
    assert port, f"REVIEWER_UI_URL={REVIEWER_UI_URL!r} names no port"
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", AGENTS_NAMESPACE,
            "port-forward", f"svc/{REVIEWER_UI_SERVICE}", f"{port}:8080",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _reviewer_ui_alive():
                break
            time.sleep(1)
        else:
            raise AssertionError(
                f"{STAGE3_DISCRIMINATOR_DEPLOYMENT} exists in {AGENTS_NAMESPACE} "
                "(this IS a stage-3 cluster), but reviewer-ui could not be "
                f"reached at {REVIEWER_UI_URL} within 30s after starting a "
                "port-forward -- is deployment/reviewer-ui healthy? (this "
                "file needs the live cluster and fails rather than skipping "
                "once stage 3 is confirmed present)"
            )
        yield REVIEWER_UI_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# --- shared fixtures: one denial, one approval -------------------------------


@pytest.fixture(scope="module")
def denial_run(claims_agent_route):
    """CLM-1842 at the page's own pre-filled EUR 6,200, refused -- M3's own
    instance of the same run `test_policy_denial.py::denial_run` drives, kept
    as its own fixture rather than imported as an instance: pytest scopes a
    module-scoped fixture per requesting module regardless, so there is no
    cost shared by importing that name specifically, and criterion 1 is this
    milestone's own regression claim on the same promise.

    Feeds criteria 1 and 2 directly, and criteria 3-6 via its payment id and
    its settlement-agent entry span.
    """
    return _drive_browser(DENIED_CLAIM_ID, None)


@pytest.fixture(scope="module")
def approval_run(denial_run, reviewer_ui_route):
    """Approve `denial_run`'s refused payment through reviewer-ui's own page,
    in a real browser, then wait for the resulting approved-execution trace --
    rooted at reviewer-ui's own `review.decision` span, per
    docs/telemetry-contract.md's "reviewer-ui's own instrumentation" section --
    to land completely in Jaeger.

    **Finding the new trace is necessarily by tag search (A14's own carve-out:
    "if the id genuinely cannot be known").** Unlike customer-ui, reviewer-ui
    injects no client-side `traceparent` capture -- there is no JavaScript
    here at all (bootstrap.py's own docstring: no FastAPI/Starlette
    auto-instrumentation, no client-side SDK), so this is a plain server-
    rendered form POST and the trace id is assigned server-side, inside
    `review.py::decide`, where nothing in this test can observe it directly.

    **The search key is `(review.payment.id, review.reviewer.id)`, not
    `review.payment.id` alone -- measured, not assumed.** This file's own
    first live run collided with a stale `review.decision` trace from an
    unrelated, much earlier manual test tagged `review.payment.id=PAY-000004`:
    payments-api's ids are sequential only *within one process lifetime*
    (ruling 32), so a redeploy resets the counter, and two different pod
    generations can both propose their own fourth payment. A genuinely unique
    reviewer name (`REVIEWER_NAME_PREFIX` plus a fresh uuid, generated once per
    invocation of this fixture) makes the compound key safe regardless of how
    many times the ledger has been reset.
    """
    from playwright.sync_api import sync_playwright

    payment_id = denial_run.reply.get("paymentId")
    assert payment_id, f"denial_run produced no paymentId to approve: {denial_run.reply}"
    reviewer_id = f"{REVIEWER_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(reviewer_ui_route)

        form = page.locator(f'form[action="/review/{payment_id}"]')
        assert form.count() == 1, (
            f"expected exactly one review form for payment {payment_id!r} on "
            "reviewer-ui's own list page; found "
            f"{form.count()} -- either the refusal was never recorded, or "
            "the list route isn't reading it (criterion 2's own claim)"
        )
        form.locator('input[name="reviewer_id"]').fill(reviewer_id)
        form.locator("button.approve").click()
        page.wait_for_selector("text=Review recorded", timeout=30_000)
        result_text = page.locator("body").inner_text()
        browser.close()

    client = JaegerClient(JAEGER_URL)
    # First contact: find the trace by its own unique compound tag (A14's
    # carve-out).
    review_trace = client.wait_for_trace(
        service="reviewer-ui",
        operation=REVIEW_DECISION_SPAN,
        tags={"review.payment.id": payment_id, "review.reviewer.id": reviewer_id},
        timeout=60,
    )
    # Then wait for the WHOLE chain -- reviewer-ui down through payments-api --
    # to land and hold steady (A20/A20a), reading the completed trace back by
    # the id this just gave us rather than trusting that first, possibly
    # partial, fetch.
    trace = client.wait_for_trace_id(
        review_trace.trace_id,
        operation=EXECUTE_APPROVED_SPAN,
        timeout=90,
        services=APPROVAL_SERVICES,
    )

    # Sanity on the shape the spec itself claims (§6): review.decision really
    # is this trace's own root, and the trace is otherwise as intact as the
    # refusing one's (same definition test_policy_denial.py's criteria 3 and 6
    # use).
    assert_single_root(trace)
    assert_no_orphans(trace)
    root = trace.roots()[0]
    assert (root.service, root.operation) == ("reviewer-ui", REVIEW_DECISION_SPAN), (
        "the approved-execution trace's root is not reviewer-ui's own "
        f"review.decision span: {root.service}:{root.operation}"
    )

    ledger_record = _get_payment(payment_id)

    return SimpleNamespace(
        payment_id=payment_id,
        trace=trace,
        result_text=result_text,
        reviewer_id=reviewer_id,
        ledger_record=ledger_record,
    )


# --- Criterion 1 --------------------------------------------------------------


@browser_test
def test_criterion_1_clm_1842_is_refused_and_the_ledger_never_shows_it_executed(
    denial_run,
):
    """§7.1: "CLM-1842 submitted in the browser is refused exactly as in
    M2.2 -- no payment executed, ledger still unexecuted. A regression test
    on M2's promise."

    **Why this asserts `status != "executed"` rather than `status ==
    "proposed"`.** M2.2's own `test_policy_denial.py` asserts the latter,
    because in M2.2 a denial left the ledger record exactly where propose put
    it. M3 changed that: `settlement.py::decide_settlement` now calls
    `_record_refusal` on every policy denial, which POSTs
    `/payments/{id}/refuse` and moves the record to `status: "refused"` --
    still a fact the criterion's own words cover ("ledger still unexecuted"),
    but a different literal string than M2.2's own assertion checks for. This
    test asserts what the criterion actually says, not what M2.2 happened to
    observe before this milestone added a third status; asserting `==
    "proposed"` here would be testing a fact this milestone deliberately
    changed.

    **`test_policy_denial.py`'s parallel assertion has since been repaired**
    (M3 Task 5B, ruling R28) and now reads `== "refused"`, so the sentence
    above describes M2.2's version of that file rather than today's. The two
    assertions are deliberately different and both are kept: that one pins the
    exact status M3 produces, which is the stronger statement and the one that
    also catches `_record_refusal` silently failing; this one pins the weaker
    property the criterion's own words claim ("ledger still unexecuted"), so a
    future milestone that legitimately renames or re-routes the terminal
    status for a denial does not falsify this regression test on M2's promise.
    """
    reply = denial_run.reply
    trace = denial_run.trace

    # The whole governed chain is present -- this milestone changes the
    # ledger's terminal status for a denial, not the chain that produces it.
    assert ALL_SERVICES <= trace.services(), (
        f"missing services: {sorted(ALL_SERVICES - trace.services())}"
    )

    assert reply.get("claimId") == DENIED_CLAIM_ID, reply
    assert reply.get("status") == "refused", reply
    assert reply.get("amount") == DENIED_AMOUNT, reply
    payment_id = reply.get("paymentId")
    assert payment_id, f"the proposal should still have been recorded: {reply}"

    # Requested exactly once, and the platform denied it under the execute
    # limit -- the PDS's own span, not the refusal prose.
    decisions = _decisions(trace, EXECUTE_TOOL)
    assert len(decisions) == 1, (
        f"expected exactly one policy.decision span for {EXECUTE_TOOL}, got "
        f"{len(decisions)}"
    )
    decision = decisions[0]
    assert decision.attrs.get(ATTR_DECISION) == "deny", decision.attrs
    assert (
        decision.attrs.get(ATTR_POLICY_RULE) == "settlement-agent.assisted.execute_limit"
    ), decision.attrs
    assert decision.attrs.get(ATTR_POLICY_LIMIT) == 6000, decision.attrs
    assert decision.attrs.get(ATTR_POLICY_AMOUNT) == 6200, decision.attrs

    # And the ledger -- the primary evidence, read straight from the service
    # that owns it -- never shows this payment executed.
    record = _get_payment(payment_id)
    assert record.get("status") != "executed", (
        "THE PAYMENT EXECUTED DESPITE THE DENIAL -- payments-api's own "
        f"record for {payment_id} reads {record.get('status')!r}: {record}"
    )
    assert record.get("amount") == DENIED_AMOUNT, record
    assert record.get("claimId") == DENIED_CLAIM_ID, record


# --- Criterion 2 --------------------------------------------------------------


@browser_test
def test_criterion_2_the_refusal_appears_in_reviewer_ui_with_rule_limit_requested_and_narrative(
    denial_run, reviewer_ui_route,
):
    """§7.2: "The refusal appears in reviewer-ui showing rule, limit,
    requested amount and the agent's narrative -- all read from the ledger."

    The ledger record is the expected value, fetched fresh (not derived from
    `denial_run.reply`, which carries none of `rule`/`limit`/`requested` --
    only the ledger's own refusal sub-object does), and reviewer-ui's own
    rendered HTML is checked for a byte match of each figure, `html.escape`d
    the same way `render.py::_payment_card` escapes them, so a mismatch here
    means the page truly isn't showing what the ledger recorded rather than
    this test comparing two different escapings of the same string.

    The specific card for this payment is located rather than the whole page
    grepped, because the ledger is a long-running, in-memory store shared
    across every task that has ever driven a settlement against this cluster
    (ruling R23: `PAY-000053` and its Jaeger traces are exactly that kind of
    stale leftover) -- a bare substring match on `rule`/`limit`/`requested`
    could be satisfied by a stranger's card on the same page.
    """
    payment_id = denial_run.reply["paymentId"]
    record = _get_payment(payment_id)
    assert record.get("status") == "refused", (
        f"payment {payment_id} is not status=refused in the ledger "
        f"({record.get('status')!r}); there is no refusal yet for "
        f"reviewer-ui to show: {record}"
    )
    refusal = record.get("refusal") or {}
    for field in ("rule", "limit", "requested", "narrative"):
        assert refusal.get(field) not in (None, ""), (
            f"the ledger's own refusal record for {payment_id} carries no "
            f"{field!r}, so reviewer-ui cannot be showing it either: {refusal}"
        )

    listing = requests.get(reviewer_ui_route, timeout=10)
    assert listing.status_code == 200, (
        f"reviewer-ui's list page returned HTTP {listing.status_code}: "
        f"{listing.text[:300]}"
    )
    html = listing.text

    idx = html.find(payment_id)
    assert idx != -1, (
        f"reviewer-ui's own listing page names no payment {payment_id!r} at "
        "all -- either the refusal was never recorded, or the list route "
        f"isn't reading it. Page (truncated): {html[:1000]!r}"
    )
    card_start = html.rfind('<div class="card">', 0, idx)
    assert card_start != -1, (
        f"found {payment_id!r} in the page but outside any '.card' block "
        f"(index {idx}); render.py's own layout may have changed"
    )
    next_start = html.find('<div class="card">', idx + 1)
    card = html[card_start: next_start if next_start != -1 else len(html)]

    # Each figure is checked as its OWN labeled fragment
    # (`render.py::_payment_card`'s literal "rule <span ...>", "limit
    # <span ...>", "requested <span ...>" text), not as a bare value. A bare
    # `escape(str(refusal["requested"])) in card` check does not actually pin
    # down which field the page is showing: `_payment_card` also prints
    # `payment["amount"]` under its own "Amount requested" label, and
    # `refusal["requested"]` is always the same number as `payment["amount"]`
    # (settlement-agent records the refusal against the amount it was asked to
    # execute) -- so that bare check would still pass even if the "Policy
    # decision" line's own `requested` figure were deleted from the template
    # entirely. Anchoring each value to its own label is what makes this
    # assertion about the refusal record specifically, not about whichever
    # field on the card happens to carry the same number.
    assert f'rule <span class="figure">{escape(str(refusal["rule"]))}</span>' in card, (
        f"reviewer-ui's card for {payment_id} does not show the ledger's "
        f"rule {refusal['rule']!r} under its own 'rule' label:\n{card}"
    )
    assert f'limit <span class="figure">{escape(str(refusal["limit"]))}</span>' in card, (
        f"reviewer-ui's card for {payment_id} does not show the ledger's "
        f"limit {refusal['limit']!r} under its own 'limit' label:\n{card}"
    )
    assert (
        f'requested <span class="figure">{escape(str(refusal["requested"]))}</span>'
        in card
    ), (
        f"reviewer-ui's card for {payment_id} does not show the ledger's "
        f"requested amount {refusal['requested']!r} under its own 'requested' "
        f"label (a bare value match here could be satisfied by the card's "
        f"separate 'Amount requested' figure instead):\n{card}"
    )
    assert escape(str(refusal["narrative"])) in card, (
        f"reviewer-ui's card for {payment_id} does not show the agent's own "
        f"narrative: {refusal['narrative']!r}\n{card}"
    )


# --- Criterion 3 --------------------------------------------------------------


@browser_test
def test_criterion_3_a_human_approves_in_the_browser_and_the_payment_executes(
    approval_run,
):
    """§7.3: "A human approves in the browser; the payment executes; the
    ledger reads `executed` at the same amount, with the same payment id."

    The ledger read-back is the primary evidence, exactly as criterion 1's
    ledger check is for the denial -- the honesty rule this whole flow
    exists to keep applies just as much to the approval as to the refusal.
    The rendered result page is checked too, as corroboration: a human
    reviewer on stage sees this page, not the ledger's JSON.
    """
    record = approval_run.ledger_record
    assert record.get("paymentId") == approval_run.payment_id, record
    assert record.get("status") == "executed", (
        f"payment {approval_run.payment_id} was approved, but the ledger "
        f"reads {record.get('status')!r}, not 'executed': {record}"
    )
    assert record.get("amount") == DENIED_AMOUNT, (
        f"the executed amount does not match what was refused: {record}"
    )
    assert record.get("claimId") == DENIED_CLAIM_ID, record

    assert "Approved by" in approval_run.result_text, approval_run.result_text
    assert approval_run.payment_id in approval_run.result_text, approval_run.result_text
    # The success-branch phrase `render.py::result_page` prints ONLY when
    # `outcome.executed is not None` -- not a bare "executed" substring check,
    # which the failure branch also satisfies: that branch's own text is
    # "Approved by {reviewer}, but the execution did not happen ... was not
    # executed: ...", which contains the literal substring "executed" too. A
    # bare check here would pass on a failed execution just as readily as on
    # a real one; the ledger check above is still this test's primary
    # evidence, but this corroboration block should actually corroborate it.
    # `page.locator("body").inner_text()` (Playwright) returns rendered TEXT,
    # not markup, so the check is against the plain-text phrase the success
    # branch alone produces ("Payment ... is now executed for ..."), not
    # against `render.py`'s own `<span class="figure">` HTML.
    assert "is now executed" in approval_run.result_text, (
        "the rendered result page does not carry reviewer-ui's own "
        "success-branch phrase ('is now executed'), so this corroboration "
        "cannot tell a real execution from the 'did not happen' failure "
        f"branch, which also contains the word 'executed': {approval_run.result_text!r}"
    )
    assert "did not happen" not in approval_run.result_text, (
        "the result page shows the execution-did-not-happen branch: "
        f"{approval_run.result_text!r}"
    )


# --- Criterion 4 --------------------------------------------------------------


@browser_test
def test_criterion_4_the_pds_permits_under_the_same_rule_for_a_different_caller(
    approval_run,
):
    """§7.4: "The PDS permitted it under the same rule (`execute_limit`) with
    a different `policy.caller.spiffe_id` -- asserted from the spans, not
    narrated."

    The decisive comparison is against criterion 1's own denial decision:
    same rule name (`settlement-agent.assisted.execute_limit`), same
    requested amount (EUR 6,200), opposite `policy.decision`, and a
    `policy.caller.spiffe_id` that is senior-settlement-agent's own identity
    and explicitly *not* the junior's -- the spec's own "one span attribute"
    claim (§2), read here off the PDS's own span rather than taken on faith.
    """
    decisions = _decisions(approval_run.trace, EXECUTE_TOOL)
    assert len(decisions) == 1, (
        f"expected exactly one policy.decision span for {EXECUTE_TOOL} in "
        f"the approved-execution trace, got {len(decisions)}"
    )
    decision = decisions[0]
    assert decision.attrs.get(ATTR_DECISION) == "permit", decision.attrs
    assert (
        decision.attrs.get(ATTR_POLICY_RULE) == "settlement-agent.assisted.execute_limit"
    ), (
        "the permit came from some rule other than the execute limit, so "
        f"this does not demonstrate the same rule permitting: {decision.attrs}"
    )
    assert decision.attrs.get(ATTR_POLICY_CALLER) == SENIOR_SPIFFE_ID, (
        f"expected the senior identity's own SPIFFE ID on this decision: "
        f"{decision.attrs}"
    )
    assert decision.attrs.get(ATTR_POLICY_CALLER) != CALLER_SPIFFE_ID, (
        "the approved execution's own decision names the JUNIOR identity -- "
        "the approval did not change who is calling"
    )
    assert decision.attrs.get(ATTR_POLICY_LIMIT) == 25000, decision.attrs
    assert decision.attrs.get(ATTR_POLICY_AMOUNT) == DENIED_AMOUNT, decision.attrs


# --- Criterion 5 --------------------------------------------------------------


@browser_test
def test_criterion_5_the_approved_trace_links_to_the_refusal_and_review_decision_records_reviewer_and_elapsed(
    denial_run, approval_run,
):
    """§7.5: "The approved-execution trace carries a span link to the
    refusal trace, and `review.decision` records the reviewer and the
    elapsed time."

    **The link's target, exactly as measured (rulings R8/R13).** Not a
    `SpanContext(span_id=0)` (dropped by the SDK before export -- see the
    module docstring), and not the PDS's `policy.decision` span. It is
    `SETTLEMENT_AGENT_REFUSAL_SPAN` (`DefaultRequestHandler._run_event_stream`)
    from the *denial* trace -- the a2a-sdk span whose context is current when
    `AgentExecutor.execute()` actually runs, measured directly on this file's
    own first live run (see that constant's own comment) rather than assumed
    to be "POST /", the ASGI entry span a *different* test uses for a
    *different* purpose. Read here two ways and cross-checked against each
    other: once off the ledger's own `refusedSpanId` field, and once off the
    raw span link on `settlement.execute_approved` in the *approval* trace.
    Both must agree with the span Jaeger actually recorded for the denial
    run, or the "link" is decorative.
    """
    entry_span = denial_run.trace.span(
        SETTLEMENT_AGENT_REFUSAL_SPAN, service="settlement-agent"
    )

    ledger_record = _get_payment(approval_run.payment_id)
    refusal = ledger_record.get("refusal") or {}
    assert refusal.get("refusedTraceId"), (
        f"the executed payment's own refusal record carries no "
        f"refusedTraceId: {refusal}"
    )
    assert refusal.get("refusedSpanId"), (
        f"the executed payment's own refusal record carries no "
        f"refusedSpanId: {refusal}"
    )
    assert _norm_trace_id(refusal["refusedTraceId"]) == _norm_trace_id(denial_run.trace_id), (
        "the ledger's refusedTraceId does not name the denial run's own "
        f"trace: {refusal['refusedTraceId']!r} vs {denial_run.trace_id!r}"
    )
    assert _norm_span_id(refusal["refusedSpanId"]) == _norm_span_id(entry_span.span_id), (
        "the ledger's refusedSpanId does not name settlement-agent's own "
        f"_run_event_stream span from the denial run: "
        f"{refusal['refusedSpanId']!r} vs {entry_span.span_id!r} (ruling R13: "
        "it must be the span active when the refusal was recorded, not "
        "policy.decision)"
    )

    execute_approved_raw = _raw_span_for(
        approval_run.trace, EXECUTE_APPROVED_SPAN, "settlement-agent"
    )
    links = _span_links(execute_approved_raw)
    assert len(links) == 1, (
        f"expected exactly one span link on {EXECUTE_APPROVED_SPAN}, found "
        f"{len(links)}: {links}"
    )
    link_trace_id, link_span_id = links[0]
    assert _norm_trace_id(link_trace_id) == _norm_trace_id(denial_run.trace_id), (
        f"the approved execution's span link points at trace {link_trace_id!r}, "
        f"not the denial run's trace {denial_run.trace_id!r}"
    )
    assert _norm_span_id(link_span_id) == _norm_span_id(entry_span.span_id), (
        f"the approved execution's span link points at span {link_span_id!r}, "
        f"not settlement-agent's own entry span {entry_span.span_id!r} from "
        "the denial run"
    )

    review_span = approval_run.trace.span(REVIEW_DECISION_SPAN, service="reviewer-ui")
    assert review_span.attrs.get("review.payment.id") == approval_run.payment_id, (
        review_span.attrs
    )
    assert review_span.attrs.get("review.decision") == "approve", review_span.attrs
    assert review_span.attrs.get("review.reviewer.id") == approval_run.reviewer_id, (
        f"review.decision's own reviewer attribute does not match who "
        f"actually submitted the form: {review_span.attrs}"
    )
    assert review_span.attrs.get("review.reviewer.spiffe_id") == REVIEWER_UI_SPIFFE_ID, (
        review_span.attrs
    )
    assert _norm_trace_id(
        review_span.attrs.get("review.refused.trace_id", "")
    ) == _norm_trace_id(denial_run.trace_id), (
        f"review.decision's own review.refused.trace_id does not name the "
        f"denial run: {review_span.attrs}"
    )
    # `> 0`, not `>= 0`: the criterion says "records the elapsed time", and a
    # hardcoded `elapsed_ms = 0` would satisfy `>= 0` while proving nothing was
    # ever measured. `bool` is deliberately excluded even though it is an
    # `int` subclass in Python -- `isinstance(True, (int, float))` is `True`,
    # and this field is elapsed milliseconds, not a flag.
    elapsed = review_span.attrs.get("review.elapsed_ms")
    assert (
        isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and elapsed > 0
    ), (
        f"review.elapsed_ms is not a real, positive measured duration: {elapsed!r}"
    )


# --- Criterion 6 (anti-bypass) ------------------------------------------------


@browser_test
def test_criterion_6_the_junior_identity_still_cannot_execute_6200_after_the_approval(
    gateway_url, valid_svid, denial_run, approval_run,
):
    """§7.6, the anti-bypass criterion: "The junior identity still cannot
    execute EUR 6,200 after an approval exists. The approval must not have
    widened the policy for the original caller."

    Directly re-plays the exact call the denial made -- same payment id
    (now `status: executed`, approved and paid under the SENIOR identity),
    same amount, but presented with `valid_svid`: settlement-agent's own
    real, attested identity, minted the same way `test_caller_identity.py`'s
    own accepted-path test mints it. If the approval had widened anything
    for the original caller, this call would now succeed (or would be denied
    under some other, higher limit); it is deliberately checked against the
    *same* rule and the *same* EUR 6,000 limit the original denial carried,
    not merely "still refused by something".

    This is the same PDS guard criterion 1 depends on, restated after an
    approval exists rather than before one -- deliberately, since there is no
    separate "has this been approved" branch anywhere in the policy for it to
    consult (the spec's own point, §1: "there is no approval flag ... no
    'permitted because a human said so' input to the policy engine"). A
    mutation that widens the junior's own limit therefore reddens this test
    and criterion 1 together, which this task's report records as the
    measured mutation evidence for this criterion.
    """
    payment_id = denial_run.reply["paymentId"]
    assert payment_id == approval_run.payment_id, (
        "criterion 6 must probe the exact payment the approval executed, not "
        "a different one"
    )

    result = call_tool_directly(
        gateway_url,
        EXECUTE_TOOL,
        {"payment_id": payment_id, "amount": DENIED_AMOUNT, "currency": "EUR"},
        headers={"authorization": f"Bearer {valid_svid}"},
    )
    assert result.tool_result is None, (
        "THE JUNIOR IDENTITY EXECUTED A PAYMENT ITS OWN APPROVAL NEVER "
        f"WIDENED ITS ALLOWANCE FOR: {result!r}"
    )
    marker = _deny_marker(result.jsonrpc_error)
    assert marker.get("rule") == "settlement-agent.assisted.execute_limit", (
        "the junior was refused, but not under its own execute-limit rule -- "
        f"something else decided this: {marker!r}"
    )
    assert marker.get("limit") == 6000, (
        f"the junior's own limit is no longer 6000 -- the approval widened "
        f"it: {marker!r}"
    )
    assert marker.get("requested") == DENIED_AMOUNT, marker


# --- Criterion 7 (anti-bypass) ------------------------------------------------


@pytest.fixture(scope="module")
def reviewer_ui_svid(mint_svid):
    """A genuine SPIRE-minted JWT-SVID for reviewer-ui's own SPIFFE ID --
    real signing key, real issuer, differing from a real caller's SVID only
    in `sub`, exactly the way `test_policy_denial.py`'s own `stranger_svid`
    is built for a different stranger."""
    return mint_svid(GATEWAY_AUDIENCE, REVIEWER_UI_SPIFFE_ID)


def test_criterion_7_reviewer_uis_own_spiffe_id_cannot_execute_a_payment(
    gateway_url, reviewer_ui_svid,
):
    """§7.7: "`reviewer-ui`'s own SPIFFE ID cannot execute a payment
    (`unknown_caller`)."

    Not behind `RUN_BROWSER_TESTS=1`: like `test_policy_denial.py`'s own
    criterion 5, this needs no model call and drives no browser -- it speaks
    MCP to the gateway directly with a minted token, which is also the
    *stronger* test (reviewer-ui itself never attempts this; proving the
    identity has no allowance is a property of the PDS's own table, not of
    what reviewer-ui's code happens to do). `EXECUTE_LIMITS` is keyed by
    `(spiffe_id, autonomy_level)` (`pds/rules.py`), so an identity with no row
    at all is refused at any amount -- proposing first (unlimited for any
    caller) establishes a real payment id so the execute attempt is refused
    on identity alone, not on a missing payment.
    """
    assert _claims(reviewer_ui_svid).get("sub") == REVIEWER_UI_SPIFFE_ID, (
        "this test is only meaningful if the token's subject is really "
        f"reviewer-ui's own: {_claims(reviewer_ui_svid).get('sub')!r}"
    )

    claim_id = f"CLM-REVIEWERUI-{uuid.uuid4().hex[:8]}"
    proposed = call_tool_directly(
        gateway_url,
        PROPOSE_TOOL,
        {"claim_id": claim_id, "amount": 100, "currency": "EUR"},
        headers={"authorization": f"Bearer {reviewer_ui_svid}"},
    )
    assert not proposed.refused, (
        f"the gateway refused even the unlimited propose_payment call for "
        f"reviewer-ui's own identity, so this probe cannot reach the "
        f"execute decision it is about: {proposed!r}"
    )
    payment_id = (proposed.tool_result.get("structuredContent") or {}).get("paymentId")
    assert payment_id, f"no paymentId in the proposal: {proposed!r}"

    result = call_tool_directly(
        gateway_url,
        EXECUTE_TOOL,
        {"payment_id": payment_id, "amount": 100, "currency": "EUR"},
        headers={"authorization": f"Bearer {reviewer_ui_svid}"},
    )
    assert result.tool_result is None, (
        "REVIEWER-UI'S OWN IDENTITY EXECUTED A PAYMENT -- the surface that "
        f"approves payouts can now also make one: {result!r}"
    )
    marker = _deny_marker(result.jsonrpc_error)
    assert marker.get("rule") == "unknown_caller", (
        "reviewer-ui's identity was refused, but not for having no execute "
        f"allowance -- rule {marker.get('rule')!r}: {marker!r}"
    )
    assert marker.get("retryable") is False, marker
    assert marker.get("requested") == 100, marker
    assert marker.get("limit") is None, (
        f"an unknown caller has no allowance to exceed, so no limit should "
        f"be reported: {marker!r}"
    )
