"""M8: the agent prepares, a human releases. Live, stage 7.

Task 1 covers the ledger and the release; Task 2 adds the A2A task states and
the stream; Task 3 adds trace B and the histogram.

The dial is maker-checker. At the resting `assisted` level the settlement agent
may release nothing, so a claim is left `awaiting_approval` and a human decides
it through the reviewer-ui's JSON route. An approval is released by the
reviewer-ui itself, through agentgateway, under its own SPIFFE ID, and the PDS
permits it only because it reads `awaiting_approval` from the ledger. A
rejection is final.

This module spends one model settlement per claim fixture and, for the release,
moves a real EUR 5,800 in the demo ledger. It is gated like every live module
that drives a settlement: `RUN_BROWSER_TESTS=1`, and it FAILS rather than skips
when the cluster is absent.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import httpx
import pytest

from tests.harness import contract, live
from tests.harness.jaeger import JaegerClient, Trace

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live stage-7 cluster, spends one settlement's worth of "
        "model tokens per claim and releases a real payment as the reviewer-ui; "
        "gated behind the same RUN_BROWSER_TESTS=1 flag "
        "scripts/smoke-test.sh sets unconditionally"
    ),
)

REVIEWER = "alex.mercer"


def _reviewer_id() -> str:
    """One reviewer id per decision. Payment ids restart when the ledger does, so a
    Jaeger lookup by `northstar.review.payment.id` can find an older run's trace B; a uuid
    reviewer id cannot (test_evidence_view.py does the same)."""
    return f"{REVIEWER}-{uuid.uuid4().hex[:8]}"
REVIEWER_UI_SPIFFE_ID = live.REVIEWER_UI_SPIFFE_ID
AMOUNT = 5800.0


def _claim(amount: float) -> dict:
    contract.assert_assisted("before a maker-checker claim")
    return _claim_at_current_level(amount)


def _claim_at_current_level(amount: float) -> dict:
    """File a claim; once, and only when the model provider was unreachable.

    M8 gate run 4: one claim's task failed with no payment because settlement-
    agent's DNS lookup of api.anthropic.com failed (three spans, ~1.5 s, one
    claim in the whole run). That is outside this repo and these tests are not
    about it, so a task that failed that way is filed again under a new id. Any
    other failure is returned as it is and fails the test that asked.
    """
    for attempt in (1, 2):
        claim_id = f"CLM-HITL-{uuid.uuid4().hex[:8]}"
        with live.claims_agent_route() as url:
            run = live.submit_claim(claim_id, amount, claims_agent_url=url)
        if attempt == 2 or not live.is_upstream_model_failure(run):
            return {"claim_id": claim_id, **run}


@pytest.fixture(scope="module")
def approved():
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    # Read BEFORE the approval, so the histogram test can ask whether this
    # approval added a sample rather than whether any sample ever existed.
    waits_before = live.prometheus_sum(APPROVED_WAIT_COUNT)
    reviewer = _reviewer_id()
    return {**run, "payment_id": payment_id, "reviewer": reviewer, "waits_before": waits_before,
            "review": live.review(payment_id, "approve", reviewer)}


@pytest.fixture(scope="module")
def rejected():
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    reviewer = _reviewer_id()
    return {**run, "payment_id": payment_id, "reviewer": reviewer,
            "review": live.review(payment_id, "reject", reviewer)}


def test_assisted_5800_waits_with_rule_and_a_zero_limit(approved):
    # The ledger keeps the refusal beside the payment; the task's own reply
    # carries the same rule and limit for the customer.
    assert approved["reply"]["rule"] == "settlement-agent.assisted.execute_limit", approved["reply"]
    assert approved["reply"]["limit"] == 0.0, approved["reply"]
    refusal = live.get_payment(approved["payment_id"])["refusal"]
    assert refusal["rule"] == "settlement-agent.assisted.execute_limit", refusal
    assert refusal["limit"] == 0.0, refusal
    assert refusal["requested"] == AMOUNT, refusal


def test_assisted_5800_is_input_required_then_approve_completes_it(approved):
    assert approved["state"] == "input-required"
    assert approved["review"]["resumedState"] is not None, approved["review"]
    with live.claims_agent_route():
        task = live.get_task(approved["task_id"])
    assert task["state"] == "completed"
    assert task["reply"]["status"] == "executed" and task["reply"]["decision"] == "approved"


def test_reject_completes_the_task_declined(rejected):
    assert rejected["review"]["resumedState"] is not None, rejected["review"]
    with live.claims_agent_route():
        task = live.get_task(rejected["task_id"])
    assert task["state"] == "completed" and task["reply"]["status"] == "rejected"
    assert task["reply"]["decision"] == "rejected"


def test_autonomous_5800_completes_with_no_wait_and_6200_waits():
    with live.autonomy(contract.AUTONOMOUS):
        settled = _claim_at_current_level(5800.0)
        waits = _claim_at_current_level(6200.0)
        try:
            assert settled["state"] == "completed" and settled["reply"]["status"] == "executed"
            assert waits["state"] == "input-required"
            assert waits["reply"]["rule"] == "settlement-agent.autonomous.execute_limit"
            assert waits["reply"]["limit"] == 6000.0
        finally:
            live.review(waits["reply"]["paymentId"], "reject", "demo-cleanup")


def test_a_push_waited_on_for_ten_minutes_delivers_completed():
    run = _claim(AMOUNT)
    result = {}
    listener = threading.Thread(target=lambda: result.update(
        live.await_outcome(run["push_token"], timeout=900)))
    listener.start()
    time.sleep(600)
    still_open = listener.is_alive()
    live.review(run["reply"]["paymentId"], "approve", REVIEWER)
    listener.join(timeout=60)
    assert still_open, f"the stream ended before the decision: {result}"
    assert result.get("state") == "completed", result
    assert result["reply"]["status"] == "executed"


def test_a_dropped_stream_reconnects_and_still_sees_the_outcome():
    run = _claim(AMOUNT)
    first = live.await_outcome(run["push_token"], timeout=60, drop_after=3)
    assert first.get("dropped"), first
    live.review(run["reply"]["paymentId"], "reject", REVIEWER)
    with live.claims_agent_route():
        once = live.get_task(run["task_id"])          # the one read after a drop
    if once["state"] not in live.TERMINAL_STATES:
        once = live.await_outcome(run["push_token"], timeout=60)
    assert once["state"] == "completed" and once["reply"]["status"] == "rejected"


def test_the_ledger_keeps_the_wait_after_the_human_has_acted(approved):
    # Read after the fixture released it: the record keeps when the wait began,
    # which is the clock `northstar.review.wait.duration` was measured from.
    record = live.get_payment(approved["payment_id"])
    assert record["awaitingSince"], record
    assert record["status"] == "executed", record


def test_approve_releases_the_payment_through_the_pds(approved):
    assert approved["review"]["error"] is None, approved["review"]
    assert approved["review"]["executed"]["status"] == "executed"
    assert live.get_payment(approved["payment_id"])["status"] == "executed"


def test_reject_records_the_reviewer_and_moves_no_money(rejected):
    assert rejected["review"]["error"] is None, rejected["review"]
    assert rejected["review"]["rejected"]["status"] == "rejected"
    assert rejected["review"]["rejected"]["rejection"]["reviewerId"] == rejected["reviewer"]
    assert live.get_payment(rejected["payment_id"])["status"] == "rejected"


def _execute_as_reviewer_ui(payment_id: str):
    """execute_payment straight at the gateway, under reviewer-ui's own SVID
    (minted by SPIRE for that subject, the way test_keystone mints tokens)."""
    with live.gateway_port_forward() as gateway_url:
        token = live.mint_svid(live.GATEWAY_AUDIENCE, subject=REVIEWER_UI_SPIFFE_ID)
        return live.call_tool_directly(
            gateway_url, "execute_payment",
            {"payment_id": payment_id, "amount": AMOUNT, "currency": "EUR"},
            {"authorization": f"Bearer {token}"},
        )


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
    # The decision that PERMITTED the release, read off trace B, names the human
    # rule and the ledger status it was decided on.
    trace_b = JaegerClient(JAEGER_URL).wait_for_operation(
        "reviewer-ui", "northstar.review.decision",
        attrs={"northstar.review.reviewer.id": approved["reviewer"]}, timeout=90)
    permits = [s for s in trace_b.spans
               if s.operation == "northstar.policy.decision" and s.attrs.get("northstar.policy.decision") == "permit"]
    assert [p.attrs["northstar.policy.rule"] for p in permits] == ["human-approved.execute"], permits


def test_executing_the_released_payment_again_is_refused(approved):
    # The payment is already executed, so the human rule must now deny it.
    outcome = _execute_as_reviewer_ui(approved["payment_id"])
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "human-approved.execute"


def test_trace_b_is_rooted_at_review_decision_and_links_to_trace_a(approved):
    trace_b = JaegerClient(JAEGER_URL).wait_for_operation(
        "reviewer-ui", "northstar.review.decision",
        attrs={"northstar.review.reviewer.id": approved["reviewer"]}, timeout=90)
    root = trace_b.root()
    assert root.operation == "northstar.review.decision"
    assert any(link.trace_id == approved["trace_id"] for link in root.links), root.links
    # The root lands first; payments-api's execute span arrives up to ~5 s later
    # (its batch processor). Wait for it, bounded, rather than assert on the
    # first read.
    trace_b = JaegerClient(JAEGER_URL).wait_for_trace_id(
        trace_b.trace_id, operation="POST /payments/{paymentId}/execute", timeout=30)
    decisions = [s for s in trace_b.spans if s.operation == "northstar.policy.decision"]
    assert [d.attrs["northstar.policy.rule"] for d in decisions] == ["human-approved.execute"]
    assert decisions[0].attrs["northstar.policy.ledger.status"] == "awaiting_approval"
    ops = {s.operation for s in trace_b.spans}
    assert "POST /payments/{paymentId}/execute" in ops, sorted(ops)


def test_trace_a_holds_the_deny_and_no_execute(approved):
    trace_a = JaegerClient(JAEGER_URL).wait_for_trace_id(
        approved["trace_id"], services={"policy-decision-service"}, timeout=90)
    denies = [s for s in trace_a.spans
              if s.operation == "northstar.policy.decision" and s.attrs.get("northstar.policy.decision") == "deny"]
    assert denies and denies[0].attrs["northstar.policy.limit.amount"] == 0.0
    assert "POST /payments/{paymentId}/execute" not in {s.operation for s in trace_a.spans}


HAND_OFF_SLACK_US = 2_000_000


def test_trace_a_is_finished_at_the_hand_off():
    """A claim that waits for a human no longer holds trace A open.

    Read trace A once its policy span is in, again after the batch processors
    have flushed (the baseline), and a third time 12 s later with nobody having
    decided: the span count must not move, and no span may start later than the
    `input-required` hand-off span's end plus 2 s. The claim is released after.
    """
    run = _claim(AMOUNT)
    assert run["state"] == "input-required", run
    try:
        client = JaegerClient(JAEGER_URL)
        client.wait_for_trace_id(run["trace_id"], operation="northstar.policy.decision", timeout=90)
        time.sleep(8)    # past the slowest export batch (5 s)
        baseline = client.get_trace(run["trace_id"])
        time.sleep(12)
        later = client.get_trace(run["trace_id"])
        assert len(later.spans) == len(baseline.spans), (
            f"trace A kept growing while the claim waited: {len(baseline.spans)} spans, then "
            f"{len(later.spans)} twelve seconds later; new: "
            f"{sorted({s.operation for s in later.spans} - {s.operation for s in baseline.spans})}")
        hand_off = [s for s in later.spans
                    if s.service == "claims-agent"
                    and s.attrs.get("northstar.a2a.task.state") == "input-required"]
        assert hand_off, ("no claims-agent span carries northstar.a2a.task.state=input-required; "
                          f"spans: {sorted(s.operation for s in later.spans)}")
        hand_off_end = max(s.start_us + s.duration_s * 1_000_000 for s in hand_off)
        straggler = [s for s in later.spans if s.start_us > hand_off_end + HAND_OFF_SLACK_US]
        assert not straggler, (
            "spans started more than 2 s after the hand-off ended, so trace A is not finished: "
            f"{[(s.service, s.operation) for s in straggler]}")
    finally:
        live.release_wait(run["reply"]["paymentId"])


def _trace_b(approved: dict) -> Trace:
    return JaegerClient(JAEGER_URL).wait_for_operation(
        "reviewer-ui", "northstar.review.decision",
        attrs={"northstar.review.reviewer.id": approved["reviewer"]}, timeout=90)


def test_the_resume_runs_in_trace_b_and_links_to_trace_a(approved):
    client = JaegerClient(JAEGER_URL)
    trace_b = client.wait_for_trace_id(
        _trace_b(approved).trace_id, operation="northstar.a2a.resume", timeout=60)
    assert trace_b.trace_id != approved["trace_id"]
    resumes = trace_b.spans_named("northstar.a2a.resume", "claims-agent")
    assert len(resumes) == 1, [(s.service, s.operation) for s in trace_b.spans]
    resume = resumes[0]
    # The v1 shape drops link attributes, so the reason is read from the OTLP form.
    links = client.link_attributes(trace_b.trace_id).get(resume.span_id, [])
    assert [l["trace_id"] for l in links] == [approved["trace_id"]], links
    assert links[0]["attrs"].get("northstar.a2a.link.reason") == "resume", links

    # The push to the customer: claims-agent's CLIENT span, then the customer-ui
    # server's SERVER span as its child. The server span lands up to ~5 s later.
    deadline = time.monotonic() + 60
    while True:
        trace_b = client.get_trace(trace_b.trace_id)
        pushes = [s for s in trace_b.spans
                  if s.service == "claims-agent" and s.kind == "client"
                  and any(str(v).endswith("/a2a/push") for v in s.attrs.values())]
        received = [s for s in trace_b.spans
                    if s.service == "customer-ui-server" and s.kind == "server"
                    and "/a2a/push" in s.operation]
        if (pushes and received) or time.monotonic() > deadline:
            break
        time.sleep(3)
    assert pushes, f"no claims-agent CLIENT span to /a2a/push in trace B: {sorted((s.service, s.operation) for s in trace_b.spans)}"
    assert received, f"no customer-ui-server SERVER span for /a2a/push in trace B: {sorted((s.service, s.operation) for s in trace_b.spans)}"
    assert received[0].parent_id == pushes[0].span_id, (received[0], pushes[0])
    assert received[0].start_us >= pushes[0].start_us


def test_the_push_token_is_in_no_span_of_trace_a_or_trace_b(approved):
    """The token is a per-claim credential. It reaches claims-agent in the stream
    params and comes back as a header; nothing may record it. Read from the OTLP
    form, which holds every attribute, resource attribute and event."""
    import json
    client = JaegerClient(JAEGER_URL)
    token = approved["push_token"]
    trace_b = _trace_b(approved).trace_id
    for name, trace_id in (("A", approved["trace_id"]), ("B", trace_b)):
        payload = json.dumps(client.get_trace_otlp(trace_id))
        assert token not in payload, f"the push token appears in trace {name} ({trace_id})"


# The series name was read off the running Prometheus on 2026-10-14. The label
# key is that Prometheus's own dot-to-underscore rendering of the M9 attribute
# `northstar.review.decision`, whose value is the registry's `approve`; it has
# not been read off a running cluster since the rename (Task 3 does that).
APPROVAL_WAIT_SERIES = "northstar_approval_wait_duration_seconds"
APPROVED_WAIT_COUNT = (
    f'{APPROVAL_WAIT_SERIES}_count{{job="reviewer-ui",northstar_review_decision="approve"}}'
)


def test_the_histogram_has_a_sample_for_the_approval(approved):
    """This approval added a sample. The count is read before the fixture approves
    and again here: a series that merely exists from an earlier run proves nothing."""
    deadline = time.monotonic() + 180
    after = live.prometheus_sum(APPROVED_WAIT_COUNT)
    while after <= approved["waits_before"] and time.monotonic() < deadline:
        time.sleep(10)
        after = live.prometheus_sum(APPROVED_WAIT_COUNT)
    assert after > approved["waits_before"], (
        f"the approved-wait count did not rise: {approved['waits_before']} before the "
        f"approval, {after} after")


# --- the two bypasses the review found (M8 review I1 and I2) ----------------


@pytest.fixture(scope="module")
def waiting():
    """A payment left awaiting approval and not decided, for the bypass tests.
    Rejected at teardown so the ledger is not left holding a wait."""
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    yield payment_id
    try:
        live.review(payment_id, "reject", REVIEWER)
    except httpx.HTTPError:
        pass


_DIRECT = (
    "import urllib.request, urllib.error\n"
    "r = urllib.request.Request("
    "'http://payments-api.northstar-systems.svc.cluster.local:8080/payments/{id}/execute',"
    " method='POST', data=b'{{\"amount\": 5800, \"currency\": \"EUR\"}}',"
    " headers={{'content-type': 'application/json'}})\n"
    "try:\n"
    "    print('STATUS', urllib.request.urlopen(r, timeout=10).status)\n"
    "except urllib.error.HTTPError as e:\n"
    "    print('STATUS', e.code, e.read()[:40])\n"
)


def test_reviewer_ui_cannot_execute_directly_against_payments_api(waiting):
    """payments-api has no caller auth; only Cilium's L7 rule on the
    reviewer-ui's egress stops a direct POST /execute that skips the gateway
    and the PDS. The proxy answers 403 before the ledger sees it."""
    result = live.kubectl(
        "-n", "northstar-agents", "exec", "deploy/reviewer-ui", "--",
        "python", "-c", _DIRECT.format(id=waiting), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "STATUS 403" in result.stdout, result.stdout
    assert "Access denied" in result.stdout, result.stdout
    assert live.get_payment(waiting)["status"] == "awaiting_approval"


def test_the_agent_cannot_release_a_payment_it_routed_to_a_human_even_at_autonomous(waiting):
    """Maker-checker: the PDS reads the ledger for the agent's execute too, so
    at `autonomous`, under the limit, a payment left waiting is still not the
    agent's to release."""
    with live.autonomy(contract.AUTONOMOUS):
        with live.gateway_port_forward() as gateway_url:
            token = live.mint_svid(live.GATEWAY_AUDIENCE)  # settlement-agent's own
            outcome = live.call_tool_directly(
                gateway_url, "execute_payment",
                {"payment_id": waiting, "amount": AMOUNT, "currency": "EUR"},
                {"authorization": f"Bearer {token}"},
            )
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "settlement-agent.payment_not_proposed", outcome.denial
    assert live.get_payment(waiting)["status"] == "awaiting_approval"
