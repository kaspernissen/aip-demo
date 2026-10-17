"""Asserts the browser owns the trace root — the top of the whole chain.

Chain under test: browser -> claims-agent -> settlement-agent -> payments-mcp
-> payments-api.

This is the CORS trap: if the collector or claims-agent rejects the
traceparent header, the browser's span and the backend chain end up in
separate traces and test_root_span_belongs_to_the_browser fails.

**Observes the real trace ID rather than searching for it.** This fixture
intercepts the real outbound POST from the browser to claims-agent
(Playwright's request-interception API) and reads the exact `traceparent`
header the browser actually sent, then looks the trace up by that ID with
`wait_for_trace_id` (never `find_trace`/`wait_for_trace`). This is strictly
sounder than a service+operation search: a positional/lookback search can
return a stale trace minutes old, which reading the ID directly off the wire
cannot do by construction.

**`wait_for_trace_id`'s `services` argument** waits for the whole chain to
land and hold steady for a quiet window past the BatchSpanProcessor export
delay before treating the trace as complete, across all five services:
settlement-agent's MCP client injects `params._meta`, so payments-mcp and
payments-api join this trace too (see agents/settlement-agent/NOTES.md
§10/§11 and tests/e2e/test_claim_correlation.py for what does and does not
cross that hop: the trace joins fully; the northstar.claim.id attribute does
not).

Requires a real browser. Skipped unless RUN_BROWSER_TESTS=1.

**Submits the form untouched rather than overwriting the claim id.**
`CLM-1842` is customer-ui's own pre-filled `DEFAULT_CLAIM`
(`apps/customer-ui/src/app/page.tsx`, amount 6200 EUR); this fixture submits
the form as-is, reads the "Outcome" `<pre>` block the page renders
(`JSON.stringify(result, null, 2)`, the same `ClaimResult` shape
`apps/customer-ui/src/a2a.ts` extracts from the A2A reply), and hands the
parsed payload to the tests below. This is a test assertion, not a guard
rail: nothing in `settlement.py` checks or clamps the amount, so if a live
model run ever proposes a different amount, the resulting assertion failure is
the finding, not something to be worked around here.

**The outcome of that submission is a refusal, not an execution** — €6,200
is above the €6,000 allowance the platform enforces on `execute_payment` for
this caller, so `test_the_browser_path_settles_the_expected_amount` below
asserts `status == "awaiting_approval"` (M8; "refused" before it) with €6,200 as the *proposed* amount. Everything
else in this file is about trace plumbing (root ownership, service presence,
orphans, the policy-number attribute) and is unaffected: the propose leg is
permitted unconditionally, so all five services still land in this one trace,
and a denied `execute_payment` leaves no dangling parent reference behind.
"""
import json
import os
import re
from types import SimpleNamespace

import pytest

from tests.harness import live
from tests.harness.jaeger import JaegerClient, assert_no_orphans, assert_single_root

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
UI_URL = os.environ.get("CUSTOMER_UI_URL", "http://localhost:8080")
CLAIMS_AGENT_URL = os.environ.get("CLAIMS_AGENT_URL", "http://localhost:19001")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="set RUN_BROWSER_TESTS=1 and install playwright to run browser tests",
)

TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-\d{2}$")


@pytest.fixture(scope="module")
def browser_trace():
    from playwright.sync_api import sync_playwright

    # CLM-1842 is customer-ui's own pre-filled DEFAULT_CLAIM
    # (apps/customer-ui/src/app/page.tsx) — submitted as-is, not overwritten
    # with a random id, so the outcome payload below can be asserted against
    # a known amount.
    claim_id = "CLM-1842"
    captured: dict[str, str] = {}
    outcome: dict[str, str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )

        def on_request(request):
            # The one outbound POST a2a.ts's sendClaim makes, straight to
            # claims-agent's root path. Reading its actual headers observes
            # what injectContext() produced, rather than assuming it.
            if request.method == "POST" and request.url.rstrip("/") == CLAIMS_AGENT_URL.rstrip(
                "/"
            ):
                # The FIRST POST is the claim stream (trace A). The page then
                # opens tasks/resubscribe under a second, linked root span
                # (trace B), which must not overwrite it.
                captured.setdefault("traceparent", request.headers.get("traceparent", ""))

        page.on("request", on_request)

        page.goto(UI_URL)
        # Submit the form untouched: CLM-1842 / POL-77120 / 6200 EUR are
        # already DEFAULT_CLAIM's values, so this is genuinely "the pre-filled
        # claim", not a value this test happens to also set.
        page.click("button[type=submit]")
        page.wait_for_selector("text=Outcome", timeout=120_000)
        # The Outcome <pre> renders JSON.stringify(result, null, 2) of the
        # exact ClaimResult apps/customer-ui/src/a2a.ts extracted from the A2A
        # reply. Located explicitly by its preceding <h2>Outcome</h2> sibling
        # rather than a positional `page.locator("pre").nth(1)` — page.tsx
        # renders both an <h2> and this <pre> as siblings of the same
        # Fragment, so the CSS adjacent-sibling combinator finds it directly
        # instead of assuming it is "the second <pre> on the page," which
        # would silently break if the page ever gained another <pre> before
        # this one.
        outcome["raw"] = page.locator("h2:text-is('Outcome') + pre").inner_text()
        # The browser's own WebTracerProvider uses a BatchSpanProcessor with
        # the OTel SDK's default 5s scheduled export delay (telemetry.ts sets
        # no override, same as every other service). The northstar.claim.submit span
        # ends the instant sendClaim's promise settles, well before that
        # timer next fires. Closing the browser tab immediately after
        # "Outcome" appears tears the page down before the export ever
        # ships, so the trace never gains its own root span — not a
        # propagation defect, just a torn-down exporter. Give the timer room
        # to fire before closing.
        page.wait_for_timeout(7_000)
        browser.close()

    cors_errors = [e for e in console_errors if "CORS" in e or "Access-Control" in e]
    assert not cors_errors, (
        "browser reported CORS errors; traceparent is being rejected: " f"{cors_errors}"
    )

    traceparent = captured.get("traceparent")
    assert traceparent, (
        "never observed a traceparent header on the outbound request to "
        f"claims-agent ({CLAIMS_AGENT_URL}); propagation.inject() produced "
        "nothing, or the request never reached the network layer"
    )
    match = TRACEPARENT_RE.match(traceparent)
    assert match, f"traceparent header {traceparent!r} does not match the W3C format"
    trace_id, span_id = match.group(1), match.group(2)

    # A claim routed to a human is waiting, and a waiting claim keeps its trace
    # open (a2a-sdk's `_run_event_stream` span and a 0.5 s polling span last as
    # long as the wait), so the trace would never hold steady. Decide it first;
    # `ledger_record` is what the ledger said while it waited.
    reply = json.loads(outcome["raw"])
    ledger_record = (
        live.release_wait(reply["paymentId"]) if reply.get("status") == "awaiting_approval" else None
    )

    client = JaegerClient(JAEGER_URL)
    # Waits for the whole chain to land and hold steady, not merely for one
    # span to appear, across all five services: settlement-agent's MCP
    # client injects params._meta (correlation.py), so payments-mcp and
    # payments-api join this trace too (traceparent crosses that hop
    # correctly — see test_all_five_services_are_in_one_trace below).
    trace = client.wait_for_trace_id(
        trace_id,
        operation="northstar.claim.submit",
        timeout=150,
        services={
            "customer-ui",
            "claims-agent",
            "settlement-agent",
            "payments-mcp",
            "payments-api",
        },
    )

    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, reply=reply,
        ledger_record=ledger_record,
    )


def test_browser_emits_the_claim_submit_span(browser_trace):
    """The fixture's own wait_for_trace_id(..., operation="northstar.claim.submit")
    already requires this span to exist before it returns, so this
    assertion can only ever restate what the fixture enforced. Kept anyway,
    same reasoning as
    test_claims_agent_and_settlement_agent_join_the_browsers_trace below: a
    named, specific failure if this ever regresses, not just a fixture-setup
    error shared across the whole file."""
    assert browser_trace.trace.has_span("northstar.claim.submit")


def test_root_span_belongs_to_the_browser(browser_trace):
    """This root is real — the browser's own northstar.claim.submit span genuinely
    lands in Jaeger, unlike a synthetic injected root — so it must be the
    trace's one and only root, and it must be the exact span whose ID was
    observed on the wire."""
    trace = browser_trace.trace
    roots = trace.roots()
    assert len(roots) == 1, f"expected one root, got {[(r.service, r.operation) for r in roots]}"
    assert roots[0].service == "customer-ui"
    assert roots[0].operation == "northstar.claim.submit"
    assert roots[0].span_id == browser_trace.span_id, (
        "the trace's root span id does not match the traceparent observed on "
        "the wire — Jaeger returned some other span's trace, not this request's"
    )


def test_claims_agent_and_settlement_agent_join_the_browsers_trace(browser_trace):
    """The two hops this file proves: browser -> claims-agent (the
    CORS-crossing hop) and claims-agent -> settlement-agent (already proven
    in tests/e2e/test_claims_chain_trace.py, reconfirmed here now that a
    genuine browser, not a raw HTTP client, originates the call).

    The fixture's own wait_for_trace_id(..., services={...}) already blocks
    until this exact set is present, so this assertion can only ever restate
    what the fixture enforced. Kept anyway as
    a documented, intentional redundancy: it gives a specific, readable
    failure message pinned to *this* test's name if that ever regresses,
    rather than only a fixture-setup error shared by every test in the file.
    """
    services = browser_trace.trace.services()
    expected = {"customer-ui", "claims-agent", "settlement-agent"}
    assert expected <= services, f"missing services: {sorted(expected - services)}"


def test_root_span_carries_the_policy_number(browser_trace):
    """northstar.claim.policy_number is documented in
    docs/telemetry-contract.md's owned-attribute table and set at
    apps/customer-ui/src/app/page.tsx:39; northstar.claim.id is asserted five
    ways elsewhere in this suite, this attribute otherwise zero. The
    fixture only fills the claim-id input (page.fill("input >> nth=0", ...)),
    leaving the policy number at its DEFAULT_CLAIM default, so this asserts
    the exact known value rather than merely "is present"."""
    root = browser_trace.trace.roots()[0]
    assert root.attrs.get("northstar.claim.policy_number") == "POL-77120"


def test_trace_has_no_orphans(browser_trace):
    """This root is real, so the no-orphans check applies unweakened."""
    assert_no_orphans(browser_trace.trace)


def test_trace_has_a_single_root(browser_trace):
    """Unweakened, same reasoning as test_root_span_belongs_to_the_browser."""
    assert_single_root(browser_trace.trace)


def test_all_five_services_are_in_one_trace(browser_trace):
    """settlement-agent's MCP client (mcp_tools.py) injects params._meta via
    the lower-level ClientSession.send_request() API, so all five services
    — customer-ui, claims-agent, settlement-agent, payments-mcp,
    payments-api — land in the one trace rooted at the browser's own
    northstar.claim.submit span.

    The fixture's own wait_for_trace_id(..., services={...}) already blocks
    until this exact five-service set is present, so — like
    test_browser_emits_the_claim_submit_span and
    test_claims_agent_and_settlement_agent_join_the_browsers_trace above —
    this assertion can only ever restate what the fixture enforced. Kept
    anyway for the same reason those two are: a named, specific failure if
    this ever regresses, not just a fixture-setup error shared across the
    whole file."""
    expected = {
        "customer-ui",
        "claims-agent",
        "settlement-agent",
        "payments-mcp",
        "payments-api",
    }
    services = browser_trace.trace.services()
    assert expected <= services, f"missing services: {sorted(expected - services)}"


def test_the_browser_path_settles_the_expected_amount(browser_trace):
    """The browser path — the one actually driven on stage — is checked
    against the outcome, not just the trace shape. CLM-1842 at €6,200 is
    customer-ui's own pre-filled `DEFAULT_CLAIM`, submitted untouched, so
    this is the outcome a visitor to the page actually gets.

    The platform denies `execute_payment` above the €6,000 allowance for
    this caller (`platform/policy-decision-service/pds/rules.py`), so the
    reply comes back `refused`: this test asserts *what does the canonical
    pre-filled claim actually do when a real browser submits it?*

    €6,200 is still asserted, and it is still the same number: on a refusal
    `settlement.py::decide_settlement` returns the amount the **ledger
    recorded for the proposal**, verbatim, not model output and not a
    placeholder (the propose leg is permitted unconditionally —
    `UNLIMITED_TOOLS` — so the proposal really is on the books; only the
    execution was stopped). Asserting it here is therefore still a real
    claim about the money, not a leftover: a refusal that reported a
    different figure, or none, would fail this.

    Still a test assertion, not a guard rail. Nothing in `settlement.py`
    clamps the amount; the ceiling lives in the platform, one hop away. If a
    live model run ever proposes a different amount, this assertion fails and
    that is the finding to record; nothing here works around it.

    Deliberately *not* asserted here: why it was refused. This test owns the
    browser-visible outcome and nothing more. The decision itself — the PDS's
    own `northstar.policy.decision` span, the rule name, the allowance and the requested
    amount on it, and the absence of any execute span at payments-api — is
    asserted from the platform's own telemetry by
    `tests/e2e/test_policy_denial.py::test_criterion_1_clm_1842_is_denied_in_the_browser_and_no_payment_executes`,
    which is where a claim that strong belongs.
    """
    reply = browser_trace.reply
    assert reply.get("status") == "awaiting_approval", reply
    assert reply.get("amount") == 6200.0, reply
