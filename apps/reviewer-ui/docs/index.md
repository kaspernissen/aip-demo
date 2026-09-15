# Reviewer UI

The internal console where a human overturns a refusal. This is where the
"supervised" claim becomes visible: a refused payment is not the end of the
story.

## Responsibilities

- List every payment whose status is `refused`, each with **four facts**: the
  rule that fired, the limit the caller held, the amount requested, and the
  model's own narrative.
- Accept an approval and POST it to `senior-settlement-agent` over A2A.
- Emit the `review.decision` span.

## The information path is the ledger, not telemetry

This page reads refusals straight from `payments-api`'s REST API — never
through the MCP path, and never out of the trace. Turn content capture off and
this page is unchanged.

Telemetry is for the platform team. The ledger is for the reviewer.

## What approval actually does

It does **not** override the refusal. It re-enters the *same* choke point —
agentgateway, the same JWT-SVID verification, the same Policy Decision Service
— under a **different identity** with its own allowance row. Same rule, same
code path, different caller, different answer.

The resulting `settlement.execute_approved` span carries a **span link** back
to the refused trace, not a parent edge: the human took minutes, in a process
that no longer exists, so a parent edge would misstate both the duration and
what was executing.

## Deliberate limitations, stated rather than hidden

- **One human, one click, once.** No second approver, no timeout, no expiry, no
  re-proposal path back to the agent.
- **The reviewer is not authenticated.** The reviewer id is a string the form
  supplies. The span's `review.reviewer.spiffe_id` is *reviewer-ui's own*
  identity, not the person's — so between them the span records an unverified
  claim and the deployment that accepted it, never which person approved.
- **Reject does nothing, honestly.** It records a span and leaves the payment
  exactly as refused.
- This service has **no `EXECUTE_LIMITS` row at all**, so it could not execute a
  payment even if it tried. That is criterion 7, and it is the stronger form of
  the claim.

## Implementation

Python, FastAPI, server-rendered HTML strings — **no frontend toolchain and no
build step**, deliberately unlike `customer-ui`. Its styling mirrors the shared
Northstar palette by hand for that reason.

Reached on host port `8081` via a NodePort, so it survives a redeploy.
