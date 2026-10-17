# Reviewer UI

The internal console where a human releases, or rejects, a payment the agent
could not release itself. This is the "checker" in maker-checker: the agent
prepares every payment, and above its band a human decides.

## Responsibilities

- List every payment whose status is `awaiting_approval`, each with the facts
  the reviewer needs: the rule that fired, the limit the caller held, the
  amount requested, how long it has waited, and the model's own narrative.
- Accept an approval and **release the money itself**: an `execute_payment`
  tool call through agentgateway, under this workload's own SPIFFE ID.
- Accept a rejection and record it on the ledger (`POST /payments/{id}/reject`).
  No money moves.
- Emit the `northstar.review.decision` span.
- Offer the same queue and decision as JSON (`GET /api/awaiting`,
  `POST /api/review/{id}`) for the demo driver and the tests.

## The information path is the ledger, not telemetry

This page reads the queue straight from `payments-api`'s REST API, never out
of the trace. Turn content capture off and this page is unchanged.

Telemetry is for the platform team. The ledger is for the reviewer.

## What approval actually does

The reviewer-ui does not override the refusal and does not ask the agent to
act. It goes through the *same* choke point as the agent: agentgateway, the
same JWT-SVID verification, the same Policy Decision Service, under its **own
identity**. The PDS permits it under the rule `human-approved.execute`, and
only when the PDS itself reads `awaiting_approval` from the ledger. A check
this service did for itself would be a check a compromised reviewer-ui could
skip, so the platform makes it.

## Deliberate limitations, stated rather than hidden

- **One human, one click, once.** No second approver, no timeout, no expiry.
- **The reviewer is not authenticated.** The reviewer id is a string the form
  supplies. The span's `northstar.review.reviewer.spiffe_id` is *reviewer-ui's own*
  identity, not the person's, so between them the span records an unverified
  claim and the deployment that accepted it, never which person approved.
- **It can release only what is waiting.** It has no spending limit, and no
  authority over any payment the ledger does not show as awaiting approval.
  Only the settlement agent can put a payment into that state.

## Implementation

Python, FastAPI, server-rendered HTML strings: **no frontend toolchain and no
build step**, deliberately unlike `customer-ui`. Its styling mirrors the shared
Northstar palette by hand for that reason.

Reached on host port `8081` via a NodePort, so it survives a redeploy.
