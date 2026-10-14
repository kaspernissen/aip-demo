"""render.py: the stage surface's HTML.

Every assertion here is either "the ledger's own value appears verbatim" or
"nothing is escaped into a different value" -- this file has no arithmetic of
its own to get wrong, and the tests are written to catch someone adding some
(e.g. reformatting the amount, or rederiving the rule from the limit).
`narrative` is the model's; a test here pins that it renders as a quoted
attribution, never as an unqualified statement of what happened, and that it
is HTML-escaped, because it is the one string here that is genuinely
untrusted third-party content.
"""
from __future__ import annotations

import render

PAYMENT = {
    "paymentId": "PAY-000053",
    "claimId": "CLM-1842",
    "amount": 5800.0,
    "currency": "EUR",
    "status": "awaiting_approval",
    "awaitingSince": "2026-09-10T10:00:00Z",
    "refusal": {
        "rule": "settlement-agent.assisted.execute_limit",
        "limit": 0.0,
        "requested": 5800.0,
        "narrative": "I'm unable to complete this payment myself.",
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    },
}


def test_list_page_shows_every_ledger_field_for_a_payment_awaiting_approval():
    html = render.list_page([PAYMENT])
    assert "PAY-000053" in html
    assert "CLM-1842" in html
    assert "5800" in html
    assert "EUR" in html
    assert "settlement-agent.assisted.execute_limit" in html
    assert "awaiting approval" in html
    assert "2026-09-10T10:00:00Z" in html  # waiting since
    assert "I&#x27;m unable to complete this payment myself." in html or (
        "I'm unable to complete this payment myself." in html
    )


def test_list_page_says_so_when_there_is_nothing_to_review():
    html = render.list_page([])
    assert "PAY-" not in html
    assert "no" in html.lower()


def test_the_narrative_is_attributed_to_the_agent_not_stated_as_fact():
    """The honesty rule: narrative is what the model told the claimant, never
    a statement of what happened. Some form of attribution language --
    "the agent said" / "told the claimant" / a quotation -- must wrap it,
    not just the bare sentence sitting next to the ledger figures as if it
    were one of them."""
    html = render.list_page([PAYMENT])
    narrative_index = html.find("unable to complete")
    assert narrative_index != -1
    surrounding = html[max(0, narrative_index - 200): narrative_index]
    assert any(
        phrase in surrounding.lower()
        for phrase in ("told the claimant", "the agent said", "said:", "narrative")
    )


def test_the_narrative_is_html_escaped():
    hostile = dict(PAYMENT)
    hostile["refusal"] = dict(PAYMENT["refusal"])
    hostile["refusal"]["narrative"] = "<script>alert(1)</script>"
    html = render.list_page([hostile])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_reviewer_id_echoed_on_a_result_page_is_html_escaped():
    outcome = render.ResultView(
        payment_id="PAY-000053",
        decision="approve",
        reviewer_id="<b>alex</b>",
        elapsed_ms=5000,
        executed=None,
        error="boom",
    )
    html = render.result_page(outcome)
    assert "<b>alex</b>" not in html
    assert "&lt;b&gt;alex&lt;/b&gt;" in html


def test_result_page_for_a_successful_approval_shows_the_ledger_record():
    outcome = render.ResultView(
        payment_id="PAY-000053",
        decision="approve",
        reviewer_id="alex.mercer",
        elapsed_ms=5000,
        executed={
            "paymentId": "PAY-000053",
            "amount": 5800.0,
            "currency": "EUR",
            "status": "executed",
        },
        error=None,
    )
    html = render.result_page(outcome)
    assert "Review recorded" in html
    assert "Paid by the platform after your approval" in html
    assert "executed" in html
    assert "5800" in html
    assert "alex.mercer" in html


def test_result_page_for_a_refused_release_shows_the_platforms_text_and_no_success():
    """The honesty requirement: surface the platform's refusal as it came back
    rather than showing a success."""
    outcome = render.ResultView(
        payment_id="PAY-000053",
        decision="approve",
        reviewer_id="alex.mercer",
        elapsed_ms=5000,
        executed=None,
        error="the ledger shows 'rejected' [policy-denial rule=human-approved.execute limit=none requested=5800.00]",
    )
    html = render.result_page(outcome)
    assert "did not happen" in html.lower()
    assert "policy-denial rule=human-approved.execute" in html
    assert "paid by the platform" not in html.lower()
    assert "status executed" not in html.lower()


def test_result_page_for_a_rejection_records_no_execution():
    outcome = render.ResultView(
        payment_id="PAY-000053",
        decision="reject",
        reviewer_id="alex.mercer",
        elapsed_ms=5000,
        executed=None,
        error=None,
        rejected={"paymentId": "PAY-000053", "status": "rejected"},
    )
    html = render.result_page(outcome)
    assert "Review recorded" in html
    assert "reject" in html.lower()
    assert "executed" not in html.lower()
