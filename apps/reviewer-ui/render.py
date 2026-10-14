"""The stage surface's HTML. Server-rendered strings, no templating engine
and no frontend toolchain -- following apps/customer-ui's layout in spirit
(one page, one form, legible from the back of a room) while using this
service's own stack.

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

# The internal register of the same institutional identity apps/customer-ui
# wears -- navy and white, Spectral for display, IBM Plex Sans and Mono for
# everything else. The tokens are mirrored by hand from
# apps/customer-ui/src/app/globals.css because this service deliberately ships
# no frontend toolchain; if one moves, move the other.
#
# TWO STRINGS THIS STYLESHEET MAY NOT CONTAIN, both asserted by
# tests/test_render.py against pages this CSS is inlined into:
#
#   "executed" -- test_result_page_for_a_rejection_records_no_execution
#                 asserts the substring is absent from the WHOLE rejection
#                 page, and the stylesheet ships inside it. The settled state
#                 is therefore `.is-settled`, never `.executed`.
#   "PAY-"     -- test_list_page_says_so_when_there_is_nothing_to_review
#                 asserts it is absent from the empty list page, so no
#                 placeholder or example reference may appear here.
#
# Fonts come from Google Fonts with a real fallback behind each family, and
# `display=swap` means a reviewer on a venue network with no egress loses the
# typography and nothing else.
_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700"
    '&family=Spectral:wght@500;600&display=swap">'
)

_STYLE = """
<style>
  :root {
    --navy-900:#0a2342; --navy-700:#123963; --blue-600:#1b5fa8; --blue-300:#8fb8dd;
    --blue-200:#c9ddf0; --blue-100:#dfebf7; --blue-050:#eff4fa; --paper:#fff;
    --ink:#0e1f33; --ink-soft:#4c637e; --ink-faint:#7a8ea6; --rule:#d7e3f0;
    --refused:#8a5a00; --settled:#0f6b4f; --fault:#a32a22;
    --serif:Spectral,Georgia,"Times New Roman",serif;
    --sans:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  *{box-sizing:border-box}
  html{color-scheme:light}
  body{margin:0;padding:0;background:var(--blue-050);color:var(--ink);
       font-family:var(--sans);font-size:16px;line-height:1.6;
       -webkit-font-smoothing:antialiased}

  .masthead{background:var(--navy-900);color:#fff;border-bottom:3px solid var(--blue-600)}
  .masthead-inner{max-width:900px;margin:0 auto;padding:16px 24px;display:flex;
                  align-items:center;gap:14px}
  .wordmark{font-family:var(--serif);font-size:1.25rem;font-weight:600;line-height:1.1}
  .wordmark span{display:block;font-family:var(--sans);font-size:.6875rem;font-weight:500;
                 letter-spacing:.16em;text-transform:uppercase;color:var(--blue-300);
                 margin-top:2px}
  .console{margin-left:auto;font-family:var(--mono);font-size:.75rem;letter-spacing:.04em;
           color:var(--blue-300);text-align:right}

  .shell{max-width:900px;margin:0 auto;padding:30px 24px 60px}
  h1{font-family:var(--serif);font-size:1.9rem;font-weight:600;color:var(--navy-900);
     margin:0 0 22px;text-wrap:balance}

  .card{background:var(--paper);border:1px solid var(--rule);border-left:4px solid var(--refused);
        border-radius:4px;padding:20px 24px;margin-bottom:18px;
        box-shadow:0 1px 2px rgba(10,35,66,.06),0 12px 32px -20px rgba(10,35,66,.32)}
  .card p{margin:0 0 10px}
  .card p:last-of-type{margin-bottom:0}

  .figure{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--navy-700);
          letter-spacing:.01em}
  .label{display:inline-block;color:var(--ink-soft);text-transform:uppercase;
         font-size:.6875rem;font-weight:600;letter-spacing:.1em;margin-right:4px}

  .narrative{border-left:2px solid var(--blue-200);background:var(--blue-050);
             padding:12px 16px;font-style:italic;color:var(--navy-700);border-radius:0 3px 3px 0}

  form{margin-top:18px;padding-top:16px;border-top:1px solid var(--rule)}
  label{font-size:.6875rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
        color:var(--ink-soft);display:block}
  input[type=text]{display:block;margin-top:6px;font-family:var(--mono);font-size:.9375rem;
                   padding:9px 12px;border-radius:3px;border:1px solid var(--blue-200);
                   background:var(--paper);color:var(--ink);min-width:260px;max-width:100%}
  input[type=text]:focus{outline:none;border-color:var(--blue-600);
                         box-shadow:0 0 0 3px var(--blue-100)}

  .decision{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
  button{font-family:var(--sans);font-size:.9375rem;font-weight:600;letter-spacing:.02em;
         padding:10px 24px;border-radius:3px;border:1px solid;cursor:pointer;
         transition:background .12s ease}
  button.approve{background:var(--blue-600);border-color:var(--blue-600);color:#fff}
  button.approve:hover{background:var(--navy-700);border-color:var(--navy-700)}
  button.reject{background:var(--paper);border-color:var(--blue-200);color:var(--navy-700)}
  button.reject:hover{border-color:var(--blue-300);background:var(--blue-050)}
  button:focus-visible{outline:3px solid var(--blue-200);outline-offset:2px}

  .verdict{background:var(--paper);border:1px solid var(--rule);border-left:4px solid var(--blue-600);
           border-radius:4px;padding:20px 24px;margin-bottom:18px}
  .verdict.is-settled{border-left-color:var(--settled)}
  .verdict.is-fault{border-left-color:var(--fault)}
  .ok{color:var(--settled);font-weight:600;font-size:1.05rem}
  .error{color:var(--fault);font-weight:600}
  .empty{color:var(--ink-soft);background:var(--paper);border:1px dashed var(--blue-200);
         border-radius:4px;padding:26px 24px;text-align:center}

  a{color:var(--blue-600)}
  .back{margin-top:26px;font-size:.9375rem}

  @media (max-width:560px){
    .console{display:none}
    .shell{padding-inline:16px}
    .card,.verdict{padding-inline:16px}
  }
</style>
"""

_MARK = (
    '<svg width="32" height="32" viewBox="0 0 32 32" aria-hidden="true" '
    'style="flex:none;display:block">'
    '<path d="M16 0.5 L18.4 13.6 L31.5 16 L18.4 18.4 L16 31.5 L13.6 18.4 '
    'L0.5 16 L13.6 13.6 Z" fill="#ffffff"/>'
    '<path d="M16 6.2 L17.3 14.7 L25.8 16 L17.3 17.3 L16 25.8 L14.7 17.3 '
    'L6.2 16 L14.7 14.7 Z" fill="#2b74c0"/>'
    "</svg>"
)

_MASTHEAD = (
    '<header class="masthead"><div class="masthead-inner">'
    f"{_MARK}"
    '<div class="wordmark">Northstar<span>Insurance</span></div>'
    '<div class="console">Settlement review<br>Internal console</div>'
    "</div></header>"
)


def _page(title: str, body: str) -> str:
    # The <h1> carries the title as VISIBLE text, not only the <title> tag:
    # the e2e tests wait on `text=Review recorded`, and
    # Playwright's text engine matches rendered text, which <title> is not.
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title>{_FONTS}{_STYLE}</head>"
        f"<body>{_MASTHEAD}<main class=\"shell\"><h1>{escape(title)}</h1>{body}"
        '<p class="back"><a href="/">&larr; Back to payments awaiting approval</a></p>'
        "</main></body></html>"
    )


def _payment_card(payment: dict[str, Any]) -> str:
    refusal = payment.get("refusal") or {}
    payment_id = str(payment.get("paymentId", ""))
    # The attribution ("told the claimant") must stay within 200 characters
    # BEFORE the narrative text -- tests/test_render.py's
    # test_the_narrative_is_attributed_to_the_agent_not_stated_as_fact reads
    # that window. Do not push markup between the two.
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
  <p><span class="label">Status</span> <span class="figure">awaiting approval</span>
     &middot; <span class="label">Waiting since</span> <span class="figure">{escape(str(payment.get("awaitingSince", "")))}</span></p>
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
        # No example reference may appear here, not even as a placeholder:
        # test_list_page_says_so_when_there_is_nothing_to_review asserts the
        # string "PAY-" is absent from this whole page.
        body = '<p class="empty">There are no payments waiting for approval.</p>'
    else:
        body = "".join(_payment_card(p) for p in payments)
    return _page("Payments awaiting approval", body)


def error_page(message: str) -> str:
    # Takes the message alone, not a separate status hint: main.py's six call
    # sites all pass just the message, and the HTTP status each of them
    # chooses is already on the response.
    return _page("Reviewer UI", f'<p class="error">{escape(message)}</p>')


@dataclass
class ResultView:
    payment_id: str
    decision: str
    reviewer_id: str
    elapsed_ms: int
    executed: dict[str, Any] | None
    error: str | None
    rejected: dict[str, Any] | None = None
    # Whether the customer's waiting claim was told (claims.py). Only said when
    # the decision itself went through.
    resumed_state: str | None = None
    resume_error: str | None = None


def result_page(outcome: ResultView) -> str:
    reviewer = escape(outcome.reviewer_id)
    payment_id = escape(outcome.payment_id)

    # The tone class on the wrapper is the ONLY styling difference between
    # these branches, and the rejection branch deliberately takes the neutral
    # one: test_result_page_for_a_rejection_records_no_execution asserts the
    # substring "executed" appears nowhere on that page, so the settled state's
    # class is `is-settled`.
    if outcome.decision == "reject" and outcome.error is None:
        tone = "verdict"
        body = (
            f'<p class="ok">Rejected by {reviewer}.</p>'
            f"<p>Payment <span class=\"figure\">{payment_id}</span>: no money moved, "
            "and none will.</p>"
        )
    elif outcome.executed is not None:
        executed = outcome.executed
        tone = "verdict is-settled"
        body = (
            f'<p class="ok">Paid by the platform after your approval, {reviewer}.</p>'
            f"<p>Payment <span class=\"figure\">{escape(str(executed.get('paymentId', '')))}</span> "
            f"is now <span class=\"figure\">{escape(str(executed.get('status', '')))}</span> for "
            f"<span class=\"figure\">{escape(str(executed.get('amount', '')))} "
            f"{escape(str(executed.get('currency', '')))}</span>.</p>"
        )
    else:
        tone = "verdict is-fault"
        what = "approval" if outcome.decision == "approve" else "rejection"
        body = (
            f"<p>{what.capitalize()} by {reviewer}, but it did not happen.</p>"
            f'<p class="error">Payment <span class="figure">{payment_id}</span> '
            f"was not changed: {escape(outcome.error or 'unknown error')}</p>"
        )

    if outcome.error is None:
        if outcome.resume_error is not None:
            body += (
                '<p class="error">The customer could not be told: '
                f"{escape(outcome.resume_error)}</p>"
            )
        elif outcome.resumed_state is not None:
            body += "<p>The customer has been told.</p>"

    body += f'<p><span class="label">Waited</span> {outcome.elapsed_ms} ms</p>'
    return _page("Review recorded", f'<div class="{tone}">{body}</div>')
