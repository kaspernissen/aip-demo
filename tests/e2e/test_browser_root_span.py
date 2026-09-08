"""Asserts the browser owns the trace root — the top of the whole M1.1 chain.

Chain under test: browser -> claims-agent -> settlement-agent -> payments-mcp
-> payments-api.

This is the CORS trap: if the collector or claims-agent rejects the
traceparent header, the browser's span and the backend chain end up in
separate traces and test_root_span_belongs_to_the_browser fails.

**A14, applied the sound way rather than by search.** A14 permits `find_trace`
only where the trace ID genuinely cannot be known up front, and names this
task as one such case — but only as a fallback. Here observation *is*
possible: this fixture intercepts the real outbound POST from the browser to
claims-agent (Playwright's request-interception API) and reads the exact
`traceparent` header the browser actually sent, then looks the trace up by
that ID with `wait_for_trace_id` (never `find_trace`/`wait_for_trace`). This
is strictly sounder than a service+operation search: two reviewers elsewhere
in this milestone proved `find_trace` can return a stale trace minutes old,
which this approach cannot do by construction.

**A20/A20a in use**: `wait_for_trace_id`'s `services` argument (Task 6's
harness addition) waits for the whole chain to land and hold steady for the
10s quiet window past the 5s BatchSpanProcessor export delay before treating
the trace as complete. Task 7 widens this from the three reachable services
(customer-ui, claims-agent, settlement-agent) to all five — settlement-agent's
MCP client now injects `params._meta`, so payments-mcp and payments-api join
this trace too (see agents/settlement-agent/NOTES.md §10/§11 and
tests/e2e/test_claim_correlation.py for what does and does not cross that
hop: the trace joins fully; the northstar.claim.id attribute does not).

Requires a real browser. Skipped unless RUN_BROWSER_TESTS=1.

**Task 5 (Task 4 review, Finding 2):** earlier revisions of this fixture
submitted a random `CLM-TEST-*` claim id and never read the reply payload, so
the browser path — the one actually driven on stage — asserted nothing about
the settlement outcome. `CLM-1842` is customer-ui's own pre-filled
`DEFAULT_CLAIM` (`apps/customer-ui/src/app/page.tsx`, amount 6200 EUR); this
fixture now submits the form untouched rather than overwriting the claim id,
reads the "Outcome" `<pre>` block the page renders
(`JSON.stringify(result, null, 2)`, the same `ClaimResult` shape
`apps/customer-ui/src/a2a.ts` extracts from the A2A reply), and hands the
parsed payload to the tests below. This is a test assertion, not a guard
rail: nothing in `settlement.py` checks or clamps the amount — M1 is
deliberately ungoverned — so if a live model run ever settles a different
amount, the resulting assertion failure is the finding, not something to be
worked around here.
"""
import json
import os
import re
from types import SimpleNamespace

import pytest

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
    # a known amount (Task 4 review, Finding 2).
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
            # claims-agent's root path. Reading its actual headers is the
            # observation A14 asks for — not an assumption about what
            # injectContext() produced.
            if request.method == "POST" and request.url.rstrip("/") == CLAIMS_AGENT_URL.rstrip(
                "/"
            ):
                captured["traceparent"] = request.headers.get("traceparent", "")

        page.on("request", on_request)

        page.goto(UI_URL)
        # Submit the form untouched: CLM-1842 / POL-77120 / 6200 EUR are
        # already DEFAULT_CLAIM's values, so this is genuinely "the pre-filled
        # claim", not a value this test happens to also set.
        page.click("button[type=submit]")
        page.wait_for_selector("text=Outcome", timeout=120_000)
        # The Outcome <pre> renders JSON.stringify(result, null, 2) of the
        # exact ClaimResult apps/customer-ui/src/a2a.ts extracted from the A2A
        # reply — the "Progress" <pre> above it is the first of the two, so
        # this is the second.
        outcome["raw"] = page.locator("pre").nth(1).inner_text()
        # The browser's own WebTracerProvider uses a BatchSpanProcessor with
        # the OTel SDK's default 5s scheduled export delay (telemetry.ts sets
        # no override, same as every other service — A20a). The claim.submit
        # span ends the instant sendClaim's promise settles, well before that
        # timer next fires. Closing the browser tab immediately after
        # "Outcome" appears (as an earlier version of this test did) tears
        # the page down before the export ever ships, so the trace never
        # gains its own root span — not a propagation defect, just a torn-down
        # exporter. Give the timer room to fire before closing.
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

    client = JaegerClient(JAEGER_URL)
    # A20: wait for the whole chain to land and hold steady, not merely for
    # one span to appear. Task 7 widens this from the three reachable
    # services (N7, task-6-review.md) to all five: settlement-agent's MCP
    # client now injects params._meta (correlation.py), so payments-mcp and
    # payments-api join this trace too (traceparent crosses that hop
    # correctly — see test_all_five_services_are_in_one_trace below).
    trace = client.wait_for_trace_id(
        trace_id,
        operation="claim.submit",
        timeout=150,
        services={
            "customer-ui",
            "claims-agent",
            "settlement-agent",
            "payments-mcp",
            "payments-api",
        },
    )

    reply = json.loads(outcome["raw"])

    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, reply=reply
    )


def test_browser_emits_the_claim_submit_span(browser_trace):
    """N8 (task-6-review.md): the fixture's own wait_for_trace_id(...,
    operation="claim.submit") already requires this span to exist before it
    returns, so this assertion can only ever restate what the fixture
    enforced. Kept anyway, same reasoning as
    test_claims_agent_and_settlement_agent_join_the_browsers_trace below: a
    named, specific failure if this ever regresses, not just a fixture-setup
    error shared across the whole file."""
    assert browser_trace.trace.has_span("claim.submit")


def test_root_span_belongs_to_the_browser(browser_trace):
    """A17: this root is real — the browser's own claim.submit span genuinely
    lands in Jaeger, unlike the synthetic injected roots used elsewhere in
    this milestone (A14/A17) — so it must be the trace's one and only root,
    and it must be the exact span whose ID was observed on the wire."""
    trace = browser_trace.trace
    roots = trace.roots()
    assert len(roots) == 1, f"expected one root, got {[(r.service, r.operation) for r in roots]}"
    assert roots[0].service == "customer-ui"
    assert roots[0].operation == "claim.submit"
    assert roots[0].span_id == browser_trace.span_id, (
        "the trace's root span id does not match the traceparent observed on "
        "the wire — Jaeger returned some other span's trace, not this request's"
    )


def test_claims_agent_and_settlement_agent_join_the_browsers_trace(browser_trace):
    """The two real hops this task can prove today: browser -> claims-agent
    (this task's new CORS-crossing hop) and claims-agent -> settlement-agent
    (already proven in tests/e2e/test_claims_chain_trace.py, reconfirmed here
    now that a genuine browser, not a raw HTTP client, originates the call).

    N8 (task-6-review.md): the fixture's own wait_for_trace_id(...,
    services={...}) already blocks until this exact set is present, so this
    assertion can only ever restate what the fixture enforced. Kept anyway as
    a documented, intentional redundancy: it gives a specific, readable
    failure message pinned to *this* test's name if that ever regresses,
    rather than only a fixture-setup error shared by every test in the file.
    """
    services = browser_trace.trace.services()
    expected = {"customer-ui", "claims-agent", "settlement-agent"}
    assert expected <= services, f"missing services: {sorted(expected - services)}"


def test_root_span_carries_the_policy_number(browser_trace):
    """N10 (final-review.md): northstar.policy.number is documented in
    docs/telemetry-contract.md's owned-attribute table and set at
    apps/customer-ui/src/app/page.tsx:39, but was asserted by no test —
    northstar.claim.id is asserted five ways, this attribute zero. The
    fixture only fills the claim-id input (page.fill("input >> nth=0", ...)),
    leaving the policy number at its DEFAULT_CLAIM default, so this asserts
    the exact known value rather than merely "is present"."""
    root = browser_trace.trace.roots()[0]
    assert root.attrs.get("northstar.policy.number") == "POL-77120"


def test_trace_has_no_orphans(browser_trace):
    """A17: unweakened — this root is real, so no exception is carved out."""
    assert_no_orphans(browser_trace.trace)


def test_trace_has_a_single_root(browser_trace):
    """A17: unweakened — same reasoning as test_root_span_belongs_to_the_browser."""
    assert_single_root(browser_trace.trace)


def test_all_five_services_are_in_one_trace(browser_trace):
    """Task 7 flips this from xfail(strict=True): settlement-agent's MCP
    client (mcp_tools.py) now injects params._meta via the lower-level
    ClientSession.send_request() API, closing the last broken hop. Confirmed
    by capture: all five services — customer-ui, claims-agent,
    settlement-agent, payments-mcp, payments-api — land in the one trace
    rooted at the browser's own claim.submit span.

    N14 (final-review.md): the fixture's own wait_for_trace_id(...,
    services={...}) already blocks until this exact five-service set is
    present, so — like test_browser_emits_the_claim_submit_span and
    test_claims_agent_and_settlement_agent_join_the_browsers_trace above —
    this assertion can only ever restate what the fixture enforced. Kept
    anyway for the same reason those two are: a named, specific failure if
    this ever regresses, not just a fixture-setup error shared across the
    whole file. Task 7 added this test without this note; added now so the
    file's own three redundant-by-construction assertions are documented
    consistently."""
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
    """Task 5 (Task 4 review, Finding 2): the browser path — the one actually
    driven on stage — is now checked against the outcome, not just the trace
    shape. CLM-1842 is the exact claim `test_settlement_agent_trace.py:219`
    and `test_claims_chain_trace.py:219` already assert settles at €6,200
    through the A2A and MCP paths respectively; this is the same claim, same
    amount, now proven through the browser too.

    This is a test assertion, not a guard rail: `settlement.py` has no amount
    check, clamp or threshold anywhere — M1 is deliberately ungoverned, the
    model can propose any amount and nothing stops it. If a live model run
    ever settles a different amount, this assertion fails and that is the
    finding to record; nothing here works around it.
    """
    reply = browser_trace.reply
    assert reply.get("status") == "executed", reply
    assert reply.get("amount") == 6200.0, reply
