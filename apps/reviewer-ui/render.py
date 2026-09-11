"""The stage surface's HTML. Server-rendered strings, no templating engine
and no frontend toolchain -- following apps/customer-ui's layout in spirit
(one page, one form, legible from the back of a room) while using this
service's own stack, per
docs/superpowers/specs/2026-09-10-northstar-m3-supervised-design.md §3:
"`reviewer-ui` follows customer-ui's existing pattern rather than
introducing a frontend toolchain."

Every figure this module prints is read out of the dict it was handed --
`payment["amount"]`, `payment["refusal"]["rule"]`, and so on -- never summed,
rounded or rederived. The one field that is not the ledger's own words is
`narrative`, the model's; it is always printed behind an explicit
attribution ("The agent told the claimant:") so it cannot be read as a
statement of what happened, and it is the one user-influenced string on this
page that gets HTML-escaped rather than trusted, because it is genuinely
third-party content -- a model's own generated prose, not a fact this
platform recorded.

`html.escape` (stdlib, no dependency) is used for anything that did not
originate as a literal string this module wrote itself: the narrative, the
reviewer's typed name, and any error text that might quote a payload back.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

_STYLE = """
<style>
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 20px;
         line-height: 1.5; max-width: 900px; margin: 2rem auto; padding: 0 1rem;
         background: #0b0f14; color: #e6edf3; }
  h1 { font-size: 2rem; }
  .card { border: 1px solid #30363d; border-radius: 8px; padding: 1.25rem 1.5rem;
          margin-bottom: 1.5rem; background: #161b22; }
  .figure { font-family: ui-monospace, Menlo, monospace; font-size: 1.1em; }
  .label { color: #8b949e; text-transform: uppercase; font-size: 0.75em;
            letter-spacing: 0.05em; }
  .decision { display: flex; gap: 0.75rem; margin-top: 1rem; }
  button { font-size: 1.1rem; padding: 0.6rem 1.4rem; border-radius: 6px;
           border: 1px solid #30363d; cursor: pointer; }
  button.approve { background: #238636; color: white; }
  button.reject { background: #da3633; color: white; }
  input[type=text] { font-size: 1.1rem; padding: 0.4rem 0.6rem; border-radius: 6px;
                      border: 1px solid #30363d; background: #0d1117; color: #e6edf3; }
  .narrative { border-left: 3px solid #8b949e; padding-left: 1rem; font-style: italic;
               color: #c9d1d9; }
  .error { color: #f85149; font-weight: bold; }
  .ok { color: #3fb950; font-weight: bold; }
  a { color: #58a6ff; }
</style>
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title>{_STYLE}</head>"
        f"<body><h1>{escape(title)}</h1>{body}"
        '<p><a href="/">&larr; Back to refused payments</a></p>'
        "</body></html>"
    )


def _payment_card(payment: dict[str, Any]) -> str:
    refusal = payment.get("refusal") or {}
    payment_id = str(payment.get("paymentId", ""))
    return f"""
<div class="card">
  <p><span class="label">Claim</span> <span class="figure">{escape(str(payment.get("claimId", "")))}</span>
     &middot; <span class="label">Payment</span> <span class="figure">{escape(payment_id)}</span></p>
  <p><span class="label">Amount requested</span>
     <span class="figure">{escape(str(payment.get("amount", "")))} {escape(str(payment.get("currency", "")))}</span></p>
  <p><span class="label">Policy decision</span>
     rule <span class="figure">{escape(str(refusal.get("rule", "")))}</span>,
     limit <span class="figure">{escape(str(refusal.get("limit", "")))}</span>,
     requested <span class="figure">{escape(str(refusal.get("requested", "")))}</span></p>
  <p class="narrative">The agent told the claimant: &ldquo;{escape(str(refusal.get("narrative", "")))}&rdquo;</p>
  <form method="post" action="/review/{escape(payment_id, quote=True)}">
    <label>Reviewer name
      <input type="text" name="reviewer_id" placeholder="alex.mercer" required>
    </label>
    <div class="decision">
      <button class="approve" type="submit" name="decision" value="approve">Approve</button>
      <button class="reject" type="submit" name="decision" value="reject">Reject</button>
    </div>
  </form>
</div>
"""


def list_page(payments: list[dict[str, Any]]) -> str:
    if not payments:
        body = "<p>There are no refused payments awaiting review.</p>"
    else:
        body = "".join(_payment_card(p) for p in payments)
    return _page("Refused payments", body)


def error_page(message: str) -> str:
    # Shipped with a keyword-only `status_hint` parameter that no caller ever
    # passed: main.py's six call sites all pass the message alone, and the
    # HTTP status each of them chooses is already on the response. Dropped in
    # the M3 final-review fix round rather than left standing as a second,
    # unexercised way to say the same thing.
    return _page("Reviewer UI", f'<p class="error">{escape(message)}</p>')


@dataclass
class ResultView:
    payment_id: str
    decision: str
    reviewer_id: str
    elapsed_ms: int
    executed: dict[str, Any] | None
    error: str | None


def result_page(outcome: ResultView) -> str:
    reviewer = escape(outcome.reviewer_id)
    payment_id = escape(outcome.payment_id)

    if outcome.decision == "reject":
        body = (
            f'<p class="ok">Rejected by {reviewer}.</p>'
            f"<p>Payment <span class=\"figure\">{payment_id}</span>: no execution "
            "was attempted.</p>"
        )
    elif outcome.executed is not None:
        executed = outcome.executed
        body = (
            f'<p class="ok">Approved by {reviewer}.</p>'
            f"<p>Payment <span class=\"figure\">{escape(str(executed.get('paymentId', '')))}</span> "
            f"is now <span class=\"figure\">{escape(str(executed.get('status', '')))}</span> for "
            f"<span class=\"figure\">{escape(str(executed.get('amount', '')))} "
            f"{escape(str(executed.get('currency', '')))}</span>.</p>"
        )
    else:
        body = (
            f"<p>Approved by {reviewer}, but the execution did not happen.</p>"
            f'<p class="error">Payment <span class="figure">{payment_id}</span> '
            f"was not executed: {escape(outcome.error or 'unknown error')}</p>"
        )

    body += f'<p><span class="label">Elapsed since refusal</span> {outcome.elapsed_ms} ms</p>'
    return _page("Review recorded", body)
