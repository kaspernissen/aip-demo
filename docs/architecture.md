# Northstar Architecture — M8 (Human in the loop), on M7's Bounded

> **M8 change, read this first.** `senior-settlement-agent` was **retired in
> M8**. Wherever this document, written milestone by milestone, describes it,
> its `ClusterSPIFFEID` fallback, its Cilium policies, its €25,000 row or the
> `approval.py` A2A hop, that is **dated history of M3 to M7**, kept because it
> records what was measured then. What is true now:
>
> - The autonomy dial is a **maker-checker policy**. `settlement-agent` may
>   release €0 at `assisted` and up to €6,000 at `autonomous`
>   (`EXECUTE_LIMITS` in `pds/rules.py`).
> - payments-api has two new states, `awaiting_approval` and `rejected`. A
>   refused payment is `awaiting_approval`; a human moves it to `executed` or
>   `rejected`.
> - **reviewer-ui releases the money itself**: it calls `execute_payment`
>   through agentgateway with its own JWT-SVID (`northstar-reviewer-ui`
>   `ClusterSPIFFEID`). The PDS permits it under rule
>   `human-approved.execute`, and only when it reads `awaiting_approval` from
>   payments-api itself (span attribute `policy.ledger.status`). A ledger it
>   cannot read is a deny.
> The policy, as `pds/rules.py` enforces it:
>
> | Caller | Level | `execute_payment` is permitted |
> |---|---|---|
> | `settlement-agent` | `assisted` | never (limit €0) |
> | `settlement-agent` | `autonomous` | up to €6,000, and only a payment the ledger shows `proposed` (else rule `settlement-agent.payment_not_proposed`, or `settlement-agent.ledger_unreadable` when the ledger cannot be read) |
> | `reviewer-ui` (in `HUMAN_APPROVERS`) | no level | only when payments-api shows the payment `awaiting_approval`, under rule `human-approved.execute` |
> | any other caller | any | never (`unknown_caller`) |
>
> The ledger state machine: `proposed` becomes `executed` (the agent, inside its
> band) or `awaiting_approval` (the agent was refused and recorded why);
> `awaiting_approval` becomes `executed` (the reviewer-ui, through the PDS) or
> `rejected` (`POST /payments/{id}/reject`, final). `executed` and `rejected`
> are terminal, and executing a rejected payment is a 409 whose
> `northstar.payment.refusal_cause` is `rejected`. **The reviewer-ui is the only
> path for a human approval**. The PDS reads the ledger status for every
> `execute_payment` (agent and human; unreadable denies), so the maker cannot release
> a payment it routed to a human. payments-api has no caller authentication, so the
> reviewer-ui's egress to it is L7 (Cilium `rules.http`: list, get and reject only),
> and a direct `POST .../execute` from that pod is refused by the proxy with 403.
> Only the reviewer-ui is in `HUMAN_APPROVERS`, and the
> Kyverno keystone policy refuses an agent the verbs to run a pod as, or mint a
> token for, that ServiceAccount.
>
> - There is one settlement Deployment. The approver is a different
>   `service.name` (`reviewer-ui`), so the "same service name, tell them apart
>   by SPIFFE ID" caveat below no longer applies.
> - **Trace B is the approval, and the span link now sits on its root.**
>   `review.decision` (reviewer-ui, SERVER) is the root of trace B. Under it:
>   the ledger read, the MCP calls through agentgateway, the PDS's
>   `policy.decision` (rule `human-approved.execute`, `policy.ledger.status` =
>   `awaiting_approval`), payments-mcp and payments-api's execute, and the
>   claims-agent resume. It carries one **span link** to the refusal span in trace A
>   (ids from the ledger's `refusal.refusedTraceId` and `refusedSpanId`), and
>   the claim id as both an attribute and baggage. Wherever the text below says
>   the link sits on `settlement.execute_approved`, that is dated M3 to M7 history.
> - **How long humans take is a metric.** reviewer-ui records the OpenTelemetry
>   histogram `northstar.approval.wait.duration` (seconds, attributes `decision`
>   and `autonomy.level`, never the reviewer) at each decision that took effect:
>   `awaitingSince` to the decision. It is not sampled, and it reaches Prometheus as
>   `northstar_approval_wait_duration_seconds_*` (measured; see the contract).

Status: **M8 (Human in the loop)**, on branch `m8-human-in-the-loop`, on top of **M7 (Bounded)**, which sat on **M6 (Golden path)**, which merged and is tagged `m6-verified`. **M5 (Progressive
autonomy) merged 2026-09-15 at `e62238f`, tagged `m5-verified`** -- read that
tag's own message before trusting it, it claims less than its predecessors.
Bumped in M6's first commit rather than left to a doc pass: this line went
stale two milestones running (M3->M4, then M4->M5), and M5 shipped a guard
(`tests/unit/test_doc_status_lines_name_the_current_milestone.py`) that now
derives the expected milestone from the newest plan file and reddens here the
moment a new plan lands. It did, on its first day.
**This header and the line below it said "M4 (Observable)" on branch
`m4-observable` until this whole-branch review's fix wave (finding J5), while
the sections beneath them had already grown the flagd sidecar diagram and
four platform-component rows — +105 lines M5 added to this file. Second
consecutive recurrence**: the paragraph immediately below already records
this exact defect happening to this exact line once before, for M3 → M4.
Recorded rather than silently corrected, per the convention the rest of this
file follows.

**This header and the line below it said "M3 (Supervised)" until M4 Task 5's
doc pass, while the sections beneath them had already grown `eval-scorer`'s
SPIFFE ID and SPIRE entry, the stage-4 surfaces and "See it yourself" step
9** — the same stale-prose class this document's own intro records catching a
few paragraphs down, this time in the one line a reader reads first. Recorded
rather than silently corrected, per the convention the rest of this file
follows.

That sentence also said "**its Cilium** and SPIRE entries" until M4's final
review. `eval-scorer` has **no** Cilium policy — `grep -c eval-scorer
deploy/platform/cilium-policies.yaml` is 0, and `scripts/setup-kind.sh`
records the absence as a posture gap in its stage-4 eval-scorer block (the
paragraph beginning "deploy/platform/cilium-policies.yaml, on the other hand,
names no rule selecting it" — cited by its text rather than by a line number,
because the line number this correction first used was already three lines off
when it was written), and spec §9's carried-debt entry (ruling
M4-R17) names `eval-scorer` as one of the unenforced `northstar-platform`
endpoints owned by M5. Two committed documents from the same milestone
disagreeing about whether the milestone's new service sits behind a network
policy is worse than either being wrong alone, and the one that was wrong is
the one a reader opens first — so the correction is recorded rather than
quietly applied.

The M4 content was written and checked by that milestone's tasks against the
live `kind-northstar` **stage-4** cluster as each was written, and M4 Task 5
re-measured the Cilium enforcement table, the SPIRE entry list and the stage-4
memory figure rather than carrying any of them forward. The M3 content keeps
its own provenance (Task 5B, against the branch's HEAD at that point and a
stage-3 cluster, with every endpoint capture, SPIRE entry list and pod count
re-taken); M2.2's keeps commit `50fac07`, Task 8; M2.1's keeps `b75b260`; and
M1.1's and M1.2's keep theirs, stated in the next block. **This document does
not claim a single verification pass over all of it** — the per-section
provenance is what makes that honest rather than vague.

M1.1 was the walking skeleton: one claim, five components, one trace, no
governance. M1.2 put a real model call inside that skeleton without changing
its shape. M2.1 changed the shape for the first time — a proxy and an
identity provider entered the picture, and one hop authenticated its caller
cryptographically. M2.2 is where the platform started saying no. M3 is where
somebody can say yes anyway. **M4 is where you can prove, from telemetry alone,
which of the two happened and why.**

**The one-sentence version of M4:** *the audit trail is reconstructed from what
the platform decided, not from what the model said.* That is why the evidence
view needed no new application code to exist, and why turning content capture
off changes nothing on it — the measured answer to OQ-1, in
[`docs/telemetry-contract.md`](telemetry-contract.md).

**The one-sentence version of M3, and the honest one:** *a refusal is now a
record a human can act on, and acting on it goes back through the same choke
point under a different identity.* Concretely, and measured live rather than
described: submitting the pre-filled `CLM-1842` at €6,200 gets the payment
**proposed, refused, and written down as refused**; the model narrates the
refusal to the claimant and does not retry; a human finds the refusal on the
reviewer UI with the rule, the limit, the requested amount and that narrative;
and one click executes the same €6,200 as `senior-settlement-agent`, which the
Policy Decision Service permits on its own allowance row. A second claim at
€5,800 still settles with no human involved. Both halves still matter, and the
M3 version of that sentence is: a platform that asks a human about everything
is not supervision, it is a queue.

**What is still not decided, so the "supervised" claim is not overread.** One
hop authorizes: `settlement-agent → agentgateway` on the MCP call, and the
approved execution crosses that same hop rather than a privileged one. Hop 1
(browser → claims-agent), Hop 2 (claims-agent → settlement-agent) and Hop 4
(payments-mcp → payments-api) carry no identity and no policy, and
payments-api still moves money for anything that can reach it. Approval is one
human, one click, once — and the human is not authenticated at all. See "What
the system deliberately does not do", which M3 **sharpens for the fourth
time** — from *one hop authorized, on one rule* to *one hop authorized, on
three rules, with one refusal a human can overturn* — rather than deleting.

**Four claims a reader might expect to find here and will not, because they
are measurably false.** (This intro said "Two" from M2.1 until M3 Task 5B,
while the list beneath it ran to three — a third item was added in M2.2 and
the count above it was not. Recorded rather than silently corrected, because
it is the same stale-prose class this repository keeps catching in its own
prose: a sentence true when written, falsified by an edit two lines below it.)

1. *"One convention end to end."* Not true, and the true statement is
   stronger: **the trace holds together end to end across components that do
   not agree on attribute names.** **agentgateway v1.5.0 — the pinned
   version, and this claim is scoped to it** — emits `http.method` /
   `http.path` / `http.status`, matching neither the stable HTTP semconv set
   this project's own services emit (`http.request.method`,
   `http.response.status_code`, `server.address`, via
   `OTEL_SEMCONV_STABILITY_OPT_IN=http`) nor the OTel legacy spelling
   (`http.target`, `http.status_code`) — and `jwt.sub` where semconv has
   `user.id`. **The version qualifier is load-bearing, not pedantry:**
   upstream PR
   [`#3182`](https://github.com/agentgateway/agentgateway/pull/3182) fixes the
   HTTP half and is **already merged** — but it merged 2026-09-01, after
   v1.5.0 shipped on 2026-08-27, so it is absent from the image this system
   runs. Stated version-free, the sentence becomes false at the next image
   bump with nothing to catch it; scoped to v1.5.0 it stays true, and
   `tests/e2e/test_gateway_trace.py`'s exact set equality is what goes red on
   the bump. (`jwt.sub` → `user.id` is in neither that PR nor any upstream
   issue.) Measured, pinned by `tests/e2e/test_gateway_trace.py` by exact
   set equality, and written up as Finding 7 in
   [`docs/upstream-findings.md`](upstream-findings.md). Trace context is what
   makes the chain one thing; attribute naming is what makes it queryable,
   and the two are separable. The real-world case is the more interesting one
   to demonstrate than a demo-world one would be.
2. *A whole-trace span total.* This document publishes none **as a property of
   the system** — the four figures below are one dated variance measurement,
   which is the thing being demonstrated rather than a number to rely on, and
   is the case the plan's rule carves out. The trace is
   non-deterministic: the `a2a-sdk`'s
   `a2a.server.events.event_queue.EventQueue.dequeue_event` polling span was
   measured varying **10–16** across identically-shaped runs, and **M2.2 adds a
   second source of variance** — a denied settlement short-circuits the whole
   subtree below the refused tool call, so a denied run is genuinely smaller
   than a permitted one. Measured for M2.2 across four browser-rooted traces:
   125, 123, 119, 117 spans, same shape every time. The "62 spans" figures
   further down are M1.2-era captures, kept with their own stated provenance
   rather than restated as current. What is stable, and therefore what gets
   published and asserted, is trace **shape** — service, operation, kind,
   parent.
3. *That the refusal is a red span.* It is not, and that is a decision rather
   than an omission. The `policy.decision` span's status stays `Unset` on a
   deny: a refusal is the platform working correctly, and an ERROR span for
   the one thing this milestone exists to do would read as a fault. The
   refusal is legible from the attributes (`policy.decision=deny`, the rule
   that fired, the amount and the limit) and from agentgateway's own
   `otel.status_description` one span up — where agentgateway *does* set
   ERROR, because from its point of view the proxied call failed.
4. *That the approval is in the same trace as the refusal.* **M3, and the
   fourth item this list has needed.** It is not, and that is the design: the
   human acts minutes later, from a different browser tab, in a process that
   did not exist when the refusal happened. There are **two traces**, joined
   by a span **link** (M8: from `review.decision`; M3 to M7: from `settlement.execute_approved`) back to the span that
   recorded the refusal — which is why the ledger's `refusal` object persists
   a trace id *and* a span id. A reader looking for one trace that contains
   both the deny and the approved execution will not find it, and should not:
   holding a span open across a human's coffee break would misstate both the
   duration and what was executing. See "Gap 2 (closed, M3)" in
   [`docs/telemetry-contract.md`](telemetry-contract.md) for the full argument
   and for what the closure deliberately does not cover.

**Provenance (fixed by the M1.2 final-review fix round, SF-6):** this
document previously claimed every claim in it was checked against HEAD
`0a1a1f7` — a commit that is an ancestor of the M1.1 merge point
(`f104109`), predating every line of M1.2 behaviour this document describes
(the PydanticAI agent, the ledger/model split, Task 8's CLIENT span). That
was false for every M1.2-era claim below. The truth, from `git log --
docs/architecture.md`: this file's M1.1-era content was checked against
`0a1a1f7`; its M1.2 content was added and last checked against the code and
the live `kind-northstar` cluster as of commit `85882e9` ("instrument httpx2
so the model call gets its CLIENT span" — Task 8, the last commit to touch
this file). This fix round corrected the provenance claim itself; it did not
re-run a fresh verification pass over every claim in this document against
the current worktree HEAD (`8e1f030`) — see "See it yourself" for how to
reproduce each capture yourself if you want to check further.

**What M2.1 changed, and where each change is described.** Earlier revisions
listed these here as notes *outside* the hop-by-hop text, because that text
was deliberately a record of M1.1's shape. M2.1 Task 5 folded them in
instead, so the document describes the system that is actually deployed:

1. **Task 1** replaced kind's default CNI with **Cilium 1.20.1** (a
   create-time `disableDefaultCNI` in `deploy/kind/stage2.yaml`). It sits
   underneath every hop below and changes none of them: the trace survived
   the swap with all 61 distinct `(service, operation, kind, parent)` rows
   identical pre and post. `kubeProxyReplacement` is deliberately off, and
   `kube-proxy` is still running. At M2.1 there was **no `toFQDNs` egress
   policy** — only its `l7Proxy` prerequisite was confirmed present, and
   nothing restricted outbound traffic. **M2.2 changed that**; see "The
   network, as the second defence" below.
2. **Task 2** deployed **agentgateway v1.5.0** into `northstar-platform` as a
   plain Deployment with a ConfigMap-mounted `config.yaml` — **not** the Helm
   chart, which installs a Gateway API control plane with nowhere to put this
   milestone's policy block. See "The components".
3. **Task 3** interposed it on the MCP hop. `settlement-agent`'s
   `PAYMENTS_MCP_URL` now names the gateway, so what was Hop 3 is now two
   hops — **Hop 3a** and **Hop 3b** below. The chain remains one distributed
   trace, with the gateway a visible node in it rather than a fracture.
4. **Task 4** made workload identity real on that hop: **SPIRE 1.15.3**
   (charts `spiffe/spire-crds` 0.6.1 then `spiffe/spire` 0.30.2) issues
   `settlement-agent` a JWT-SVID and the gateway verifies it. See "Workload
   identity: what is real and what is deferred".
5. **Task 5** proved the verification is load-bearing rather than
   decorative — `tests/e2e/test_caller_identity.py` — and wrote this
   revision. See "Identity with teeth".

**What M2.2 changed, in the order it happened.** Same convention: each item
names where it is described.

1. **`d17309c`** made the choke point enforceable rather than conventional: a
   `CiliumNetworkPolicy` on `payments-mcp`'s **ingress** admitting only
   agentgateway. Before it, `claims-agent` could POST an MCP `initialize`
   straight at `payments-mcp:8000` and get HTTP 200 — measured. See "Hop 3".
2. **The Policy Decision Service** (`platform/policy-decision-service`), a
   gRPC service implementing agentgateway's `ExtMcp` contract, holding one
   rule and emitting one span. See "The components" and "The decision the
   platform makes".
3. **`mcpGuardrails`** on the gateway's MCP route, calling that service on
   every `tools/call`, `failureMode: failClosed`. The deny became real. See
   "Hop 3a".
4. **A terminal denial path in `agents/settlement-agent/mcp_tools.py`**, so
   the model sees a refusal it cannot retry and narrates it. See "The decision
   logic".
5. **Cilium `toFQDNs` egress policies** on all three `northstar-agents`
   workloads, so the model provider is reachable from exactly one pod and the
   gateway is the only path to a tool. See "The network, as the second
   defence".

**What M3 changed, in the order it happened.** Same convention again: each
item names where it is described, and each is folded into the hop-by-hop text
below rather than left as a note outside it.

1. **Task 2 — the refusal became a record.** `payments-api` grew a third
   status (`refused`), a `refusal` object beside the payment, and two routes:
   `POST /payments/{id}/refuse` and `GET /payments`. See "Hop 4", where both
   are captured live. Nothing about it is authorization — it is the ledger
   writing down what the platform decided.
2. **Task 3 — a second identity, and an anti-bypass check.**
   `senior-settlement-agent` is the settlement-agent image in a second
   Deployment under a second ServiceAccount, so the Policy Decision Service
   grants it €25,000 on its own allowance row. `refuse_payment` and
   `get_payment` joined `UNLIMITED_TOOLS`; `refuse_payment` also became
   caller-scoped. `execute_approved` refuses to execute unless the ledger
   already shows a matching refusal (`UnverifiedApproval`), and both
   settlement Deployments got Cilium **ingress** allowlists — they had
   `Allow Ingress ANY` before, so anything reaching an A2A port could execute
   any proposed payment. See "The decision the platform makes" and "Workload
   identity".
3. **Task 4 — the human.** `reviewer-ui`, a FastAPI page with no build step,
   reading refusals straight from payments-api's REST API and POSTing an
   approval to the senior agent over A2A. It emits the `review.decision` span
   and mounts the Workload API socket only to read its own SPIFFE ID. See
   "The components" and "See it yourself".
4. **Task 5A — the seven exit criteria**, as `tests/e2e/test_supervised.py`,
   six of its seven tests behind `RUN_BROWSER_TESTS=1`.
5. **Task 5B — this revision**, plus the OQ-U1 measurement in
   [`docs/upstream-findings.md`](upstream-findings.md) and a memory limit for
   `reviewer-ui` set from a cgroup measurement after it was OOMKilled three
   times during Task 5A.

This document does not duplicate [`docs/telemetry-contract.md`](telemetry-contract.md),
which is the authority on exactly what instrumentation is enabled, what it
emits, and the convention gaps found in it. This document is about the
system's shape: who talks to whom, over what protocol, and what a reader
follows through Jaeger to see it. Where the two overlap (coverage numbers,
span names), the numbers here were re-measured against a fresh live trace,
not copied — they agree, and any future disagreement between this file and
the contract should be treated as a bug in one of them, not resolved by
picking one silently.

## The chain, in one picture

<pre class="mermaid">
flowchart TD
    browser["`browser
      TS, manual root span`"]
    claims["`claims-agent
      Python, a2a-sdk`"]
    settle["`settlement-agent
      Python, a2a-sdk`"]
    gw["`agentgateway
      Rust, v1.5.0
      SVID verified · policy consulted`"]
    pds["`policy-decision-service
      Python, gRPC ExtMCP`"]
    mcp["`payments-mcp
      Python, FastMCP`"]
    api["`payments-api
      Go, otelhttp
      system of record`"]

    browser <-->|"`Hop 1 — A2A/JSON-RPC over HTTP
      message/send, JSON-RPC reply`"| claims
    claims <-->|"`Hop 2 — A2A/JSON-RPC over HTTP
      message/send, JSON-RPC reply`"| settle
    settle -->|"`Hop 3a — MCP over streamable HTTP
      identity in an Authorization: Bearer JWT-SVID header
      trace context in BOTH a traceparent header
      and JSON-RPC params._meta`"| gw
    gw <-->|"`Hop 3p — gRPC, on EVERY tools/call, BEFORE the
      call reaches payments-mcp. failureMode: failClosed.
      A deny stops here.`"| pds
    gw -->|"`Hop 3b — MCP again, same transport, ONLY if the policy
      permitted. The gateway STRIPS the credential and
      re-stamps _meta with its own outbound span`"| mcp
    mcp -->|"`Hop 4 — plain HTTP/JSON
      otelhttp client to otelhttp server`"| api
</pre>

**M5's flag lookup is a third picture, for the same reason M3's approval path
below is a second one rather than another arrow above**: it happens inside a
single box in the picture above, not between two of them, and drawing it as a
branch off `policy-decision-service` would either crowd the Hop 3p annotation
beside it or misstate the lookup as a network hop between pods when it is a
localhost call inside one pod.

<pre class="mermaid">
flowchart TD
    subgraph pod["one pod — no Service, no second Deployment"]
        pds["`policy-decision-service
      Python, gRPC ExtMCP
      pds/autonomy.py`"]
        flagd["`flagd
      sidecar, OFO-injected
      provider: kubernetes`"]
        pds -->|"`Hop 3f — gRPC, localhost:8013, INSIDE the same pod.
      flagd is an initContainer the Open Feature Operator
      injects into this pod's spec.`"| flagd
    end
    ff[["`FeatureFlag/northstar-autonomy
      a Kubernetes object`"]]
    flagd -->|"watches — a real watch, not a poll"| ff
</pre>

Every `tools/call` the PDS decides makes this lookup once, before Hop 3p's
verdict — `pds/autonomy.py::resolve_autonomy` asks flagd for the
`autonomy-level` variant, keyed on the caller's SPIFFE ID from the same
evaluation context Hop 3p already carries, and the answer becomes
`policy.autonomy.level` on the `policy.decision` span. This is also the one
hop in the whole chain that changes behaviour without a code deploy: the
`northstar-autonomy` `FeatureFlag` object can be patched live (`kubectl patch
featureflag/northstar-autonomy -n northstar-platform ...`), and flagd's
Kubernetes watch means the very next lookup sees it — no pod restart, no
image rebuild. `tests/e2e/test_progressive_autonomy.py` is built on exactly
that property, and its own second test confirms the property holds by
comparing pod UIDs and image digests before and after a flip: nothing was
redeployed to make the same claim settle instead of refuse.

**M3's approval path is a second picture, not another arrow in this one**, and
drawing it separately is the accurate thing rather than the tidy thing. It
starts minutes later, from a different browser tab, in a different trace:

<pre class="mermaid">
flowchart TD
    human["`human
      browser`"]
    rui["`reviewer-ui
      Python, FastAPI`"]
    api["`payments-api
      the same one`"]
    senior["`senior-settlement-agent
      the settlement-agent image,
      second identity`"]
    gw["`agentgateway
      same gateway, same jwtAuth, same PDS —
      different jwt.sub, different allowance row`"]
    mcp["payments-mcp"]

    human <-->|"plain HTML — form POST, rendered HTML"| rui
    rui -->|"REST, no MCP — GET /payments?status=refused"| api
    rui -->|"`A2A/JSON-RPC over HTTP, message/send,
      carrying the approval and the refusal's
      trace id and span id`"| senior
    senior --> gw
    gw -->|"Hop 3p, then permit only"| mcp
    mcp --> api
</pre>

Two things that picture is making explicit. **reviewer-ui reaches payments-api
directly and is forbidden from reaching the governed path at all** — no
agentgateway, no payments-mcp, no Policy Decision Service — which is enforced
by `reviewer-ui-egress-allowlist` rather than left to convention. And **the
approval rejoins the original chain at exactly the choke point**, so the human
override is authorized by the same mechanism as everything else; it is not a
side door.

This agrees with `README.md`'s one-line version; these diagrams just name the
transport and the payload shape at each arrow, which the README's version
doesn't attempt.

**Hop 3p is drawn as a branch off the gateway rather than as a step in the
chain, deliberately, and the trace agrees.** The decision is not a hop the
claim travels through — it is a question the gateway asks and a verdict it
acts on. In Jaeger both the gateway's `Guardrail` CLIENT span and the PDS's
`policy.decision` SERVER span are **children of the gateway's `tools/call`
SERVER span**, siblings of each other, and the onward
`tools/call payments-mcp_<tool>` CLIENT span is their sibling too — present
on a permit, absent on a deny. Measured; see
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "agentgateway's
guardrail telemetry, as measured".

## The components

M1.1 had five, all of them this project's own code. M2.1 added platform
infrastructure this project runs but did not write, so the two are listed
separately — the distinction matters for the no-invented-telemetry rule,
which applies to the second table without exception. **M2.2 adds a sixth
component of this project's own**, and it is deliberately in neither of those
two groups: the Policy Decision Service is code this repository writes, but it
is platform machinery rather than an application component, so it gets its own
row in the platform table with that distinction stated in the cell.

**M3 adds two more, both application components, and one of them is a
borderline case worth naming.** `reviewer-ui` is straightforwardly an
application: new code, its own image, a surface a human touches.
`senior-settlement-agent` is listed in the application table too, and it is
the only row in this document that describes **no code and no image of its
own** — it is `settlement-agent`'s image in a second Deployment. It earns a
row because a reader following a trace will find a pod by that name, and
because the interesting property of M3 is exactly that the difference between
the two is an identity rather than a program. The alternative — mentioning it
only in a footnote to `settlement-agent`'s row — would have hidden the thing
the milestone is about.

### The application components — five at M1.1, seven as of M3, and one more in `northstar-platform` since M4

**M4 added an eighth first-party component, `platform/eval-scorer`, and for
two milestones it was in neither inventory** (M4 re-review, finding L9). It
was deliberately absent from the table below rather than forgotten: this
table is the *application* plane, and eval-scorer runs in `northstar-platform`
beside the platform components. But the section below that one used to open
"none is this project's code", which eval-scorer falsified — so it had no row
in either inventory for the whole of M4. **M5 Task 6 closed this**: that
section's opening sentence was rewritten (it no longer claims "none is this
project's code" — see "The platform components M2.1 through M5 add" below,
which now names `eval-scorer` as one of two rows that are this project's own
code), and eval-scorer was given its row there. Left standing here, past
tense, rather than deleted, per this document's own convention for a gap that
has since been closed — a reader counting components from this heading no
longer comes up one short.


Namespaces, ports, and service DNS names below are read directly from
`deploy/k8s/*.yaml`, not from memory or from the diagram above.

| Component | Language / framework | Namespace | Service (DNS) : port | Image | Role |
|---|---|---|---|---|---|
| `customer-ui` | TypeScript, Next.js 15.5.21 (`output: "export"`, static, served by nginx 1.27) | `northstar-agents` (**not** its own namespace — see note below) | `customer-ui.northstar-agents.svc.cluster.local:8080`, exposed as a `NodePort` (30080) mapped to host `8080` by kind's `extraPortMappings` | `localhost:5001/northstar/customer-ui:dev` | Owns the trace root. The only component a human touches directly. |
| `claims-agent` | Python 3.13, `a2a-sdk[telemetry]==0.3.7` + Starlette | `northstar-agents` | `claims-agent.northstar-agents.svc.cluster.local:9001` | `localhost:5001/northstar/claims-agent:dev` | A2A server (inbound, from the browser) and A2A client (outbound, to settlement-agent). Parses the claim, delegates, returns settlement-agent's JSON verbatim. |
| `settlement-agent` | Python 3.13, `a2a-sdk[telemetry]==0.3.7` + Starlette + `mcp==2.2.0` client (2026-09-11; was `1.28.1`, and `1.9.4` before that) + `pydantic-ai-slim==2.42.0` (M1.2; 2.41.0 until 2026-09-11) | `northstar-agents` | `settlement-agent.northstar-agents.svc.cluster.local:9002` | `localhost:5001/northstar/settlement-agent:dev` | A2A server; MCP client. As of M1.2, the settlement decision itself is a real model call (see "The decision logic" below) — the model chooses whether and how to propose and execute the payment via payments-mcp. |
| `payments-mcp` | Python 3.13, FastMCP 4.0.3 | `northstar-tools` | `payments-mcp.northstar-tools.svc.cluster.local:8000`, MCP endpoint at `/mcp` | `localhost:5001/northstar/payments-mcp:dev` | MCP server exposing **four** tools as of M3 — `propose_payment` and `execute_payment` since M1.1, plus `refuse_payment` and `get_payment` from M3 Task 3 (this row said "two tools" until M3 Task 5B, and `contracts/mcp/payments-mcp.json` still listed only two, which is what made the gate red). An adapter over payments-api — it owns no state of its own (`server.py`'s own docstring: "This is an adapter over payments-api, not a system of record."). |
| `payments-api` | Go 1.27.1, `otelhttp v0.71.0` / otel core `v1.46.0` (**2026-09-11 (M3): was Go 1.24, `otelhttp v0.66.0` / otel core `v1.41.0`** — see Finding 11) | `northstar-systems` | `payments-api.northstar-systems.svc.cluster.local:8080` | `localhost:5001/northstar/payments-api:dev` | The system of record for payments. In-memory store, sequential IDs, no persistence. As of M3 it also records **why** a payout did not happen (`POST /refuse`) and lists them (`GET /payments`) — a record, not a gate. |
| `reviewer-ui` **(M3 Task 4)** | Python 3.13, FastAPI 0.141.1 + uvicorn, server-rendered HTML strings — **no frontend toolchain and no build step**, deliberately unlike `customer-ui` | `northstar-agents` | `reviewer-ui.northstar-agents.svc.cluster.local:8080`, exposed as a `NodePort` (30081) mapped to host `8081` by `deploy/kind/stage3.yaml` | `localhost:5001/northstar/reviewer-ui:dev` | The page a human reads and clicks. Lists refusals straight from payments-api's REST API (never through the MCP path), and POSTs an approval to `senior-settlement-agent` over A2A. Emits the `review.decision` span. |
| `senior-settlement-agent` **(M3 Task 3; retired in M8)** | **No image and no code of its own** — the `settlement-agent` image, byte-identical, in a second Deployment under a second ServiceAccount | `northstar-agents` | `senior-settlement-agent.northstar-agents.svc.cluster.local:9002` | `localhost:5001/northstar/settlement-agent:dev` (the same mutable `:dev` tag settlement-agent runs) | Executes what a human approved, under an identity the Policy Decision Service grants a higher limit. "Senior" is an *identity*, not a different program — which is the entire point, and the reason this row's Image cell repeats another row's. |

**Namespace note, worth stating plainly:** `customer-ui` deploys into
`northstar-agents` — the same namespace as `claims-agent` and
`settlement-agent` — not a namespace of its own (confirmed in
`deploy/k8s/customer-ui.yaml`, `deploy/k8s/namespaces.yaml`, which
declares four: `observability`, `northstar-agents`, `northstar-tools`,
`northstar-systems`). `scripts/setup-kind.sh` creates a fifth,
`northstar-platform`, at stage 2 for M2.1's platform components —
deliberately outside `namespaces.yaml`, since it holds infrastructure this
project configures but does not own (see "The platform components M2.1
adds" below, which lives there). A reader expecting one namespace per
component (the
plane labels — `northstar.io/plane: agents/tools/systems` — encourage that
reading) would be wrong about `customer-ui` specifically; it shares the
agents' namespace even though it isn't itself an agent.

**2026-09-15, whole-branch review (N2): this said "Five namespaces exist in
the cluster (`kubectl get ns` confirms it)", which was already an
undersell of the literal command even at M4 (kind's own `default`,
`kube-system`, `kube-public`, `kube-node-lease`, `local-path-storage`, plus
Cilium's `cilium-secrets`, were always present alongside the five named
above) and became flatly false once M5's `setup-kind.sh` added two more
project-relevant namespaces of its own: `cert-manager` (Step 8f) and
`open-feature-operator-system` (Step 8g) — both described in their own right
under "The platform components M2.1 through M5 add" below.** Measured live,
2026-09-15:

```
$ kubectl get ns
NAME                           STATUS
cert-manager                   Active
cilium-secrets                 Active
default                        Active
kube-node-lease                Active
kube-public                    Active
kube-system                    Active
local-path-storage             Active
northstar-agents                Active
northstar-platform              Active
northstar-systems                Active
northstar-tools                  Active
observability                    Active
open-feature-operator-system     Active
```

**13**, not five. Of those, **seven** are project-relevant (the five named
above, plus `cert-manager` and `open-feature-operator-system`) and six are
kind's own defaults or Cilium's, present regardless of what this project
adds. The corrected claim, so a future reader does not have to re-derive
which count the sentence meant: **seven project-relevant namespaces**, and
**13** if `kubectl get ns` is read literally as the sentence itself invited.

What each component is explicitly **not** responsible for:

- **customer-ui** does not talk to anything but claims-agent and the OTel
  Collector. It has no server-side logic beyond a static export served by
  nginx — no API routes, no session state, no auth.
- **claims-agent** does not assess or settle anything itself. It parses the
  claim (`intake.py`, deliberately strict JSON validation, no LLM), and
  delegates unconditionally to settlement-agent. It does not decide whether
  the delegation should happen — the allow-list below governs the target,
  not the decision.
- **settlement-agent** does not talk to payments-api directly and does not
  own payment state. As of M1.2 it *does* reason about the claim — a real
  model call decides whether and how to settle it — but the reply's amounts
  still come from what payments-api's ledger recorded, never from the
  model's own words (see "The decision logic" below).
- **payments-mcp** does not own payment state. It has no logic of its own
  beyond translating an MCP tool call into an HTTP call to payments-api and
  returning the result (`payments_client.py`).
- **payments-api** does not know anything about A2A, MCP, agents, or claims
  beyond the `claimId` string it is handed. It has no authorization of any
  kind — see "What the system deliberately does not do" below; this is
  stated in its own OpenAPI contract (`contracts/openapi/payments-api.yaml`):
  *"Deliberately tiny. In M1 it has no authorization of any kind: anything
  that can reach it can move money."* M3 did not change that: it does not
  decide whether a refusal was justified or whether an approval is real, it
  writes down what it is told and refuses only self-contradictory
  instructions.
- **reviewer-ui** does not decide anything and does not touch the governed
  path. It has no `EXECUTE_LIMITS` row, no MCP client, and no route to
  agentgateway, payments-mcp or the Policy Decision Service — enforced, not
  conventional (`reviewer-ui-egress-allowlist`). It reads the ledger, renders
  it, and forwards a click. A `reject` is accepted and is inert by design: it
  records a span and writes nothing.
- **senior-settlement-agent** does not contain a higher limit, or any limit.
  It is the same code; the difference is entirely in which SPIFFE ID the
  Workload API hands its pod and which row the Policy Decision Service then
  finds. Nothing in the image knows it is the senior one — and one consequence
  is worth knowing before opening Jaeger: its `OTEL_SERVICE_NAME` is
  `settlement-agent`, the same as the junior's (read from
  `deploy/k8s/senior-settlement-agent.yaml`), so **its spans arrive under
  `service.name=settlement-agent`**. The two are told apart in the trace by
  `jwt.sub` on the gateway's span and by `policy.caller.spiffe_id` on the
  PDS's decision, not by service name. That is a consistent choice rather than an
  oversight — the service is the same service — but it is the one place where
  "senior is only an identity" makes the telemetry read oddly.

### The platform components M2.1 through M5 add

All of these live in `northstar-platform` (or a namespace of their own, for
the two M5 controllers below); most are third-party infrastructure this
project configures but does not write — the no-invented-telemetry rule
applies to that majority without exception. **Two rows are this project's
own code, and each says so in its own cell rather than being folded into
this heading's old, now-false claim**: `policy-decision-service` (M2.2) and
`eval-scorer` (M4, given a row here in M5 Task 6 — see the note under the
application-components heading above for why it was left out of both
inventories for two milestones). **M6 adds a third, partial case**:
`backstage` itself is third-party framework code this project configures,
same as agentgateway or SPIRE, but one plugin inside it
(`autonomyContract/`) is this project's own — the same distinction the
no-invented-telemetry rule needs, drawn at plugin granularity rather than
service granularity because that is where the boundary actually falls.
Images and versions below were read from the live cluster, enumerating over
every pod's `.spec.initContainers` **and** `.spec.containers` — not
`containerStatuses`, which omits init containers and would have missed two of
the six SPIRE sub-images, including the one carrying the hazard recorded under
"Deferred hardening" below.

| Component | What it is | Version / image | Role in this system |
|---|---|---|---|
| `agentgateway` | Rust MCP/A2A/LLM proxy. A **Linux Foundation** project — **not** CNCF | `cr.agentgateway.dev/agentgateway:v1.5.0` (note the leading `v`; the tag `1.5.0` does not exist on that registry, though the binary reports `1.5.0` as its internal version) | Interposed on the MCP hop. Verifies the caller's JWT-SVID (`jwtAuth`), asks the Policy Decision Service about every `tools/call` (`mcpGuardrails`), and forwards to `payments-mcp` **only** if the answer is permit. |
| `policy-decision-service` | **This project's own code** (Python 3.13, `grpcio`), not third-party — listed here because it is platform machinery rather than an application component. Serves agentgateway's `agentgateway.dev.ext_mcp.ExtMcp` contract, from a proto vendored verbatim at v1.5.0 (`platform/policy-decision-service/proto/ext_mcp.proto`, `d459d88`) | `localhost:5001/northstar/policy-decision-service:dev`, `policy-decision-service.northstar-platform.svc.cluster.local:9100` | Answers permit or deny on caller identity plus amount. Holds the milestone's one rule and emits the `policy.decision` span. Makes no Kubernetes API calls and holds no RBAC; nothing in `northstar-agents` can reach it. |
| `eval-scorer` **(M4; row added M5 Task 6)** | **This project's own code** (Python 3.13, standard library only — no web framework, no third-party OTel SDK dependency beyond what `platform/eval-scorer/requirements.txt` pins), not third-party — the same reason `policy-decision-service` has a row here rather than in the application table | `localhost:5001/northstar/eval-scorer:dev`, `eval-scorer.northstar-platform.svc.cluster.local:8300` | Scores a settled trajectory's identity authority against its own independent copy of the policy table (`scorer/authority.py`) and its own independent copy of the contract's currently-published autonomy level (`scorer/contract.py`'s `CURRENT_LEVEL`), never by re-reading the `policy.decision` span's own claim — the anti-vacuity argument `scorer/contract.py`'s own module docstring makes at length. `CURRENT_LEVEL` is a static constant checked at build time against the committed `deploy/platform/openfeature/featureflag.yaml`, deliberately blind to a live `kubectl patch` against the deployed `FeatureFlag` object (M5-R29/M5-R31) — the two are independent by design, not by oversight, which is exactly what let Task 6 flip the live contract without touching this service or its tests. |
| Kyverno **(M7)** | Kyverno, chart `kyverno/kyverno` **3.9.1**, installed in the `kyverno` namespace by `setup-kind.sh --stage 7` with `deploy/platform/kyverno/values.yaml` (OTLP tracing on the admission controller; read-only RBAC on the kinds the policy matches) | appVersion **v1.19.1**; one `ValidatingPolicy` (`policies.kyverno.io/v1`, CEL), `deploy/platform/kyverno/keystone-policy.yaml`, named `agents-cannot-edit-their-own-authority` | The keystone: refuses an agent identity's create, update or delete of the objects that define its own authority. See "The rings, and the keystone" below. `failurePolicy: Fail`. |
| `backstage` **(M6)** | Backstage backend, `@backstage/backend-defaults@^0.17.7` and siblings — third-party framework, plus **this project's own code** in one plugin: `platform/backstage/packages/backend/src/plugins/autonomyContract/` (router, k8s client, evaluator, telemetry), the same reason `policy-decision-service` and `eval-scorer` have rows here rather than in the application table, scaled down to one plugin rather than a whole service | `localhost:5001/northstar/backstage:dev`, `backstage.northstar-platform.svc.cluster.local:7007` | Serves the software catalog (nine `Component` entities: the eight in the tree plus Backstage itself, derived from `catalog-info.yaml` files staged into the image at build time — see "Known limitations" below) and the autonomy-contract card, the one UI in this system with a write path of its own: `POST /api/autonomy-contract/variant` flips `northstar-autonomy`'s `targeting.if[1]` through a second, narrower ServiceAccount (`backstage-autonomy-writer`) than the one the Kubernetes plugin reads with. |
| SPIRE server / agent | SPIFFE runtime, chart `spiffe/spire` 0.30.2 from the hardened repo | appVersion SPIRE **1.15.3**; `ghcr.io/spiffe/spire-server:1.15.3`, `spire-agent:1.15.3` | Issues the JWT-SVID. Trust domain `northstar.local`, cluster name `northstar`. |
| SPIRE CRDs | chart `spiffe/spire-crds` 0.6.1, installed first | — | The `spire.spiffe.io` CRDs the `ClusterSPIFFEID` resources need. |
| `spire-controller-manager` | ships inside the SPIRE chart | `ghcr.io/spiffe/spire-controller-manager:0.7.0` | Reconciles `ClusterSPIFFEID` resources into real registration entries. |
| `spiffe-csi-driver` (+ `node-driver-registrar`) | ships inside the SPIRE chart | `ghcr.io/spiffe/spiffe-csi-driver:0.2.7`, `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.15.0` | Bind-mounts the Workload API socket into `settlement-agent`'s pod at `/spiffe-workload-api/spire-agent.sock`. |
| `spiffe-oidc-discovery-provider` (+ `nginx` sidecar) | ships inside the SPIRE chart | `ghcr.io/spiffe/oidc-discovery-provider:1.15.3`, `docker.io/nginxinc/nginx-unprivileged:1.30.0-alpine` | Publishes SPIRE's JWT signing keys as a JWKS, which the gateway fetches. Plain HTTP inside the cluster — recorded as a gap, not hidden. |
| `cert-manager` (+ `cainjector`, `webhook`) **(M5 Task 2)** | Upstream Kubernetes certificate controller, applied straight from its release manifest (`kubectl apply -f cert-manager.yaml`), not Helm | `v1.21.2`; `quay.io/jetstack/cert-manager-controller:v1.21.2`, `-cainjector:v1.21.2`, `-webhook:v1.21.2` | Its own namespace, `cert-manager`, not `northstar-platform`. A prerequisite for the Open Feature Operator's admission webhook, which needs a signed TLS certificate to run; issues and rotates that certificate. Reads and writes no `northstar-*` resource. |
| Open Feature Operator (OFO) | Helm chart `openfeature/open-feature-operator`, installed by `scripts/setup-kind.sh` | chart `v0.9.3`; `ghcr.io/open-feature/open-feature-operator:v0.9.3` | Its own namespace, `open-feature-operator-system`. Watches `FeatureFlag` and `FeatureFlagSource` custom resources (`core.openfeature.dev/v1beta1`) and, on a pod carrying the `openfeature.dev/enabled`/`openfeature.dev/featureflagsource` annotations, mutates that pod's spec at admission time to add a `flagd` sidecar — an `initContainer` with `restartPolicy: Always`, the mechanism that puts flagd inside `policy-decision-service`'s own pod below rather than behind a separate `Service`. |
| `flagd` | Sidecar, not its own pod — injected by the Open Feature Operator into `policy-decision-service`'s pod as an `initContainer` (`kubectl -n northstar-platform get pod -l app=policy-decision-service` shows it under `initContainers`, confirmed live rather than assumed from the chart) | `ghcr.io/open-feature/flagd:v0.16.2` | Watches the `northstar-autonomy` `FeatureFlag` object (`provider: kubernetes`, a live Kubernetes API watch, not file-polling) and answers flag evaluations over gRPC on `localhost:8013` inside the PDS pod — the port `pds/autonomy.py` calls. Also exposes a management/`healthz`+`/metrics` port (`:8014`, forwarded by `scripts/start-demo.sh` as `flagd-metrics`) and an OFREP REST port (`:8016`, `POST /ofrep/v1/evaluate/flags/{key}`) used to resolve a flag from outside the pod without a gRPC client — the mechanism `tests/harness/contract.py` uses to confirm a live flip actually resolved, from an ephemeral port-forward, before any test trusts it. |

**Not the Helm chart, for agentgateway, and that was a real decision.** The
gateway is a plain `Deployment` plus a ConfigMap-mounted `config.yaml`
(`deploy/platform/agentgateway/`), not `helm install agentgateway`. The chart
installs a Gateway API *control plane* — it expects `Gateway`/`HTTPRoute`
resources and a controller to translate them, and there is nowhere in that
model to put this milestone's single inline `jwtAuth` policy block. A
standalone binary reading one file is both the smaller thing and the more
legible one for a document like this to describe.

**Two versions of SPIRE, and they are not the same number.** The *chart* is
0.30.2 and the *product* is 1.15.3. `spiffe/spire` and `spiffe/spire-crds`
are independent tag series on the same repository, which is why
`deploy/platform/spire/values.yaml` pins two chart versions rather than one.

**What SPIRE contributes to the telemetry: metrics only.** SPIRE 1.15.3 has
no OpenTelemetry support of any kind — no OTLP exporter, no tracing, and its
telemetry configuration offers only Prometheus, DogStatsD, Statsd, M3 and an
in-memory sink. **So SPIRE emits no spans, and this document publishes no
SPIRE span attribute anywhere, because there are none to publish.** "It
cannot be traced" is the accurate statement. Its Prometheus metrics *are*
enabled and scraped into the collector as `service.name=spire-server` /
`spire-agent` — and since M4 Task 2 they leave the collector's `metrics`
pipeline over OTLP like every other metric, so at stage 4 they land in
Prometheus as well (271 distinct `spire_*` **metric names** were counted there
— names, not series, which is the unit `/api/v1/label/__name__/values`
returns; the series count is much larger. That is what makes them the
independent positive control for the collector→Prometheus hop). See
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "SPIRE
instrumentation, as measured".

## The waiting claim — A2A's own task lifecycle (M8)

The Hop 1 description below is the M1 shape (a blocking `message/send`) and is
kept as dated history. Since M8 the customer-ui submits a claim with
`message/stream`, and the claim is an A2A task with real states:
`submitted → input-required → completed` when a payment is routed to a human,
or `submitted → completed` when the settlement agent settles it.

- **The claim stream** returns at `input-required` (or at a terminal state) and
  the browser closes it. Its first event carries the task id. The status message
  is one JSON text part with `claimId`, `policyNumber`, `paymentId`, `amount`,
  `currency`, `status`, `narrative`, `rule`, `limit` and `decision`.
- **The wait is one stream.** The browser sends `tasks/resubscribe` and holds it
  until the task is `completed`. sse-starlette sends a `: ping` every 15 s. If
  the stream drops, the customer-ui reads the task once with `tasks/get` and
  resubscribes. That is a reconnect, not polling. A 10-minute hold was measured
  across the real path (`agents/claims-agent/NOTES.md`).
- **The first `execute()` stays running.** The claims-agent parks it on an
  in-process future while the task is `input-required`. The SDK removes a
  task's event queue when `execute()` returns, and `tasks/resubscribe` needs
  that queue, so the parked call is what makes the resubscribe possible. The
  price is that a claims-agent restart loses the waiting claims.
- **Both status updates are `final=False`.** The SDK's event consumer closes the
  root queue immediately on a final update. The stream ends instead when the
  parked `execute()` returns and the SDK drains the queue. Clients end on the
  task state.
- **The reviewer-ui resumes the same task id** with `tasks/get` and then a
  non-blocking `message/send` carrying the decision. The claims-agent does not
  trust that message. It reads the payment from payments-api (one
  `GET /payments/{id}`, allowed by an L7 Cilium rule) and reports what the
  ledger says: `executed` is "Settled", `rejected` is "Declined by a reviewer".
  A resume the ledger does not back is refused and the task keeps waiting.
- **A decline is `completed`, not A2A's `rejected`.** In A2A `rejected` means
  the agent refused the task. Here the agent did the work and a person declined
  the payment.
- **`refusal_unrecorded`** (the settlement agent could not record the refusal on
  the ledger) completes the task at once with a message that the claim could not
  be routed for review. Nobody could find that payment, so waiting would be for
  nobody.
- **Trace A keeps one long span.** The SDK's `_run_event_stream` SERVER span
  lasts as long as the parked `execute()`, so it is open in trace A for the whole
  wait (measured 83.2 s for a 75 s wait; the `POST /` span itself ended when the
  customer closed the claim stream). See `docs/telemetry-contract.md`.

## Hop 1 — browser → claims-agent: A2A over JSON-RPC 2.0

The browser is an A2A client. `apps/customer-ui/src/a2a.ts` builds a
JSON-RPC 2.0 `message/send` envelope by hand (it deliberately does not use
`@a2a-js/sdk` — see `apps/customer-ui/NOTES.md` §1: the SDK's current line
targets A2A protocol `1.0`, while claims-agent's agent card advertises
`protocolVersion: "0.3.0"`, and reconciling that skew for a demo UI wasn't
judged worth it) and POSTs it to `claims-agent`'s `/` endpoint.

The form (`apps/customer-ui/src/app/page.tsx`) pre-fills:

```json
{
  "claimId": "CLM-1842",
  "policyNumber": "POL-77120",
  "description": "Rear-end collision at a junction",
  "estimatedAmount": 6200
}
```

**Real request, captured from a live Playwright-driven submission against
the deployed pod** (`POST http://localhost:19001/`, i.e. the port-forwarded
claims-agent):

Headers of interest: `content-type: application/json`,
`traceparent: 00-ddf2747a385885345c0eeb25997d53ba-bd2489e913a5994b-01`
(injected by `injectContext` in `apps/customer-ui/src/telemetry.ts`, which
calls `propagation.inject(context.active(), headers)` — this is also where
`baggage` would ride if any were set at this point in the flow; none is,
since `northstar.claim.id` isn't minted until claims-agent's own intake).

```json
{
  "jsonrpc": "2.0",
  "id": "2b41f8d30d57401583e2039df1639b0d",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "0fe94b7d0e5b41629bb32c1983d740d0",
      "parts": [
        { "kind": "text", "text": "{\"claimId\":\"CLM-1842\",\"policyNumber\":\"POL-77120\",\"description\":\"Rear-end collision at a junction\",\"estimatedAmount\":6200}" }
      ]
    }
  }
}
```

Real reply, same capture:

```json
{
  "id": "2b41f8d30d57401583e2039df1639b0d",
  "jsonrpc": "2.0",
  "result": {
    "kind": "message",
    "messageId": "373ae387-28cd-4e4b-b9c2-f52634547f0f",
    "parts": [
      { "kind": "text", "text": "{\"claimId\": \"CLM-1842\", \"policyNumber\": \"POL-77120\", \"paymentId\": \"PAY-000061\", \"amount\": 6200, \"currency\": \"EUR\", \"status\": \"executed\", \"narrative\": \"Claim CLM-1842 is covered. Settling at 6200 EUR against the assessed damage.\"}" }
    ],
    "role": "agent"
  }
}
```

**That reply capture is M1-era, and is kept deliberately** — it is the
envelope shape, and the envelope shape has not changed. What M2.2 changed is
one value inside it. The same `CLM-1842`/€6,200 submission now comes back
with `"status": "refused"` rather than `"executed"`: settlement-agent still
proposes the payment, the platform refuses the execute, and
`agents/settlement-agent/settlement.py`'s refusal branch returns the *same
five keys* it returns on success (`amount`, `currency`, `status`,
`paymentId`, `narrative`) — sourced from what the ledger recorded for the
**proposal**, so `paymentId` names a real proposed-but-unexecuted payment and
`amount` is the proposed amount. Only `narrative` is the model's, and it
differs on every run, which is why no verbatim M2.2 reply is published here.
Transport is unchanged too: HTTP 200, `result` present, no JSON-RPC `error`.
So claims-agent and the browser parse a refusal exactly as they parse a
settlement.

The claim's `parts[0].text` is itself a JSON string (A2A wraps arbitrary
text; the claim payload happens to be JSON) — claims-agent's `intake.py`
parses that inner string, not the outer envelope, as the claim.

CORS: claims-agent's `CORSMiddleware` (`main.py`) allows origin
`http://localhost:8080` (from `CORS_ALLOWED_ORIGINS`) and explicitly allows
the `traceparent`, `tracestate`, and `baggage` headers — without that
allow-list entry the browser's preflight would strip them and the root span
would detach from everything downstream (`apps/customer-ui/NOTES.md` §4).
Separately, the browser's own OTLP export (the `claim.submit` span itself,
not the claims-agent call) goes to the OTel Collector at
`http://localhost:4318/v1/traces`, which needs its own, independent CORS
entry for `http://localhost:8080` (`deploy/observability/collector-config.yaml`)
— confirmed present.

## Hop 2 — claims-agent → settlement-agent: A2A again, behind an allow-list

claims-agent delegates by building the identical kind of JSON-RPC
`message/send` envelope (`delegation.py`) and POSTing it to whichever URL
`ClaimsAgentExecutor._delegate(name)` resolves. That resolution is gated by
an explicit, hardcoded allow-list, not by discovery:

```python
# delegation.py
ALLOWED_AGENTS = allowed_agents_from_env()   # NORTHSTAR_ALLOWED_AGENTS env var

# executor.py
def _delegate(self, name: str) -> A2ADelegate:
    try:
        return self._delegates[name]
    except KeyError:
        raise RuntimeError(
            f"claims-agent is not permitted to delegate to {name!r}; "
            f"permitted: {sorted(self._delegates)}"
        ) from None
```

In the deployed manifest, `NORTHSTAR_ALLOWED_AGENTS` has exactly one entry:
`settlement-agent=http://settlement-agent.northstar-agents.svc.cluster.local:9002`
(`deploy/k8s/claims-agent.yaml`). `delegation.py`'s own docstring states the
intent precisely: *"Which agents may be delegated to is configuration, never
discovery. In M1 that configuration is an environment variable; in M2 these
become agentgateway route names enforced at the gateway."*

**Corrected 2026-09-15 (M6 Task 1).** Until this correction, both this
document and the docstring it quotes went on to say *"in M6 Crossplane
generates them from the Agent CRD's `delegation.allowedAgents` field"* — the
M1 spec's original plan for M6 (`docs/superpowers/specs/
2026-09-07-northstar-m1-connected-design.md:256`). M5's own spec dropped
Crossplane and Argo CD in favour of Backstage over the `FeatureFlag` resource
it introduced, and M6's design (`docs/superpowers/specs/
2026-09-15-northstar-m6-golden-path-design.md` §1) confirms M6 does not
reintroduce a CRD layer here either. This allow-list is **unchanged by M6**:
it is still the M2-era environment variable read by
`allowed_agents_from_env()`, exactly as deployed above.

**What this mechanism does protect against:** claims-agent's own code (or a
bug, or a name that arrives some other way) calling out to an agent name
that was never configured — the lookup raises before any network call is
attempted, and the error names exactly what's permitted.

**What this mechanism does not protect against — stated plainly, because
overstating it would be exactly the kind of thing this document is held not
to do:**

- It is enforced entirely in-process, in claims-agent's own Python. There is
  no gateway, mTLS, or Kubernetes `NetworkPolicy` anywhere in this cluster
  (`deploy/` has no `NetworkPolicy` resource at all, confirmed by search) —
  nothing stops any other pod in the cluster from calling settlement-agent's
  Service directly, bypassing claims-agent and this check entirely.
  Symmetrically, `mcp_tools.py`'s docstring states "settlement-agent is the
  only agent permitted to reach payments-mcp," but that, too, is a statement
  of intent enforced by nothing but which URL settlement-agent happens to be
  configured with — claims-agent (or anything else in the cluster) could
  reach `payments-mcp.northstar-tools.svc.cluster.local:8000` directly today.
- It checks a *name*, not an identity — there is no cryptographic or
  workload-identity verification that the process on the other end of the
  configured URL actually is settlement-agent. **This is still true on Hop 2
  in M2.1.** SPIRE has arrived, but only Hop 3a uses it: the allow-list here
  is unchanged and still checks a string. See "Workload identity: what is
  real and what is deferred", hardening item 2.
- It says nothing about whether the delegated call itself should be allowed.
  Once a name is in the map, the amount, the claim, and the decision to pay
  are not checked against anything — see "What the system deliberately does
  not do" below.

This is the M1 stand-in for what M2's `agentgateway` is meant to enforce at
the network boundary instead of in application code.

## Hop 3 — settlement-agent → payments-mcp: now two hops, through agentgateway

**This was one hop in M1.1 and is two in M2.1.** `settlement-agent`'s
`PAYMENTS_MCP_URL` is
`http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp`
(`deploy/k8s/settlement-agent.yaml`), so:

| | From → to | What is different about it |
|---|---|---|
| **Hop 3a** | `settlement-agent` → `agentgateway` | Carries the credential: `Authorization: Bearer <JWT-SVID>`. Trace context rides in *two* independent carriers (see below). This is the only authenticated hop in the system, and as of M2.2 the only authorized one. |
| **Hop 3p** | `agentgateway` → `policy-decision-service` | **M2.2.** gRPC, `agentgateway.dev.ext_mcp.ExtMcp/CheckRequest`, on every `tools/call`, at the REQUEST phase — before the call reaches `payments-mcp`, which is the only moment at which a refusal is still a refusal rather than a regret. Not on the claim's path: a question the gateway asks. |
| **Hop 3b** | `agentgateway` → `payments-mcp` | Same MCP transport, same tool call — **only if Hop 3p permitted**. The gateway has **stripped** the credential and **re-stamped** `params._meta` with its own outbound span. `payments-mcp` therefore verifies nothing and sees no identity. |

Hops 3a and 3b are MCP over streamable HTTP, and neither the reroute nor the
policy changed the protocol at either end: `payments-mcp` is unmodified, does
not know a proxy is in front of it, and does not know a policy exists. That is
the property exit criterion 4 is about — the tool is governed without being
told it is being governed.

**What `payments-mcp` sees on a refusal: nothing at all.** Measured on the
denial trace — there is no `payments-mcp` span for the refused `tools/call`
anywhere in it (the `payments-mcp` spans present are `initialize`'s), and no
`payments-api` execute span either. The refusal is not a call that failed; it
is a call that never happened.

**The gateway is a node in the trace, not a fracture** — measured shape, and
the only thing about the trace this document publishes, since a whole-trace
span total is not stable (see the header):

<pre class="mermaid">
flowchart TD
    s1["`settlement-agent · **execute_tool**`"]
    s2["`settlement-agent · **POST**
      httpx CLIENT`"]
    s3["`agentgateway · **tools/call**
      SERVER`"]
    s4["`agentgateway · **Guardrail**
      CLIENT — M2.2`"]
    s5["`policy-decision-service · **policy.decision**
      SERVER — M2.2`"]
    s6["`agentgateway · **tools/call payments-mcp_#60;tool#62;**
      CLIENT`"]
    s7["`payments-mcp · **tools/call #60;tool#62;**
      SERVER`"]
    s8["`payments-mcp · **POST**
      httpx CLIENT`"]
    s9["`payments-api · **POST /payments/...**
      SERVER`"]

    s1 --> s2 --> s3
    s3 --> s4
    s3 --> s5
    s3 -->|"permit only"| s6
    s6 --> s7 --> s8 --> s9
</pre>

One trace id throughout, no new roots and no orphans.
`tests/e2e/test_gateway_trace.py` is the runnable evidence and pins this
shape, the span kinds, the parentage, and the gateway's exact attribute
vocabulary. Note that the tool name is **not** in the gateway's SERVER span
name — both tool calls in a settlement produce a span named exactly
`tools/call`, and telling them apart means reading the `gen_ai.tool.name`
attribute.

**Read the indentation carefully, because it is the M2.2 measurement that was
easiest to get wrong.** The three M2.2-marked spans are **siblings**, all three
children of the gateway's `tools/call` SERVER span. `policy.decision` is *not*
a child of `Guardrail`, even though `Guardrail` is the span for the gRPC call
that produced it: agentgateway propagates the context it *received* to the
guardrail hook rather than its own client span's context. Measured on six
captured traces before it was written down, and pinned by exact equality on
the sorted `(service, operation, kind)` triples of that span's children in
`tests/e2e/test_gateway_trace.py` — which is a stronger assertion than a
child count, and replaced an "exactly one child" assertion that M2.2 falsified.
(Attributed to `test_policy_denial.py` until M4's final review; the triple
assertion has always lived in `test_gateway_trace.py`.)

**Why the trace survives, stated precisely, because two mechanisms are very
easy to conflate here and conflating them produces a false claim.** The
gateway *ingests* trace context from the HTTP `traceparent` header and from
nowhere else; it *emits* SEP-414 `params._meta` on messages it forwards.
Those are independent directions, not a round trip:

- **Ingress.** The httpx instrumentation auto-injects a `traceparent`
  **HTTP header** on every outbound MCP request, because the MCP
  streamable-HTTP transport is httpx-based. (`HTTPX2ClientInstrumentor` since
  the `mcp` 2.2.0 bump of 2026-09-11 — 2.x's transport is built on `httpx2`,
  a distribution separate from `httpx`; this bullet named
  `HTTPXClientInstrumentor` until then.) The gateway reads that. It does
  **not** read `params._meta` on the way in — see Finding 6 in
  [`docs/upstream-findings.md`](upstream-findings.md).
- **Egress.** Separately, the gateway stamps its *own* outbound span into
  `_meta` on the messages it forwards, which is why `payments-mcp`'s
  `initialize` spans also joined the trace — those are not messages
  `settlement-agent` hand-injected.

MCP's own propagation convention (SEP-414) rides JSON-RPC `params._meta` and
never HTTP headers. So this project's own hand-injection in `mcp_tools.py` is
for FastMCP's benefit at the far end, and the header the gateway reads comes
free from an instrumentor. Nothing here should be read as agentgateway
supporting SEP-414 on ingress: it does not.

Never stdio. The design spec is explicit about why: *"Transport is
**streamable HTTP** throughout. stdio cannot traverse agentgateway in M2, so
choosing it now would guarantee a rewrite."* (`docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`).
That prediction is now load-bearing rather than hypothetical, and Finding 6
is why: a stdio MCP client carries trace context *only* in `_meta`, which is
the one carrier this gateway does not read.
`payments-mcp/server.py` runs `mcp.run(transport="http", ...)`; settlement-agent's
`mcp_tools.py` connects via `mcp.client.streamable_http.streamablehttp_client`.

**Four** tools as of M3, exact signatures and annotations
(`contracts/mcp/payments-mcp.json`, matching `tools/payments-mcp/server.py` —
and read back off the *served* `tools/list` for M3 Task 5B rather than off
either file, which is how the drift below was found):

```json
propose_payment(claim_id: string, amount: number, currency: string = "EUR") -> dict
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }

execute_payment(payment_id: string, amount: number, currency: string = "EUR") -> dict
  annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false }

refuse_payment(payment_id, rule, limit, requested, narrative,
               refused_trace_id, refused_span_id) -> dict          # M3 Task 3
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
  (every parameter required -- no defaults)

get_payment(payment_id: string) -> dict                            # M3 Task 3
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
  (the only tool in this server with readOnlyHint: true)
```

**This paragraph said "two tools" until M3 Task 5B, and so did
`contracts/mcp/payments-mcp.json` — which is worse, because a test reads it.**
`tests/e2e/test_payments_mcp_trace.py::test_tool_schema_matches_the_committed_contract`
diffs the served tool list against that file, so it had been **failing** since
M3 Task 3 added the two tools without updating the contract. It is not a
gated test; it is simply in a module that needs a live cluster, so no cheap
local loop ran it. The contract file is regenerated from the served list, and
the failure is recorded here rather than only fixed — a "source of truth" that
a milestone can leave two tools behind is worth knowing about.

**Why `destructiveHint: true` is load-bearing, not decorative:** the design
spec's own argument is *"Consequence is declared in the protocol... the
Policy Decision Service (M2) reads that annotation rather than pattern-matching
tool names. ... It also means a newly added consequential tool inherits
governance automatically, which is the real test of whether a platform
abstraction works."*

**M2.2 correction, and it is a correction against this project's own stated
intention rather than a stale sentence.** Earlier revisions said the
annotation was "waiting for M2's Policy Decision Service to key off it instead
of a hardcoded list of dangerous tool names, which is the weaker design this
project is deliberately avoiding." The Policy Decision Service now exists, and
**it does not read the annotation.** `platform/policy-decision-service/pds/rules.py`
holds frozensets of tool *names* — `UNLIMITED_TOOLS` and `LIMITED_TOOLS`,
which at M2.2 held one name each (`propose_payment` and `execute_payment`) and
as of M3 hold three and one, plus a third `CALLER_SCOPED_TOOLS` mapping — and
decides from those. So the
weaker design is the one that shipped, and the reason is worth stating rather
than hiding: agentgateway's guardrail hook hands the PDS the JSON-RPC `params`
of the call it is judging (`{"name": ..., "arguments": {...}}`, measured, M2.2
Task 1 question 5) and nothing else — no tool schema, no annotations. Reading
`destructiveHint` from a `tools/list` the PDS would have to fetch and cache
itself is real work with real staleness questions, and it was out of scope for
a milestone whose job was to make one denial real.

The consequence is concrete and should be read as debt, not as nuance: a
newly-added consequential tool does **not** inherit governance here. It falls
into neither frozenset, so `decide()` refuses it under the rule name
`unknown_tool` — which fails closed, and is the right direction, but it means
every new tool needs a `rules.py` edit rather than being governed by what it
already declares about itself. The annotation is still true, still correct,
and still read by nothing.

**M3 turned that prediction into a measurement, which is the only reason to
mention it again.** Task 3 added two tools, and both needed exactly the
`rules.py` edit this paragraph warned about: `refuse_payment` and
`get_payment` had to be named in `UNLIMITED_TOOLS` by hand, and
`refuse_payment` then needed a *third* table (`CALLER_SCOPED_TOOLS`) because
being unlimited in amount is not the same as being open to any caller — a
distinction `destructiveHint` cannot express at all. Both carry
`destructiveHint: false`, and `get_payment` is the server's only tool with
`readOnlyHint: true`; neither annotation took any part in the decision. The
debt is real and it compounded exactly as predicted rather than turning out
cheaper than feared.

## Hop 4 — payments-mcp → payments-api: plain HTTP/JSON

`payments_client.py`'s `httpx.Client` calls payments-api directly; no MCP,
no A2A. Verified live against the deployed payments-api (port-forwarded).
**M3 Task 2 took this from four endpoints to six**, and both additions exist
for the human: one writes down why a payout was refused, the other lists the
refusals so a reviewer can find them (`systems/payments-api/handlers.go`,
`contracts/openapi/payments-api.yaml`):

| Method & path | Request body | Response | Status codes |
|---|---|---|---|
| `GET /healthz` | — | `{"status":"ok"}` | `200` |
| `POST /payments/propose` | `{"claimId","amount","currency"}` | `{"paymentId","claimId","amount","currency","status":"proposed"}` | `201` (created), `400` (malformed JSON, missing `claimId`, negative `amount`) |
| `POST /payments/{paymentId}/execute` | `{"amount","currency"}` — required; `amount` is mandatory, `currency` defaults to `EUR` | `{"paymentId","claimId","amount","currency","status":"executed"}` or `{"error":"..."}` | `200` (executed), `400` (`{"error":"execution must state the amount it is paying"}` for an absent or unparseable body; `{"error":"amount is required"}` for a body that omits the amount), `404` (`{"error":"payment not found"}`, unknown id), `409` — **two distinct causes**: `{"error":"stated amount does not match the proposal"}` (the confirmation-of-intent check refused it) and `{"error":"payment already executed"}` |
| `POST /payments/{paymentId}/refuse` **(M3)** | `{"rule","limit","requested","narrative","refusedTraceId","refusedSpanId"}` — only `rule` is required; `refusedAt` is *not* accepted, the ledger stamps its own clock | The payment, now `refused`, with a `refusal` object | `200`, `400` (`{"error":"malformed JSON body"}`, `{"error":"rule is required"}`), `404`, `409` — **three distinct causes**: `{"error":"requested amount does not match the proposal"}`, `{"error":"payment already executed"}`, `{"error":"payment already refused"}` |
| `GET /payments` **(M3)** | — | Array of payment records, sorted by `paymentId` | `200` only. `?status=` filters; an **unrecognised status is not an error**, it matches nothing and returns `[]` |
| `GET /payments/{paymentId}` | — | Payment record, or `{"error":"payment not found"}` | `200`, `404` |

Verbatim from a live capture against the deployed pod (port-forward, one
sequence against a single freshly-proposed payment: propose, execute with no
body, execute with a wrong amount, execute with a wrong currency, execute
correctly, execute again, execute-unknown). Re-captured 2026-09-10 against the
image built from `e062c10`, which added the confirmation-of-intent check —
the previous capture here showed bodyless executes succeeding and every line
of it would now return `400`:

```
POST /payments/propose -> 201  {"paymentId":"PAY-000004","claimId":"CLM-DOC-TEST-3","amount":100,"currency":"EUR","status":"proposed"}
POST /payments/PAY-000004/execute                                 (no body) -> 400  {"error":"execution must state the amount it is paying"}
POST /payments/PAY-000004/execute  {"amount":250,"currency":"EUR"} -> 409  {"error":"stated amount does not match the proposal"}
POST /payments/PAY-000004/execute  {"amount":100,"currency":"USD"} -> 409  {"error":"stated amount does not match the proposal"}
POST /payments/PAY-000004/execute  {"amount":100,"currency":"EUR"} -> 200  {"paymentId":"PAY-000004","claimId":"CLM-DOC-TEST-3","amount":100,"currency":"EUR","status":"executed"}
POST /payments/PAY-000004/execute  {"amount":100,"currency":"EUR"}  (again) -> 409  {"error":"payment already executed"}
POST /payments/PAY-999999/execute  {"amount":100,"currency":"EUR"} -> 404  {"error":"payment not found"}
```

Two things worth reading off that capture. The two 409s carry different
messages and come from different checks — one is the confirmation of intent,
one is the already-executed guard — and they are only both reachable because
the request states what it is paying. And a mismatched *currency* is refused
by the same comparison rather than by schema validation, which is why
`contracts/openapi/payments-api.yaml` deliberately does not put `enum: [EUR]`
on the execute body's `currency` (it does on propose): the field is a claim to
be checked, not an instruction to be validated.

**M3's two routes, captured the same way** (M3 Task 5B, against the deployed
pod on the branch's images, one sequence against a single freshly-proposed
payment, then a second payment to reach the third 409):

```
POST /payments/propose  {"claimId":"CLM-DOC-TEST-M3","amount":9000,...} -> 201  {"paymentId":"PAY-000026",...,"status":"proposed"}
POST /payments/PAY-000026/refuse                                  (no body) -> 400  {"error":"malformed JSON body"}
POST /payments/PAY-000026/refuse  {"limit":6000,"requested":9000}           -> 400  {"error":"rule is required"}
POST /payments/PAY-000026/refuse  {"rule":"execute_limit","requested":1234} -> 409  {"error":"requested amount does not match the proposal"}
POST /payments/PAY-000026/refuse  {"rule":"execute_limit","limit":6000,"requested":9000,"narrative":"...","refusedTraceId":"aabb..","refusedSpanId":"0011.."}
                                                                            -> 200  {...,"status":"refused","refusal":{...,"refusedAt":"2026-09-11T07:39:12Z"}}
POST /payments/PAY-000026/refuse  (again, same body)                        -> 409  {"error":"payment already refused"}
POST /payments/PAY-999999/refuse  {"rule":"execute_limit",...}              -> 404  {"error":"payment not found"}
POST /payments/PAY-000026/execute {"amount":9000,"currency":"EUR"}          -> 200  {...,"status":"executed","refusal":{...}}
GET  /payments?status=refused                                               -> 200  [ ... one record, PAY-000016 ... ]
GET  /payments?status=nonsense                                              -> 200  []
POST /payments/propose  {"claimId":"CLM-DOC-TEST-M3B","amount":100,...}     -> 201  {"paymentId":"PAY-000027",...}
POST /payments/PAY-000027/execute {"amount":100,"currency":"EUR"}           -> 200  {...,"status":"executed"}
POST /payments/PAY-000027/refuse  {"rule":"execute_limit","limit":50,"requested":100}
                                                                            -> 409  {"error":"payment already executed"}
```

**Three things that capture settles, and the third one surprises people.**

1. `refusedAt` came back on the 200 even though the request never sent it. The
   ledger stamps its own clock, deliberately: the elapsed time a human reviewer
   is measured against must not be a figure a caller can supply.
2. An unrecognised `?status=` returns `200 []`, not `400`. `GET /payments` is
   also the one route in this service whose span carries **no** claim-id
   baggage annotation — it lists across every claim, so there is no single
   claim id to read (`newRouter`'s `handleNoClaimID`).
3. **Executing a `refused` payment succeeds — `200`, and it is not a bug.**
   The eighth line above executes PAY-000026 after it was refused and gets
   `status: executed` back with the `refusal` object still attached. That is
   precisely how approval works: `refused` is terminal *until a human approves*,
   not terminal outright, and the approved execution goes through this same
   route. It also means the `refusal` object is present on any payment that has
   ever been refused rather than only while `status` reads `refused` — a
   distinction an earlier draft of the OpenAPI description got wrong, and which
   only a live `GET /payments` showed. The ledger is not the authorization
   point; the gateway and the Policy Decision Service are, which is the same
   sentence "What the system deliberately does not do" has been making since
   M1.1.

Every route except `/healthz` is wrapped in `otelhttp.NewHandler(...)`
individually so the span name is the route template (e.g.
`POST /payments/{paymentId}/execute`), not the raw path — low-cardinality
span names per HTTP semantic conventions. `/healthz` is deliberately
unwrapped: `handlers.go`'s own comment records that the readiness probe
polling it produced "162 GET /healthz traces against 44 POST
/payments/propose traces in one measured hour" before this fix — the probe
was polluting the trace view, so it was excluded from tracing entirely
rather than filtered downstream.

## The decision logic — M1.2: a real model now decides, the ledger still states the amounts

M1.1's `decide_settlement` was the deterministic stub its own docstring
promised to become: *"In M1.2 the body becomes a PydanticAI agent; this
signature and return shape stay the same so nothing downstream changes."*
That happened at M1.2 Tasks 2–4. `agents/settlement-agent/settlement.py` now
validates the same three fields as before — `claimId` present, `amount`
present, `amount >= 0` — but the settlement decision after that validation
is a real PydanticAI `Agent` call against `anthropic:claude-sonnet-5`, not a
fixed narrative string:

```python
settlement_agent = Agent(
    os.environ.get("NORTHSTAR_SETTLEMENT_MODEL", DEFAULT_MODEL),
    deps_type=SettlementDeps,
    instructions=(
        "You settle motor insurance claims for Northstar Insurance.\n"
        "You are given a claim that has already been assessed as covered.\n"
        "Settle it by calling propose_payment with the assessed amount, then "
        "execute_payment with the paymentId you get back and that same "
        "amount.\n"
        "Then reply with a short narrative for the claimant.\n"
        "Settle the assessed amount. Do not round it, discount it or "
        "renegotiate it. Do not state amounts in your narrative."
    ),
)

@settlement_agent.tool
async def propose_payment(ctx, amount, currency="EUR") -> dict: ...

@settlement_agent.tool
async def execute_payment(ctx, payment_id, amount, currency="EUR") -> dict: ...
```

**The model chooses the tool sequence.** `decide_settlement` no longer calls
`propose`/`execute` itself — it runs `settlement_agent.run(prompt, deps=...)`
with one templated sentence (`f"Claim {claim_id} is assessed as covered for
{amount:.0f} {currency}. Settle it."`, built only from `{claimId, amount,
currency}` — the claim's free-text description never reaches the model at
all) and lets the model decide when, whether, and in what order to call
`propose_payment` and `execute_payment`. Both tools still call
`mcp_tools.PaymentsTools` exactly as before — the MCP call path, the
verified `params._meta` trace-context propagation, and payments-mcp's own
error handling are all unchanged from M1.1. The model chooses; the same
M1.1-proven code calls.

**The reply's amounts come from the ledger, never from the model's own
words.** `decide_settlement` reads `deps.record.executed` — the actual
`propose`/`execute` results returned by payments-mcp, captured by the tool
functions themselves as a side effect — and builds the reply's
`amount`/`currency`/`status`/`paymentId` from *that*, not from anything the
model said. Only the reply's `narrative` field is the model's own text
(`result.output`, the agent's final free-form turn). Concretely:

```python
return {
    "amount": executed["amount"],       # from payments-api's ledger
    "currency": executed["currency"],   # from payments-api's ledger
    "status": executed["status"],       # from payments-api's ledger
    "paymentId": executed["paymentId"], # from payments-api's ledger
    "narrative": result.output,         # the model's own prose — nothing else
}
```

This is deliberately testable and tested:
`test_reply_amount_comes_from_the_ledger_not_from_the_model` (Task 2) drives
a scripted model that narrates one amount while the mocked ledger returns
another, and asserts the reply carries the ledger's figure — proving the
separation is real, not incidental. If the model finishes a run without
ever calling `execute_payment`, `decide_settlement` raises a `RuntimeError`
naming the claim and whatever was proposed, rather than silently reporting
success; and any `ToolCallError` from a failed payments-mcp call propagates
uncaught out to the caller as a JSON-RPC error, so a real failure is never
smoothed over into friendly prose.

A reader watching this trace in Jaeger sees the same two real network calls
(propose, execute) M1.1 always showed, plus — new in M1.2 — one
`invoke_agent settlement_agent` span, three `chat claude-sonnet-5` spans
(one model turn each: propose, execute, final narrative), and two
`execute_tool` spans, all nested inside the same request
(`docs/telemetry-contract.md`'s "PydanticAI instrumentation, as measured"
section has the full attribute inventory). Where M1.1's narrative was a
fixed string and this document had to say so plainly, M1.2's narrative is
genuinely the model's own words about a decision the model itself made —
the trajectory a reader follows in Jaeger now really did happen the way it
reads, not merely look like it did.

**M1.2 Task 8: each `chat claude-sonnet-5` span now has its own HTTP CLIENT
child.** Tasks 1–7 left the model's own outbound hop untraced below the
`chat` span — `anthropic==1.4.0` speaks `httpx2`, a distribution
`HTTPXClientInstrumentor` does not cover, so the request to
`api.anthropic.com` produced no HTTP status, retry count or wire-latency
detail of its own (`docs/telemetry-contract.md`'s former Gap 7). Task 8
closed this by additionally instrumenting `HTTPX2ClientInstrumentor`
(`bootstrap.py`) — the sibling class already present in the same installed
`opentelemetry-instrumentation-httpx==0.65b0` package. A reader now sees, per
model turn, a `POST` CLIENT span nested directly under that turn's `chat`
span, carrying the same stable HTTP semconv attributes every other outbound
hop in this project carries (`http.request.method`, `http.response.status_code`,
`server.address`, `url.full`) — measured at `POST` / `200` /
`api.anthropic.com` / `https://api.anthropic.com/v1/messages?beta=true`.
Exactly one such span appears per `chat` span, confirmed by diffing a live
capture taken before this change against one taken after
(`tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`).

## The decision the platform makes — M2.2

**Still no amount check, clamp, threshold or guard rail in the agent, and that
is now a governed property rather than an admission.** Re-confirmed for M2.2
by reading `settlement.py`, `executor.py`, `mcp_tools.py` and
`tools/payments-mcp/*.py`: the only numeric checks in either component are
`_validate`'s pre-model `amount is None`/`amount < 0` guards, both inherited
unchanged from M1.1 and both about the *inbound claim*, not about how much may
be paid. `settlement.py`'s own module docstring says where the limit is
instead: *"That hole is what M2's Policy Decision Service fills — and note
where it is filled: still not here. There is no limit in this file in M2.2
either. The refusal comes from the platform, at a choke point the agent cannot
reach around."* That claim is not left to prose:
`tests/e2e/test_policy_denial.py`'s criterion-4 test walks both components'
production source with `ast`, and fails on any policy vocabulary, any literal
equal to a PDS allowance, or any unallowlisted comparison involving money.

**The rules, in full, because there are few enough that hiding them would be
worse than printing them** (`platform/policy-decision-service/pds/rules.py`).
M2.2 shipped with one sentence here; M3 added a row and two tables, and none of
it changed `decide()`:

> **Execute limits**, keyed on `(SPIFFE ID, autonomy level)`:
> `settlement-agent`/`assisted` → €6,000; `settlement-agent`/`autonomous` →
> €1,000,000 (reached by nothing today); and, **new in M3**,
> `senior-settlement-agent`/`assisted` → €25,000 — the colleague with higher
> approval authority the agent's own refusal narrative already promises.
>
> **Unlimited tools**, because they move no money and reverse nothing:
> `propose_payment`, and — new in M3 — `refuse_payment` (recording *why* a
> payment did not happen) and `get_payment` (reading the ledger back before
> executing an approval, R11).
>
> **Caller-scoped tools**, also new in M3: `refuse_payment` is unlimited in
> amount but restricted in *caller* to the two settlement identities. It had
> been unconditional for any caller including `caller=None`, so any holder of a
> valid SVID could author the refusal record a human reads (M3 Task 3 fix
> round, R12b). This is one more table `decide()` looks up, never a tool-name
> branch.
>
> Any other tool, any other caller, any other autonomy level, and any amount
> that is not finite and positive: **denied**.

Four properties of the execute limit are deliberate and each was argued:

- **Identity is structurally required, not merely consulted.** The allowance
  table is keyed on `(SPIFFE ID, autonomy level)`, so a decision cannot be
  reached without a caller. An unknown caller falls to `unknown_caller` and is
  denied — which is what exit criterion 5 demonstrates by changing the
  identity and watching the verdict change, using a second real SPIRE-minted
  JWT-SVID rather than a mocked one.
- **Proposing and executing are different acts.** `propose_payment` records an
  intent and moves no money, so it is permitted unconditionally
  (`unlimited_tool`); `execute_payment` is the one that is limited. The trace
  shows both decisions, which is more informative than a single gate would be:
  the same caller is permitted to propose €6,200 and refused permission to
  execute it.
- **The limit test is `amount > limit`, so everything that is not greater
  would otherwise pass** — including `NaN`, which is not greater than
  anything, and zero, and negatives. Those are refused separately, under their
  own rule name `invalid_amount`, so an audit trail can distinguish "the limit
  considered this and said yes" from "this was never a payable amount".
- **A parse failure is a denial, never a gRPC error.** `parse_call` never
  raises; it returns a `ToolCall` with holes in it, which `decide` refuses.
  The distinction matters because agentgateway routes a gRPC error through
  `failureMode` and reports it as a PDS *outage*, which is a different
  sentence both on stage and in a trace.

**`failureMode: failClosed` is set explicitly, and the reason this document
gave for it was wrong — the correction is recorded rather than quietly
swapped.** Through M2.2 Tasks 2–7 this paragraph read: *"agentgateway v1.5.0's
`FailureMode` derives `Default` with `FailOpen` first, so omitting the key
means a PDS outage silently permits every payment."* Task 8 measured it
against the pinned image and **the default is `failClosed`, not `failOpen`.**

The measurement, because a claim this load-bearing should not be swapped for
another assertion. First, the binary was tied to a source revision: `docker run
--rm cr.agentgateway.dev/agentgateway:v1.5.0 --version` reports
`git_revision: fe6732474a96a0363dfb9822859af4e9bab360fa`, which is exactly the
commit tag `v1.5.0` points at — so the source read below is the source of the
binary that runs in this cluster, not a nearby version of it. Then three
probes, same image, same throwaway MCP backend, a guardrail pointed at a dead
address (`127.0.0.1:9`) so every `tools/call` forces the failure branch:

| `failureMode` in the config | What the `tools/call` returned |
|---|---|
| omitted entirely | `-32603 mcpGuardrails checkRequest failed: upstream call failed: Connect: Connection refused` |
| `failClosed` | the same error, byte for byte |
| `failOpen` | the call **passed through** to the backend and the tool answered |

Omitting the key behaves identically to `failClosed`, and only `failOpen`
lets the call through. The source agrees:
`crates/agentgateway/src/mcp/guardrails/mod.rs` at that revision declares
`enum FailureMode` with `#[derive(Default)]` and `#[default]` on `FailClosed`,
and `Remote.failure_mode` is `#[serde(default)]` — and `client.rs` imports its
`FailureMode` from that module, so this is the enum the guardrail actually
uses.

**The line stays, and stays load-bearing, for a different reason.** It is no
longer "the default would betray us"; it is that fail-open is one word away
and the difference is invisible in a healthy cluster — there is no observable
difference between fail-open-and-healthy and fail-open-and-not-governing until
someone reads a trace. Stating the value makes the intent reviewable instead
of inherited, and `tests/unit/test_stage2_pds_config.py` still fails if the
deployed config ever says `failOpen` or drops the key. What changed is the
argument, not the configuration: nothing in this repository was altered by
this correction except the sentences that got it wrong.

**What the model does with a refusal.** `mcp_tools.PolicyDenial` is a
`pydantic_ai.ToolFailed`, so the framework hands the refusal to the model as a
result rather than as a retry instruction — and deliberately **not** a
`pydantic_ai.ModelRetry`, which the framework would hand back as "try again".
The model sees the refusal and explains it to the claimant. The denial is
recognised by the structured marker `data.retryable: false` /
`data.decision: "deny"` that the PDS puts in the JSON-RPC error's `data`
member, never by pattern-matching the reason prose — a transport failure and a
denial-shaped message with no marker both stay transport failures. And
`settlement.py`'s `_latch` makes the guarantee independent of the model's
cooperation: after a refusal, no further payment call leaves the process for
that claim, so an agent cannot sit on stage retrying a payment it will never
be allowed to make.

**A refusal comes back as a reply, not as an error.** `executor.py` returns a
normal A2A reply with `status: "refused"` and the model's narrative, rather
than letting an exception become a JSON-RPC error envelope. The claimant is
owed an explanation rather than a stack trace, and the platform refusing is
the platform working. Every other field of the documented reply is unchanged —
including `amount: 6200`, which is the amount the ledger recorded for the
*proposal*, since proposing was permitted.

**One number in M1.2's version of this section has to be read differently
now.** It said three independent live runs "all settled exactly the assessed
€6,200 — a fact about this model on this prompt, not a property anything in
this chain enforces." Both halves still hold and the conclusion has inverted:
the model still proposes exactly €6,200, and the platform now refuses to
execute it. What was an observation about a model's behaviour is now the input
to a decision something else makes.

## The network, as the second defence — M2.2

Everything above is enforced at the application layer, by a proxy an agent
chooses to talk to. M2.2's last task makes that choice not a choice, in both
directions, with `CiliumNetworkPolicy` on a Cilium 1.20.1 dataplane
(`deploy/platform/cilium-policies.yaml`, **nine** documents -- `grep -c 'kind: CiliumNetworkPolicy'`; this said "five" until M4's final review, and the table below has always been a selection rather than the whole file):

| Direction | Policy | What it makes impossible |
|---|---|---|
| **Ingress** (`d17309c`) | `payments-mcp` admits agentgateway and the kubelet probe, nothing else | A workload reaching the tool without crossing the policy hook. Measured before it existed: `claims-agent` POSTed an MCP `initialize` at `payments-mcp:8000` and got **HTTP 200** |
| **Egress** (`50fac07`) | one document per workload in `northstar-agents` — `settlement-agent` may reach DNS, agentgateway:3000, the collector:4318 and `api.anthropic.com:443` by name; `claims-agent` may reach DNS, settlement-agent:9002, the collector and, from M8, payments-api:8080 for one L7 read (`GET /payments/{id}`, the ledger read on resume); `customer-ui` may reach DNS only | An agent reaching the model provider, the ledger, the tool or the PDS directly. Two of the three workloads had unrestricted outbound egress before this, including to the model provider — measured, which is why the policy covers the namespace rather than the one agent the criterion names |

Four things about that table are measurements and not design intent:

- **The denials are `TimeoutError`, not `ConnectionRefusedError`.** A Cilium
  drop discards the packet, so there is no RST. A test written to expect a
  refusal would fail against a working policy.
- **DNS still resolves for hosts the policy will not let you reach.**
  `api.anthropic.com` resolves inside `claims-agent` while the connection to
  it is dropped. That is correct: the L4 DNS rule permits the lookup, and
  enforcement happens on the connect.
- **`toFQDNs` needs Cilium's DNS proxy, and the proxy is visible in the
  datapath.** `settlement-agent`'s two port-53 rules carry a `PROXY PORT`
  in the BPF policy map; the other two agents' do not, because they have no
  `toFQDNs` rule to feed. That difference is asserted, and it catches a
  mutation the behavioural probe cannot: stripping `rules.dns` leaves the
  connection to `api.anthropic.com` working, because Cilium retains the
  already-derived FQDN identity from earlier lookups.
- **The model-provider allowance is a per-name identity, not "external is
  fine".** Its BPF row carries `fqdn:api.anthropic.com` *and*
  `reserved:world` on 443/TCP, so "no egress row mentions `reserved:world`"
  would fail on a *correct* policy. The assertion that separates the two cases
  is that every world-bearing row is also FQDN-labelled and port-scoped.

**No Cilium telemetry is asserted as a span, and none was invented.**
`enable-hubble: false` in this cluster's `cilium-config` — read live — so
there are no Hubble flows here at all. The evidence for every claim in this
section is a read of the BPF policy map or a TCP probe from inside a pod.

**`toFQDNs` is only as good as the SDK default it names.**
`api.anthropic.com` is configured nowhere in this repository; it is the
`anthropic` client's own hardcoded `base_url`. If that default ever moves,
this policy fails **closed** — the model call breaks rather than a new host
being silently permitted. That is the right direction, and it is still a
coupling to a third-party constant.

## Workload identity: what is real and what is deferred

**M2.1 Task 4.** One hop in this system now authenticates its caller
cryptographically. Hop 3 — settlement-agent → agentgateway — carries a
SPIFFE JWT-SVID issued by SPIRE, and the gateway refuses the call without one.

What is installed, pinned (`deploy/platform/spire/`):

| Piece | Version | Role |
|---|---|---|
| `spiffe/spire-crds` chart | 0.6.1 | the `spire.spiffe.io` CRDs, installed first |
| `spiffe/spire` chart | 0.30.2 (appVersion SPIRE 1.15.3) | server, agent, CSI driver, controller-manager, OIDC discovery provider |
| trust domain | `northstar.local` | every SPIFFE ID in this system |
| cluster name | `northstar` | the k8s node attestor's cluster |

**Four** identities are registered, declaratively, as `ClusterSPIFFEID`
resources — two from M2.1 Task 4, a third added by M2.2 and a fourth by M4
Task 4, all in `deploy/platform/spire/registration.yaml`, all rendered from the
same
`spiffe://{{ .TrustDomain }}/ns/{{ .PodMeta.Namespace }}/sa/{{ .PodSpec.ServiceAccountName }}`
template. Read back off the live SPIRE server's own entry list, not from the
manifest alone:

```
spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
spiffe://northstar.local/ns/northstar-platform/sa/agentgateway
spiffe://northstar.local/ns/northstar-platform/sa/policy-decision-service
spiffe://northstar.local/ns/northstar-platform/sa/eval-scorer
```

All four are attested on namespace **and** Kubernetes service account
(`k8s:ns:...` plus `k8s:sa:...` selectors on the entry, alongside the
controller-manager's own `k8s:pod-uid:`), which is why each workload got a
dedicated ServiceAccount: without one, each attested as `.../sa/default`, and
an identity whose service-account component reads "default" is not something a
policy can decide on. That is not hypothetical — it is what SPIRE minted for
`settlement-agent`'s pod, on this cluster, before it was given a
ServiceAccount of its own.

**Why the fourth one had to be an explicit CR, which is the part that is easy
to get wrong.** The chart's catch-all fallback CR selects namespaces `NotIn
[northstar-platform, spire-server, spire-system]` — read off the live CR — so a
workload placed in `northstar-platform` with no CR of its own gets **no
identity at all**, and nothing says so. M4 Task 4 proved that the expensive
way: `eval-scorer` ran with a dedicated ServiceAccount and a SPIFFE ID written
into this repo's own allow-list while `spire-server entry show` listed an
entry for every other workload and none for it. The two M3 identities below are the misleading
precedent — they need no CR because they are in `northstar-agents`, which the
fallback does cover. **Namespace decides, not the ServiceAccount.**

**M3 added two more identities, and deliberately not two more
`ClusterSPIFFEID`s.** `senior-settlement-agent` (Task 3) and `reviewer-ui`
(Task 4) each got a dedicated ServiceAccount and nothing else; the SPIRE
chart's own catch-all `default` CR (`fallback: true`) derives their IDs from
namespace + ServiceAccount using the same template, so registering them
explicitly would have added a manifest that changed no outcome. Read back off
the same live entry list:

```
(retired in M8) .../ns/northstar-agents/sa/senior-settlement-agent
spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui
```

**But the entries are not the same shape, and that is worth knowing before
anyone relies on one.** The four explicit entries carry `k8s:ns:` **and**
`k8s:sa:` selectors; the two fallback entries carry only `k8s:pod-uid:`
(measured — `spire-server entry show` on the live server, M3 Task 5B;
re-measured for `eval-scorer`'s entry in M4 Task 4, `k8s:ns:northstar-platform`
+ `k8s:sa:eval-scorer` + the controller-manager's `k8s:pod-uid:`). The
resulting SPIFFE ID is identical either way, so nothing downstream behaves
differently today, and it is the reason the Task 3 review's decision to make
"senior" an *identity* works at all. What it means is that these two are
attested to a pod instance rather than to a namespace-plus-service-account
pair. Hardening them into explicit non-fallback entries is a one-manifest
change nobody has needed yet.

**Three of the six mount the Workload API socket; the other three are
registered on purpose rather than by accident.** `settlement-agent` mounts the
Workload API socket
and fetches a JWT-SVID; so does `senior-settlement-agent`, which is the same
image with the same `SPIFFE_ENDPOINT_SOCKET` and the same `csi.spiffe.io`
volume — that is the entire mechanism by which the "senior" identity is real
rather than a label, since the socket answers with whatever ID the calling
pod attests to. `reviewer-ui` also mounts the socket, but for a narrower
purpose and never as a credential: it reads its *own* ID back for the
`review.decision` span's `review.reviewer.spiffe_id` attribute
(`apps/reviewer-ui/identity.py`) and presents it to nothing. None of
agentgateway, the Policy Decision Service and `eval-scorer` has a
`csi.spiffe.io` mount, and none presents an SVID to anything. The gateway's
entry exists because its
SPIFFE ID is the **audience** settlement-agent's token is minted for, so that
string has to name a real registered identity rather than an invented label.
The PDS's entry exists for the same class of reason and one more: the ExtMCP
hop between the gateway and the PDS is **plain gRPC today**, protected by a
`CiliumNetworkPolicy` rather than by mTLS, and this entry is what makes the
PDS's own certificate possible the day that hop is hardened — as well as being
the identity a future audit record would name as the decider. `eval-scorer`'s
entry exists for the narrower reason its own CR comment gives: it is the
service whose whole job is to judge what other identities did, and an auditor
that cannot itself be named is a poor auditor. An identity nothing registers is
a string, not an identity — which M4 Task 4 confirmed literally, having written
one down in three places before noticing SPIRE had never minted it.

**A guard exists for these strings specifically**, because a published
identity nothing checks is a published identity that can go stale in the
direction that looks like success:
`tests/unit/test_stage2_spire_config.py::test_every_spiffe_id_published_in_the_docs_is_one_this_repo_configures`
enumerates **every** `spiffe://` string in this document and in
`docs/telemetry-contract.md` — prose, tables and fenced blocks alike — and
fails on any that is not one this repository actually configures. Some come
from an explicit `ClusterSPIFFEID` and some from the chart's fallback; which
is which is stated above, per identity, and the guard's `permitted` set is the
one place the membership lives.

**No count is written in this paragraph, and that is deliberate.** It said
"these five strings" and "one of the five … (three explicit, two from the
chart's fallback — the list in that module grew by two in M3)" while the guard
already held six and `registration.yaml` already held four. Both numbers were
correct when written and both were falsified by a later task adding an
identity — twice, by M3 and then by M4 Task 4, which corrected that same
stale-count paragraph *in the guard's own docstring* and left this one behind.
The guard checks membership, never cardinality, so nothing here can redden on
a count; the fix is to stop writing one.

**How the credential gets into the pod.** No secret, key or token is in any
manifest. The `spiffe-csi-driver` bind-mounts the spire-agent's Workload API
socket into settlement-agent's pod at `/spiffe-workload-api/spire-agent.sock`,
and the agent's Kubernetes workload attestor identifies the calling container
via kubelet before answering. `agents/settlement-agent/svid.py` asks that
socket for a JWT-SVID scoped to one audience, caches it until 30s before
expiry, and `mcp_tools.py` presents it as `Authorization: Bearer` on the MCP
transport. **`senior-settlement-agent` runs that identical code path and gets a
different token**, because the socket answers with the attested identity of
whichever pod is asking — no configuration selects the ID.

**What the gateway checks, and the sentence this paragraph had to give up.**
`jwtAuth` in `mode: strict` — issuer, audience, signature (against SPIRE's
JWKS, fetched from the OIDC discovery provider) and `exp`. Through M2.1 this
paragraph continued: *"Verification only: there is no `mcpAuthorization` and no
`authorization` rule in the gateway's config, so every call it can authenticate
it still permits. Deciding what an authenticated caller may do is a later
milestone."* That was true for exactly one milestone. **M2.2 made it false** —
`mcpGuardrails` on the MCP route asks the Policy Decision Service about every
`tools/call`, so an authenticated caller is emphatically not an authorized one
(see "The decision the platform makes"). The mechanism is still not
`mcpAuthorization` or an `authorization` rule, which is the half of the old
sentence that survives: authorization here is an external gRPC hook rather than
a rule in the gateway's own config, and *that* is the deliberate choice —
policy lives in a service this project writes and tests, not in a proxy's
config language. **M3 leaves that shape untouched and adds a caller**:
`senior-settlement-agent` authenticates at the same hop, against the same
audience, and is decided by the same service on its own allowance row.

**Why a bearer token and not mTLS.** The spec permits either, and mTLS is the
stronger choice: an mTLS peer certificate cannot be replayed by whoever
obtains a copy of it, and a JWT-SVID can. It was not taken here because it
would mean threading a SPIFFE TLS context through `streamablehttp_client`'s
transport — real risk inside the one file M2 is allowed to modify, and the file
the surviving Task 3 trace was measured against. A JWT-SVID keeps the identity
genuinely SPIFFE-issued (a `spiffe://` subject, signed by SPIRE, verified via
JWKS) at the cost of one HTTP header. The weakness is bounded rather than
eliminated: the audience scopes each token to this one gateway, and the TTL is
one hour (SPIRE's `default_jwt_svid_ttl`).

**Recorded as later hardening, in order of how much they matter:**

1. **mTLS instead of (or under) the bearer token**, per the above. This is the
   real fix, and it is deferred rather than declined.
2. **The other hops are still unauthenticated.** Hop 1 (browser →
   claims-agent), Hop 2 (claims-agent → settlement-agent) and Hop 4
   (payments-mcp → payments-api) carry no SVID at all. Hop 2's allow-list
   still checks a *name*, exactly as described at Hop 2 above. **The
   reachability half of this item narrowed in M3 and the authentication half
   did not.** Through M2.2 this read "anything in the cluster can still call
   settlement-agent's Service directly", which was measured and true — both
   settlement Deployments had `Allow Ingress ANY`, so anything that could
   reach an A2A port could execute any proposed payment (M3 Task 3 review,
   finding 1/R11). M3 Task 3 added `settlement-agent-ingress-allowlist`
   (claims-agent plus the kubelet probe) and Task 4 added
   `senior-settlement-agent-ingress-allowlist` (reviewer-ui plus the probe),
   so today only the one measured caller of each can reach it. That is a
   network control, not an identity one: a pod that satisfies the
   `endpointSelector` still presents no credential, and `claims-agent` itself
   has no ingress policy at all. Defence in depth, not the fix.
3. **The JWKS endpoint is plain HTTP inside the cluster.**
   `spiffe-oidc-discovery-provider.tls.spire.enabled` is set to `false`, so the
   provider serves the key set over HTTP on port 80 via the chart's own nginx
   sidecar. The chart's default is HTTPS with a SPIRE-issued serving
   certificate, which agentgateway would have to be taught to trust in order
   to fetch a *public* key set. Signature verification does not depend on
   transport security here — the keys are public and the tokens are signed —
   but an attacker able to intercept that fetch could serve a key set of their
   own choosing, so this is a genuine gap on a real network.
4. **`payments-mcp` does not check anything.** The gateway removes the
   credential from its original location after validating it — agentgateway
   v1.5.0's `preserveToken` defaults to false, and
   `crates/agentgateway/src/http/jwt.rs` acts on it directly
   (`if !self.preserve_token { self.location.remove(req)?; }`) — so the MCP
   server behind it sees no identity, and would accept a direct call that
   skipped the gateway entirely. Removing it is the right default; the gap is
   that nothing re-establishes identity on the second leg.

### Two more things with no owner until now

Both were surfaced by an earlier task and had nowhere to be recorded, which
is how findings get lost. Neither is a defect in this project's own work.

**5. Four of the SPIRE chart's sub-image tags are behind upstream — and the
standing decision is to leave them alone.** All six sub-images the
`spiffe/spire` 0.30.2 chart brings resolve to an exact tag or digest and
nothing floats (verified by enumerating every pod's
`.spec.initContainers` *and* `.spec.containers`; two of the six *are* init
containers, which is why `containerStatuses` is the wrong thing to
enumerate). Four are nonetheless older than what upstream ships today, each
checked against the upstream release list rather than a local index:

| Sub-image | Chart pins | Latest upstream (checked 2026-09-09) |
|---|---|---|
| `ghcr.io/spiffe/spiffe-csi-driver` | 0.2.7 | v0.2.13 |
| `registry.k8s.io/sig-storage/csi-node-driver-registrar` | v2.15.0 | v2.18.0 |
| `docker.io/nginxinc/nginx-unprivileged` | 1.30.0-alpine | 1.31.5-alpine (and 1.30.4-alpine within its own line) |
| `docker.io/library/busybox` | 1.37.0-uclibc | 1.38.0-uclibc |

**These are the chart's own choices and are deliberately NOT overridden.**
`deploy/platform/spire/values.yaml` sets no `image`, `tag`, `repository` or
`digest` key anywhere — confirmed by grep, and the reason is a judgement
rather than an omission: overriding a chart's internal sub-image tags means
running a combination the chart's maintainers never tested, and nothing in
this repository's test suite would catch an incompatibility between, say, a
newer CSI node-driver-registrar and the driver the chart expects. The
"nothing floats" rule is satisfied — every tag is exact — and "exactly pinned
by the chart" is a different property from "newest available". If these ever
need bumping, the right move is a newer *chart*, not a values override.

**6. A stage-preparation hazard: one init image is pinned by digest against a
rolling tag, and it fails closed.** The
`spiffe-oidc-discovery-provider`'s `ensure-alternate-names` init container
runs:

```
cgr.dev/chainguard/bash:latest@sha256:90041f375e30f41aa7e0390075d8a69dc61900771d52fa98d63ee5d03d866a58
```

A digest pinned against `:latest` is reproducible right up until the digest
is garbage-collected, and Chainguard's free `cgr.dev/chainguard` namespace is
where that is most likely to happen — it generally keeps only the current
`latest`. **Measured 2026-09-09: the pinned digest still resolves (HTTP 200),
but `:latest` now points at a different manifest entirely
(`sha256:6250d4dc…`).** So the tag has already moved on and the pinned digest
is now an untagged manifest whose retention this project does not control.

Why this is a *stage-preparation* hazard specifically, and not a background
worry: the failure mode is an image pull failure on a **cluster recreate**,
which is exactly the thing that happens shortly before a talk. It fails
closed — the init container cannot start, so the discovery provider never
becomes ready, so the gateway cannot fetch the JWKS, so every MCP call is
refused. The symptom at the front of the room would be a demo where the
model asks and nothing happens, and the cause would be four layers down in a
chart's init container.

**Mitigation to run before a talk, not after:** mirror the images this
cluster needs into the shared `kind-registry` and point the install at that,
or — simpler and enough for a demo — pre-pull them and `kind load` them into
the `northstar` node so the pull never leaves the machine. Either way the
check is cheap: recreate the cluster once, on the day before, from a network
you do not need.

**Why the trace is unaffected.** Identity and trace context cross this
boundary in two different carriers, deliberately: the SVID in an HTTP
`Authorization` header, the trace context in the JSON-RPC `params._meta` bag
(plus the `traceparent` header the httpx instrumentation injects —
`HTTPX2ClientInstrumentor` since `mcp` 2.2.0 — which is what the gateway
actually reads). Neither substitutes for the other — trace
context is attacker-supplied and is not a credential; an SVID says nothing
about which trace a call belongs to.

**What SPIRE itself contributes to the telemetry: metrics only, no spans.**
SPIRE 1.15.3 has no OpenTelemetry support of any kind — no OTLP exporter and
no tracing. Its telemetry configuration offers Prometheus, DogStatsD, Statsd,
M3 and an in-memory sink and nothing else. So SPIRE adds no spans to the
trace, and none are invented for it. Its Prometheus metrics *are* enabled and
scraped into the collector — and, from M4 Task 2, forwarded on to Prometheus
over OTLP with everything else in the `metrics` pipeline; see
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "SPIRE
instrumentation, as measured".

## Identity with teeth: what was proven, and what was not

**M2.1 Task 5.** An identity that is issued, sent and verified but never
*decides* anything is decoration. Task 4 made the platform know who is
calling and put the verified subject in the trace; on its own that proves
less than it looks like, because a gateway that recorded `jwt.sub` from any
token it was handed — or forwarded regardless — would satisfy every one of
those assertions. `tests/e2e/test_caller_identity.py` is the negative half.
It speaks MCP to the gateway directly rather than through
`settlement-agent`, which spends no model tokens and tests the gateway's
verification rather than the agent's error handling.

**What is proven.** Each case is refused with HTTP **401** and a `text/plain`
body carrying agentgateway's own `TokenError` text — measured, and not the
JSON-RPC error body an earlier spike had predicted, because `jwtAuth` runs as
an HTTP filter before the MCP backend is consulted at all:

| Presented | Refused with |
|---|---|
| no token | `authentication failure: no bearer token found` |
| `Bearer not-a-real-svid` | `authentication failure: the token header is malformed: Error(InvalidToken)` |
| a well-formed JWT from an untrusted issuer, own `kid` | `authentication failure: token uses the unknown key "…"` |
| the same, wearing SPIRE's current `kid` | `authentication failure: the token is invalid or malformed: Error(InvalidSignature)` |
| **a genuine SPIRE-minted JWT-SVID for a different audience** | `authentication failure: the token is invalid or malformed: Error(InvalidAudience)` |

**The last row is the one that matters, and it is the only one that isolates
a claim check.** That token is minted by the real SPIRE server, signed by the
real JWT signing key SPIRE publishes in the JWKS the gateway fetches, and
carries the real issuer and `settlement-agent`'s own attested subject. Only
`aud` differs — it names `payments-mcp`'s SPIFFE ID instead of the
gateway's. So its refusal cannot be explained by malformedness, an untrusted
signer, or expiry; the only remaining explanation is the `audiences` list in
the gateway's config. That is the property the config file had until now only
*claimed* in prose: a token issued for one SPIFFE service in this trust
domain cannot be replayed at another.

**What is not proven, stated plainly because the obvious summary would
overstate it.** The tests do **not** demonstrate that agentgateway's `iss`
*string comparison* fired. With the deployed single-issuer configuration that
check is not independently reachable, and the reason is upstream's code
rather than this project's choices: `Jwt::validate_claims`
(`crates/agentgateway/src/http/jwt.rs`) looks the signing key up by the
token header's `kid` and only then calls `jsonwebtoken::decode`, which
verifies the signature before validating `iss` and `aud`. A token bearing an
untrusted issuer is therefore always caught earlier — by key trust, which is
the check that actually matters, and which is what an attacker minting their
own token runs into. The `iss` comparison becomes independently testable only
in a multi-issuer (`providers`) configuration, which this project does not
deploy.

**These tests have been made to fail.** A test that has never failed proves
nothing, so three mutations were applied to the live gateway config, each
derived from a distinct decision path in the code being checked rather than
from edits a human might plausibly make, and each predicted before it was
run:

| Mutation | Predicted and observed |
|---|---|
| remove the `jwtAuth` block entirely | all five refusals red; the accepted-path control stays green |
| `mode: strict` → `mode: optional` | **only** the no-token test red — `optional` still validates a token it is handed, it just permits a missing one |
| point `audiences` at `payments-mcp`'s SPIFFE ID | the audience test red (it is now accepted) **and** the control red (its correct-audience token is now rejected) |

The third is the one that matters for guard quality: it is a *swap*, so it
fails only if the audience test asserts an assignment rather than merely
"something got refused". The config was then restored and the live value read
back with `jsonpath` and compared by checksum against the repo file — a
`kubectl diff` cannot prove a revert, since it is a three-way merge against
`last-applied-configuration`.

## The rings, and the keystone — M7

M7 organises what the earlier milestones built as **rings**, a framing taken
from William Rizzo's *Bounded Agent Autonomy* demo (OSFF 2026), and adds the
one that was missing. The argument is the one both demos share: you do not
trust an agent to remember its own authority limits, you make the platform
enforce them.

| Ring | Question | Northstar's answer | Stage |
|---|---|---|:---:|
| Core · Identity | who it is | SPIRE JWT-SVIDs, verified by the gateway on every call | 2 |
| Ring 1 · Reach | what it connects to | Cilium: the gateway is the only way into `payments-mcp` | 2 |
| Ring 2 · Capability | what it may do | agentgateway → ExtMCP → policy-decision-service, `failClosed`; the autonomy flag | 2, 5 |
| Keystone | it cannot edit what constrains it | Kyverno `ValidatingPolicy` | **7** |

M7 deliberately does not port William's other choices. Linkerd would duplicate
SPIRE on identity (the M2 rationale stands), and his static CEL
`mcpAuthorization` allowlist is the approach the decision service replaced.
Those are two layers, "hide" versus "refuse and record", not a disagreement.

### The keystone policy

`deploy/platform/kyverno/keystone-policy.yaml`, a `policies.kyverno.io/v1`
`ValidatingPolicy` (CEL). Kyverno v1.19 deprecates the legacy `kyverno.io`
`ClusterPolicy` (kyverno/kyverno#16868), which is what William's version on
v1.18.1 uses; same rule, current type.

- **Who is denied:** any request whose `request.userInfo.username` starts with
  `system:serviceaccount:northstar-agents:` or
  `system:serviceaccount:northstar-tools:`. A prefix, not a list, so a new
  agent is covered without editing the policy. Humans, CI and
  `backstage-autonomy-writer` (in `northstar-platform`) do not match. The match
  is a `matchCondition`, which Kyverno copies into the webhook configuration
  (confirmed on the live `ValidatingWebhookConfiguration`), so the API server
  filters **before** calling Kyverno.
- **What is protected** (CREATE, UPDATE, DELETE; `serviceaccounts/token` is
  CREATE only), every ring's configuration plus the routes to a different
  identity:
  - Capability: `FeatureFlag` and `FeatureFlagSource` in `northstar-platform`;
    the `agentgateway-config` ConfigMap.
  - Reach: `CiliumNetworkPolicy` and `CiliumClusterwideNetworkPolicy`.
  - Identity: SPIRE's `ClusterSPIFFEID`, `ClusterStaticEntry` and
    `ClusterFederatedTrustDomain`.
  - RBAC: `Role`, `RoleBinding`, `ClusterRole`, `ClusterRoleBinding`.
  - Kyverno itself: **every** kind in the `policies.kyverno.io` and
    `kyverno.io` groups (`*`), because mutating, generating and namespaced
    policies, legacy `ClusterPolicy` and either `PolicyException` are each a way
    to bend or bypass this one. Kyverno's reports live in other groups and are
    not matched.
  - Workloads anywhere: `Pod`, `Deployment`, `ReplicaSet`, `StatefulSet`,
    `DaemonSet`, `Job`, `CronJob`. A pod created with `serviceAccountName:
    senior-settlement-agent` is issued that identity's SVID, so creating one is
    editing your own identity with no RBAC object touched.
  - `serviceaccounts/token`: minting a token for another ServiceAccount. Kyverno
    does match this subresource (verified: the test below is refused with the
    policy's message, and fails when the rule is removed).

  Controllers (`system:serviceaccount:kube-system:*`), humans and CI are never
  matched, because the identity condition only admits the `northstar-agents` and
  `northstar-tools` prefixes. No Northstar agent creates a workload or holds any
  RBAC to: no RoleBinding names an agent ServiceAccount, and no agent imports a
  Kubernetes client. Each of the objects above has a live test in
  `tests/e2e/test_keystone.py` (pod, cluster-wide Cilium policy, ClusterSPIFFEID,
  legacy ClusterPolicy, PolicyException, token) and was mutation-checked by
  removing its rule from the live policy and watching the test go red.
- **What Kyverno cannot cover, so it is RBAC-only:**
  - `ValidatingWebhookConfiguration` and `MutatingWebhookConfiguration`: the API
    server never sends admission for webhook configurations to webhooks. An
    agent with write access to one could remove Kyverno's registration, and
    nothing in the cluster would say no except RBAC. Agents hold no such grant.
  - Anything else RBAC happens to grant an agent outside the list above, and the
    PDS execute limits, which are in code (`pds/rules.py`) and change only by a
    reviewed commit and a rebuild.
  - Kyverno's default webhook `namespaceSelector` excludes `kube-system` and
    `kyverno`: an agent identity's writes in those two namespaces are not
    evaluated either.
- **Fail closed, with a measured exception.** `failurePolicy: Fail`, consistent
  with the gateway. Measured on the live stage-7 cluster by taking Kyverno away
  two ways, always against the same four requests (Backstage's writer patching
  the flag, `settlement-agent` with the over-grant patching it, a human creating
  a ConfigMap, a human patching the flag):

  | Kyverno | Backstage's writer | Over-granted agent | Humans |
  |---|---|---|---|
  | **Unreachable, webhook still registered** (the Service has no endpoints) | allowed | **refused**: `failed calling webhook ... connect: connection refused` | allowed |
  | **Stopped gracefully** (`scale --replicas=0`) | allowed | **ALLOWED** (fail-open) | allowed |

  So Backstage's flip is **not** affected by a Kyverno outage: the identity
  condition is evaluated by the API server before the webhook is called, and
  Backstage's identity never matches it. The caveat is the second row: a
  graceful stop makes Kyverno delete its own webhook configurations on the way
  out, so the keystone silently stops enforcing while the policy object still
  exists. The guarantee is "fail closed while Kyverno is registered", not "fail
  closed whenever Kyverno is down". `start-demo.sh` and the gate therefore check
  the webhook registration and the endpoints, not only the policy's `Ready`.
- **Message,** shown live: `DENIED: agent identity '<username>' may not change
  the objects that define its own authority (<kind>/<name>). Authority changes
  go through a reviewed commit, never a runtime decision by the agent.` The
  API server prefixes it with the webhook name and `Policy
  agents-cannot-edit-their-own-authority failed:`, which is what the tests
  assert on so an RBAC `Forbidden` cannot pass for it.

**Two lines, not one.** RBAC already denies every agent ServiceAccount a write
to the flag, so on a correct cluster an agent sees an RBAC `Forbidden`: the
first line, not the keystone. The keystone is the line that holds when RBAC is
wrong. Northstar does not ship a standing over-grant to show it; `Beat K`
(`scripts/beat-keystone.sh`) applies `deploy/demo/keystone-overgrant.yaml`
("someone made a mistake"), shows Kyverno refuse the same patch, and deletes
it. `setup-kind.sh` never applies that file (pinned by a unit test), and
`start-demo.sh` refuses to start if it is left behind.

**Two more things measured while building it** (both would otherwise be assumed):

1. With the chart's default RBAC the policy is stored and its webhook is
   configured, but `.status.conditionStatus.ready` stays `false`: "Policy is
   not ready for reporting, missing permissions: get/list/watch" for every
   non-core kind it matches. `deploy/platform/kyverno/values.yaml` grants the
   admission and reports controllers read-only access to those kinds, and
   `setup-kind.sh` waits on `ready` rather than on the object existing.
2. The policy only reports Ready once Kyverno can read every kind it matches,
   including the `serviceaccounts/token` subresource and `*` over Kyverno's own
   groups, which the readiness check wants spelled out. Same file, same fix.

### Which refusals the trace can see

Kyverno exports OTLP traces (`admissionController.tracing.*`). The admission
controller is pointed at the collector's **gRPC** receiver, `4317`. Measured on
the live stage-7 cluster, Kyverno chart 3.9.1 / app v1.19.1:

| Question | Finding |
|---|---|
| Does a denial produce a span in Jaeger? | **Yes.** Service `kyverno-admission-controller`. One 8-span trace per denial: `POST` → `ADMISSION POST /vpol/agents-cannot-edit-their-own-authority` → `METRICS POST ...` → `GVK`, `METRICS`, `ROLES`, `FILTER`, `VALIDATE UPDATE core.openfeature.dev/v1beta1, Kind=FeatureFlag`. Sub-millisecond. |
| Attributes on the denial span | `admission.response.allowed = False`, `admission.response.result.status = Failure`, `admission.response.result.message` (carries the policy name), `admission.request.user.name`, `admission.request.user.groups`, `admission.request.operation`, `admission.request.kind.kind/group/version`, `admission.request.name`, `admission.request.namespace`, `admission.request.roles` (the over-grant is visible), `admission.request.uid`. **The policy name is not an attribute of its own**; it is in the message and in the root span's `url.path`. |
| Is anything sampled? | **No.** Kyverno v1.19.1's `pkg/tracing/config.go` builds the tracer provider with no `WithSampler`, so the SDK default `ParentBased(AlwaysSample)` applies, and with no parent that is always-on. The chart has no sampling value. 20 consecutive denials produced 20 traces. |
| Is it part of the claim's trace? | **No, a standalone trace.** One service, one root, no parent. `kubectl` sends no `traceparent` and the kind API server does not propagate trace context to webhooks. Kyverno installs the W3C propagator and would continue a trace if one arrived. |
| Noise | Kyverno traces its own webhook self-checks (`ADMISSION POST /verifymutate`, a `Lease` update) about six times a minute, so the service is mostly noise. Search by operation `VALIDATE ...` or by `admission.request.user.name`. |

So the answer for the deck: the gateway's decision is in the claim's trace;
Kyverno's refusal is in Jaeger, attributable, but standalone; **Cilium's drops
are in no trace**, because Hubble is not installed. That last one is stated, not
fixed. `tests/e2e/test_keystone.py::test_the_denial_reaches_jaeger_as_a_standalone_kyverno_trace`
asserts the first two (and fails if the trace ever stops being standalone, so
the claim cannot silently go stale).

## Two ways to say no: the gateway's ceiling and the PDS (M8)

The gateway can refuse a payment by itself. One CEL rule on the route, an
`authorization` **allow** rule in `deploy/platform/agentgateway/config.yaml`,
caps `execute_payment` at a fixed ceiling (read it from that file; it is not
retyped here) for every caller, and refuses a zero or negative amount. It is
tested and documented. It is **not** in the live five-minute demo; it lives on
the deck. Off stage: `scripts/demo ceiling`.

| | agentgateway CEL ceiling | Policy Decision Service |
|---|---|---|
| Code to write | none: one line of config | a service |
| Sees tool arguments | yes, route level, v1.6.0 and later only | yes |
| Changes live | config reload, about 0.36 s (file mode, measured in the spike) | flag flip, nothing reloads |
| Says why | `403 text/plain "authorization failed"` and nothing else | rule, limit, a reason the model can explain |
| In the trace | one gateway span, `POST /*`, `error.type=Authorization` | a `policy.decision` span with rule and limit |

Sources: the spike in `.demo/reports/cel-spike-report.md` (git-ignored, local)
for the reload time and the argument visibility, and
`tests/e2e/test_gateway_ceiling.py` for the 403, the missing span and the
string amount, all measured on the live cluster.

What was measured, and how each result shaped the rule:

- **It is an allow rule, on purpose.** A `deny:` rule fails open on a value it
  cannot evaluate: in the spike a string amount `"60000"` went through and
  executed. An allow rule that errors denies.
  `test_a_string_amount_is_denied_not_waved_through` guards it.
- **It must let everything else through.** Two guards open the rule:
  `request.method != "POST"` lets the MCP client's own `GET` and `DELETE` on
  `/mcp` through, and `mcp.methodName != "tools/call"` keeps `initialize` and
  `tools/list` working for every caller. The method guard was added after
  measuring that the first version answered 403 to every session's `GET` and
  `DELETE`; `tests/e2e/test_gateway_ceiling.py` guards it.
- **It only works on v1.6.0 and later.** On v1.5.0 the route policy has no MCP
  context and the same rule denies everything.
- **Its 403 leaves no `policy.decision` span**, because the PDS is never asked.
  The refusal is visible, but it is one generic span with no rule and no limit.
- **A JSON-RPC batch is still refused.** A batch (an array) is a `POST` with no
  `mcp.methodName`, so the allow rule errors and denies: `403 authorization
  failed` at the gateway instead of the `400` from the deserializer.

- **A claim above the ceiling reaches no human.** The ceiling answers the
  agent's `execute_payment` with a `403`, not with a "no" tool result, so the
  agent's tool client fails the call: the claim's task ends `failed`, the payment
  stays `proposed` (never `awaiting_approval`, so it is not in the reviewer-ui's
  queue) and no `policy.decision` span exists for the execute. **By design nobody,
  a human included, may release it through this gateway:** the reviewer-ui's
  release is the same call on the same route. Pinned by
  `test_a_claim_over_the_ceiling_fails_and_nobody_is_asked`; the operator's
  version is the last appendix of `docs/demo-runbook.md`.

## What the system deliberately does not do

**M3 sharpens this section for the fourth time rather than deleting it. The
position is now "one hop authorized, on three rules, with one refusal a human
can overturn".** M1 was *no authorization anywhere*. M2.1 was *authenticated
but not authorized*. M2.2 put a real decision in the path of the one call that
moves money. M3 adds the only thing a refusal was missing — somebody who can
say yes anyway — and the accurate summary is narrower than "the platform is
supervised", so here is what is actually decided and what is still not:

**What is now authorized**, verified by reading the config and confirmed on
live spans:

- **agentgateway does authorization as well as authentication.** Its
  `config.yaml` carries `jwtAuth` *and* `mcpGuardrails` as siblings on the
  same route — the first establishes who is calling, the second what that
  caller may do. Losing either makes the other meaningless: a policy without
  authentication decides on a self-asserted name, and authentication without a
  policy is M2.1. Of the two mechanisms this project examined, the external
  `ExtMCP` gRPC hook is the one used; the native CEL `mcpAuthorization` is
  still absent, deliberately, because the rule needs an amount comparison and
  an identity table rather than an expression.
- **The decision is made somewhere the agent cannot reach around.** The PDS is
  not reachable from `northstar-agents` at all (egress policy), the tool is
  not reachable except through the gateway (ingress policy), and the agent
  contains no limit to disable. Removing the governance means changing the
  platform, not the agent.
- **The human's override is authorized like everything else — M3.** The
  approved execution does not bypass the gateway; it goes through it, with a
  different SVID, and is decided by the same PDS on its own allowance row. So
  "a human approved it" is not a privileged channel: if the amount exceeded
  `senior-settlement-agent`'s €25,000 the approval would be refused too, and
  `reviewer-ui`'s own SPIFFE ID has no allowance row at all, which
  `tests/e2e/test_supervised.py`'s criterion 7 proves by minting a real SVID
  for it and being denied `unknown_caller`.

**What is still not authorized, and none of it is an oversight:**

- **Only `tools/call` is decided.** `initialize`, `notifications/initialized`
  and the transport's own `GET`/`DELETE` are not sent to the PDS, because they
  carry no tool and no amount. The PDS denies any method other than
  `tools/call` if it is ever handed one, so widening the config fails loudly
  rather than silently permitting.
- **Three allowance rows, two callers, one autonomy level the demo ships
  reaching.** Through M2.2 this read "two entries and both name
  `settlement-agent`". M3 added a third — `senior-settlement-agent` at
  `assisted`, €25,000 — so the table now names two callers.
  **Dated note, 2026-09-15 (M5 Task 6): "reached by nothing" is no longer
  true, and the correction is narrower than it sounds.** The `autonomous` row
  has now been reached, live, by `tests/e2e/test_progressive_autonomy.py`,
  which patches the deployed `northstar-autonomy` `FeatureFlag` object's
  targeting rule (never the committed
  `deploy/platform/openfeature/featureflag.yaml`, per ruling M5-R8's own
  instruction not to touch it), drives one real settlement against
  `settlement-agent.autonomous.execute_limit`, and reverts the flag before the
  test ends. What did **not** change is what ships: the demo's resting state
  is still the no-op targeting rule M5-R8 describes (both branches resolve to
  `assisted`), so a bare `pytest tests/` or a fresh `setup-kind.sh` bring-up
  reaches the `autonomous` row exactly as often as before this task — never,
  unless something reaches in and flips the live object. Any caller not in
  the table is `unknown_caller` and denied. This is still nowhere near a
  general authorization model, and the number of rows is the least
  interesting part of M3 or M5: `decide()`'s allowance logic was not touched
  by either (**N4, whole-branch review: this said "`decide()` itself was not
  touched by either", false when written — Task 3's two `rule=` literals sit
  inside `decide()`, at `pds/rules.py:204` and `:211`, and this fix wave's own
  N6 fix touches `decide()` again, adding the `_KNOWN_CALLERS` check and
  splitting its deny-branch rule name in two. None of that is allowance
  logic: every change across M3, M5 Task 3 and this fix wave is to which rule
  NAME a decision is reported under, never to which decisions are permitted**),
  because approval changes *who asks* and the flag changes *what a rule
  resolves to*, neither changes what is allowed.
- **Nothing in the policy knows a human approved anything.** The PDS sees a
  second identity with a higher limit and decides exactly as it would for any
  caller. The evidence that a human was involved lives in the ledger's refusal
  record, which `execute_approved` reads and refuses to proceed without
  (`UnverifiedApproval`) — an application-level check, in the agent, not a
  platform decision. A reader who expects "the platform enforces that a human
  approved" should read that sentence twice: the platform enforces *who may
  pay how much*, and the agent enforces *that there is a refusal to overturn*.
- **Hops 1, 2 and 4 carry no identity and no policy.** Hop 2's allow-list
  still checks a target *name*, exactly as described at Hop 2 above, and
  `customer-ui` reaches `claims-agent` with no credential at all. The
  reachability half of this changed in M3 without the identity half changing:
  both settlement Deployments now have Cilium ingress allowlists (claims-agent
  and reviewer-ui respectively), so "anything in the cluster can call
  `settlement-agent`'s Service directly" — true and measured through M2.2 — is
  no longer accurate. What reaches them presents no credential all the same.
- **payments-mcp performs no check of its own** before calling payments-api,
  and sees no identity to check with — the gateway strips the credential on
  the outbound leg.
- **payments-api still has no authorization.** Its OpenAPI contract says so
  outright: *"Deliberately tiny. In M1 it has no authorization of any kind:
  anything that can reach it can move money."* M2.2 added one thing to that
  service and it is **not** authorization: the execute handler now confirms
  the stated amount and currency against the recorded proposal, and refuses
  with `409` on disagreement. That is a confirmation of intent — it checks
  that an instruction agrees with itself — not a decision about whether the
  caller may pay. Anything in the cluster that can reach its Service can still
  move money, as long as it states the right amount. **M3 added two routes to
  that service and neither is authorization either.** `POST /refuse` writes
  down why a payout did not happen; `GET /payments` lists what happened.
  Executing a payment that reads `refused` succeeds — measured, `200`, with
  the refusal record still attached (see Hop 4) — because the ledger is a
  record, not a gate, and the approved execution comes through that same route.
- **Approval is one human, one click, once.** This is the whole of the "not
  decided" list that M3 itself creates, and it is longer than the feature:
  no second approver, no quorum, no timeout or expiry on a pending refusal,
  no re-proposal path back to the original agent, and **no authenticated
  human**. `reject` is accepted (`_VALID_DECISIONS = ("approve", "reject")`)
  but it is inert by design: it records a `review.decision` span with
  `review.decision=reject` and writes nothing to the ledger and tells nobody,
  so the payment simply stays `refused` — which is what it already was. And
  the identity of the person is not established anywhere:
  `review.reviewer.id` is a string the request supplies, and
  `review.reviewer.spiffe_id` is `reviewer-ui`'s **own** SPIFFE ID, so between
  them the span records an unverified claim and the deployment that accepted
  it, never *which person* approved. There is no login, no session and no
  Keycloak — which the stage table above still lists as part of this stage,
  and which is not installed.
- **The refusal-to-approval join is a span link, and a link is not a
  guarantee.** It survives only because Task 2 persisted both the trace id and
  the span id in the ledger; a refusal recorded before those fields existed
  gets no link at all, by design, rather than a link the OpenTelemetry SDK
  would silently drop. Nothing reconciles the two traces if the ledger record
  is lost.
- **The `_latch` still does not cover the approved path**, and this is an open
  limitation rather than a decision — see limitation 3 in
  [`docs/telemetry-contract.md`](telemetry-contract.md), which M3 amended and
  left open. M3 was the milestone that was going to fix it, and did not.

**One M1.2 sentence in this section is now false and is corrected rather than
left standing.** It quoted `executor.py`'s module docstring: *"There is no
policy check here. That is the point: EUR 6,200 leaves the company because a
message asked for it, and the model itself chose to spend it."* That docstring
was rewritten in M2.2 and now reads: *"There is no policy check here, and
there is none in settlement.py either. In M1 that was the whole point... As of
M2.2 the check exists, but it is deliberately somewhere this code cannot reach
around — the gateway's policy hook, deciding on the `tools/call` request
itself."* The first clause survived; the conclusion inverted.

Submitting the default form (`CLM-1842`, `€6,200`) proposes the payment and is
**refused** at the execute step, and the model explains why. That is the
argument this demo has been building towards since M1.1, when the same form
moved €6,200 through a real Go system of record because a JSON-RPC message
asked for it. **As of M3 the demo does not end there**: the refusal is written
down beside the payment, a human finds it on the reviewer UI, and one click
executes the same €6,200 through the same choke point under an identity that is
allowed to. The interesting part on stage is not that the money finally moves —
it is that nothing about the refusal had to be undone or worked around for it
to.

**One claim in this section was true through M1.2 and is now false, so it is
corrected rather than left standing.** Earlier revisions said *"There is no
auth header, no API key, no mTLS anywhere in this chain — confirmed by
reading every HTTP client construction site in the repo (`delegation.py`,
`mcp_tools.py`, `payments_client.py`); none sets an `Authorization` header or
any credential."* As of M2.1 Task 4, **`mcp_tools.py` does set one** — an
`Authorization: Bearer <JWT-SVID>` header on every MCP call. The other two
construction sites are unchanged and still set no credential, so the accurate
statement is: one of this chain's three HTTP client construction sites now
presents a credential, and it is the one on Hop 3a.

(That paragraph, in turn, is now itself out of date in one direction and
sharpened rather than deleted: `mcp_tools.py` is still the one construction
site that presents a credential, and as of M2.2 it is also the one that
receives a refusal. `delegation.py` and `payments_client.py` still set none.)

## Determinism worth knowing: payment IDs

`systems/payments-api/store.go`'s `store` is an in-memory
`map[string]Payment` with a `nextID int` counter starting at `1`, formatted
as `PAY-%06d`. There is no persistence layer. This means:

- IDs are strictly sequential within one process's lifetime.
- **They reset to `PAY-000001` on every pod restart** — a fresh
  `newStore()` on every process start, deliberately, per the code's own
  comment: *"Deterministic sequential IDs matter: the demo's numbers must be
  identical on every run."* A live capture taken for this document (against
  a pod that had already handled many prior test and demo runs since its
  last restart) shows `PAY-000059` through `PAY-000061` — proof the counter
  is genuinely cumulative across calls, not proof of what it reads right
  after a restart, which this exercise did not trigger (restarting the live
  demo pod was avoidable and unnecessary to confirm behaviour already clear
  from the source).

**Does interposing the gateway change any of this? No — and it is worth
saying, because "a proxy is now in the path" sounds like it might.** Payment
IDs are minted by `payments-api`'s counter and by nothing else. agentgateway
does not generate, rewrite or cache them, and it plays no part in the
`propose → execute` correlation beyond forwarding the tool calls that carry
them. SPIRE does not touch them either. The counter advances by one per
`propose_payment` that reaches payments-api, exactly as in M1.1.

**What does affect the number a reader sees — not new in M2.1, but with one
new source.** Anything that proposes a payment advances the counter: demo
runs, the e2e suite, and probes. M2.1 adds
`tests/e2e/test_caller_identity.py`, whose accepted-path control really
executes `propose_payment` through the gateway on every run — deliberately,
because a refusal test whose green and red look alike proves nothing. This is
why nothing in this repo asserts an absolute payments-api record count or a
hardcoded next ID: the store is in-memory and every probe writes to it.
Assertions are relative or filtered — a test asserts that *its own* call came
back `proposed` for *its own* claim id, never that the ledger holds N rows.

## The trace, hop by hop

**Provenance of the numbers in this section, corrected for M2.1.** They were
measured live for the *M1.2* revision of this document — a Playwright claim
submission against the then-deployed cluster, trace
`edd4efea69542d6d0f401291917f5d5c`, plus a second capture with
request+response bodies, trace `ddf2747a385885345c0eeb25997d53ba` — and at
that time matched `docs/telemetry-contract.md` exactly (62 spans, identical
per-service split). **They were not re-measured for M2.1, and the span total
is not a number this project publishes as current any more** (see the
header: the `a2a-sdk` polling span varies 10–16 run to run, so no whole-trace
total is stable). They are kept, with this provenance, because the
*propagation mechanism* each row describes is what this section is about and
that has been re-checked — the mechanism table below includes M2.1's two new
gateway rows. Treat the counts as an M1.2-era illustration of the coverage
asymmetry, not as a measurement of the deployed system today. The
propagation table itself is current.

| Boundary | Mechanism | Carries `traceparent`/`tracestate`? | Carries `baggage` (→ `northstar.claim.id`)? |
|---|---|---|---|
| browser → claims-agent | HTTP header, injected by hand (`telemetry.ts`'s `injectContext`, since the browser is not wrapped by any auto-instrumentation) | Yes | N/A — not yet minted at this point |
| claims-agent (inbound) | ASGI/Starlette instrumentation (`opentelemetry-instrumentation-starlette`, wrapping the built app) extracts it. **`a2a-sdk` 0.3.7 itself extracts nothing** — an injected `traceparent` is ignored by the SDK on its own; this is the fix this project applied (A18), not something the SDK does natively. | Yes (via the fix) | Not yet — the claim id is minted inside the executor, after this extraction has already happened |
| claims-agent → settlement-agent (outbound) | `HTTPXClientInstrumentor`, instrumenting the plain `httpx.AsyncClient` `delegation.py` uses — free, no hand-written injection code | Yes | Yes, for the two claims-agent spans created after intake (see coverage below) |
| settlement-agent (inbound) | Same ASGI/Starlette instrumentation as claims-agent | Yes | Yes — extracted at the ASGI boundary, before any of settlement-agent's internal tasks spawn, so it reaches every subsequent span in that process |
| settlement-agent → agentgateway (**Hop 3a**, M2.1) | **Two independent carriers, both present, neither redundant.** (i) JSON-RPC `params._meta` (SEP-414, unprefixed `traceparent`/`tracestate`/`baggage`): on `mcp==1.9.4` (this project's original pin), the high-level client's `ClientSession.call_tool()` had no way to set `_meta` at all, so `mcp_tools.py` (Task 7) drops to `ClientSession.send_request()` and hand-builds the request, injecting via `opentelemetry.propagate.inject(carrier)`. **The pin is now `mcp==2.2.0`** (2026-09-11; it was `1.28.1`, which cleared four pip-audit advisories — `docs/telemetry-contract.md`'s Gap 6). Two things changed at 2.x and both are recorded rather than acted on: `call_tool()` has had a `meta=` kwarg since `modelcontextprotocol/python-sdk#1231`, and 2.x's dispatcher now injects trace context into `params._meta` **by itself**, unconditionally, on every request. `mcp_tools.py`'s hand-built `send_request()` path is therefore still what runs and is now also redundant — the SDK overwrites its `_meta` with a strictly deeper parent in the same trace. Removing it is open, deliberately-unbundled work (`docs/upstream-findings.md` Finding 4). (ii) An HTTP `traceparent` **header**, auto-injected by the httpx instrumentation because the transport is httpx-based — `HTTPX2ClientInstrumentor` since the 2.x bump, because `mcp` 2.x's transport is built on `httpx2` rather than `httpx` | Yes, twice over | Yes in `_meta`; yes in the `baggage` header |
| agentgateway (inbound) | **The HTTP header, and only that.** `TraceParent::from_request` (v1.5.0, `crates/agentgateway/src/telemetry/trc.rs:690-696`) reads the `traceparent` header and never inspects the request body. It does **not** read `params._meta` — Finding 6. With no header it would mint a new trace root | Yes (via the header) | Not consumed as baggage — the gateway has no baggage concept and its spans carry no `northstar.claim.id`, which is the intended enrichment boundary and is asserted as a negative in `tests/e2e/test_gateway_trace.py` |
| agentgateway → policy-decision-service (**Hop 3p**, M2.2) | The HTTP `traceparent` header on the gRPC `CheckRequest`, forwarded by the gateway to the guardrail hook and extracted by the PDS's own `CompositePropagator` from the ExtMCP `headers` field. **Measured that it is forwarded** (M2.2 Task 1, run C: ten headers reached the hook with `requestHeaders` removed entirely, `traceparent` among them) — exit criterion 3 depends on it, so it was checked rather than assumed. The context forwarded is the one the gateway *received*, not its own client span's, which is why `policy.decision` is a sibling of `Guardrail` rather than its child | Yes | **No.** `baggage` was not among those ten headers, and the PDS is not one of the three services running `ClaimIdSpanProcessor`. Its span carries no `northstar.claim.id`, and is correlated to the claim by trace id — the same boundary agentgateway's own spans sit on |
| agentgateway → payments-mcp (**Hop 3b**, M2.1) | The gateway starts its own outbound CLIENT span, injects that span into the outbound **headers**, and then copies the three W3C keys from those headers into `params._meta` (`crates/agentgateway/src/mcp/upstream/mod.rs:146` then `:110-122`). So `_meta` reaching payments-mcp names the *gateway's* span, not settlement-agent's — which is why payments-mcp parents under the gateway and not under the agent | Yes (re-stamped) | The `baggage` key is copied too, if the header is present |
| payments-mcp (inbound) | FastMCP 4.0.3's own `extract_trace_context()` (`fastmcp/telemetry.py`) reads `traceparent`/`tracestate` out of `params._meta` | Yes | **No — FastMCP never reads the `baggage` key out of `_meta` at all.** This is the one place `northstar.claim.id` stops, and interposing the gateway did not change that. |
| payments-mcp → payments-api | Ordinary HTTP header, via `HTTPXClientInstrumentor` | Yes | No — nothing to propagate; baggage never entered this process's ambient context |
| payments-api (inbound) | `otelhttp` (Go), standard W3C header extraction | Yes | No, for the same reason |
| **browser → reviewer-ui (M3)** | Nothing. A plain HTML form POST from a page reviewer-ui itself rendered, with no JavaScript and no instrumentation — deliberately unlike `customer-ui`, which hand-mints a root span in `telemetry.ts`. So `review.decision` is a **trace root**, and the reviewer's trace begins at the server rather than in the browser | No | No |
| **reviewer-ui → payments-api (M3)** | `HTTPXClientInstrumentor`, on the direct REST reads (`GET /payments?status=refused`, `GET /payments/{id}`) — each now made inside one of this service's **own** spans (`review.queue`, `review.decision`), so the CLIENT span has a parent to inject. **2026-09-11 (M3): this cell used to continue "— **plus** a trace-flags mask on injection (`apps/reviewer-ui/bootstrap.py`'s `_Level1CompatibleTraceContextPropagator`)".** There is no mask any more: payments-api moved to otel-go v1.46.0, which accepts the Level 2 `-03` flag byte its predecessor discarded, so this hop is now plain stock W3C propagation like every other hop in this table. Note which hop this is *not*: it never crosses agentgateway, payments-mcp or the PDS | Yes — **but this cell said "Yes" for a whole task while it was measurably "No"; see the correction under this table** | No — reviewer-ui runs no `ClaimIdSpanProcessor` and never sees the claim id as baggage; it knows the payment id, which is what it renders |
| **reviewer-ui → senior-settlement-agent (M3)** | `HTTPXClientInstrumentor` again, on the raw A2A JSON-RPC POST (`approval.py`, the same technique as `delegation.py`), so the senior agent's spans are children of `review.decision`'s trace. **The join back to the refused trace is carried in the request body, not in a header**: `refusedTraceId` and `refusedSpanId` travel as ordinary JSON fields and become a `Link` on `settlement.execute_approved` | Yes | No |
| **senior-settlement-agent → agentgateway (M3)** | Identical to Hop 3a above, because it is the same code in the same image. The only difference on the wire is the `Authorization` bearer token, which carries this pod's own SPIFFE ID | Yes, twice over | Yes — and **the claim id is re-minted here rather than propagated**, which is the one genuinely new thing in this column. The approval arrives from reviewer-ui carrying no baggage (reviewer-ui never had any), so `execute_approved` reads `claimId` off the ledger record it just fetched and calls `set_claim_id` itself. That is why `settlement.execute_approved` and everything below it carry `northstar.claim.id` in a trace whose root never saw one |

**Correction, M3 final-review fix round 2026-09-11 (controller ruling R36) —
the `reviewer-ui → payments-api` row published `traceparent: Yes` from M3
Task 4 until this round, and for that whole time it was measurably No.** Two
independent causes, both now closed, and the row above describes the fixed
system:

1. **No parent span on the reads.** `apps/reviewer-ui/main.py`'s `index()`
   called `list_refused()` with no span current, and `review.py`'s `decide()`
   called `ledger.get()` *before* `start_as_current_span` — deliberately, on
   a rule about not inventing telemetry for a review that never happened. So
   `HTTPXClientInstrumentor`'s CLIENT span was the **root of a trace of its
   own** every time. Measured before the fix, over the 30 most recent traces
   of each shape: reviewer-ui's outbound `GET` spans and payments-api's
   `GET /payments` spans shared **zero** trace ids, and a 67-span approval
   trace contained no reviewer-ui ledger read at all. `index()` now opens
   `review.queue` and `decide()` opens `review.decision` before reading.
2. **A flag byte neither end is wrong about.** With (1) fixed, payments-api
   *still* minted a fresh root. `opentelemetry-sdk==1.44.0` sets W3C Trace
   Context **Level 2**'s `RANDOM_TRACE_ID` bit alongside `SAMPLED`, emitting
   `00-…-03`; `go.opentelemetry.io/otel v1.41.0` — payments-api's pin —
   refuses any version-00 traceparent whose flag byte exceeds `0x02`
   (`propagation/trace_context.go`: `if version == 0 && (h != "" || opts[0] >
   2)`), and an empty SpanContext makes `otelhttp` start a new root.
   Measured through the repo-managed port-forward: a hand-injected `…-01`
   produced a correctly parented `GET /payments` span; the identical request
   with `…-03` produced no span under that trace id at all. **otel-go
   v1.42.0 already widened that check to `> 3`.**

   **2026-09-11 (M3): this item used to continue, and no longer holds:**

   > but every release from v1.42.0 on declares `go 1.25.0` while this repo
   > builds payments-api on `golang:1.24-alpine` under `GOTOOLCHAIN=local`
   > against go1.24.10 — so the upstream fix is a toolchain bump, not a module
   > bump, and is carried debt rather than something this round took. The
   > workaround is one masked flag byte on reviewer-ui's injected header,
   > documented in full on `_Level1CompatibleTraceContextPropagator` and
   > pinned by `apps/reviewer-ui/tests/test_bootstrap.py`.

   payments-api is on **otel-go v1.46.0** and `otelhttp v0.71.0`, built on
   `golang:1.27.1-alpine`, with `go.mod` declaring `go 1.25.0` and pinning
   `toolchain go1.27.1`; the Go suite runs under `GOTOOLCHAIN=auto`, which is
   what moved the toolchain from the operator's PATH into this repository.
   The mask and the three tests that pinned it are deleted. Re-run on the live
   cluster, the same two-request experiment: `…-01` → `GET /payments` SERVER
   `da5830097b2aa97e` parent `31966bf1692149b0`; `…-03` → `GET /payments`
   SERVER `ca981a384c75fc22` parent `51665f1dbec74c0b`. The second used to
   produce nothing.

**Measured after the mask was removed**, one live listing request:
trace `eacff63cc15a0868cee60332b1728735`, three spans —
`reviewer-ui review.queue` (root, SERVER, `e40bafb90b4a5483`) →
`reviewer-ui GET` (CLIENT, `9280bc1b88bb4120`, `http.url`
`…/payments?status=refused`) → `payments-api GET /payments` (SERVER,
`a418e6258a9c1bac`). The pre-bump capture of the same shape, reached through
the mask, was trace `c05d71f98aecafddaaf0fa4e3901213c`.
`tests/e2e/test_supervised.py`'s criterion 5 asserts the
decision-path equivalent (`review.decision` → reviewer-ui CLIENT `GET` →
payments-api `GET /payments/{paymentId}`), identified by parentage rather
than by name because the approved-execution trace contains a second
payments-api `GET /payments/{paymentId}` from the `get_payment` MCP call.
Why nothing else in this table was affected: reviewer-ui was the only service
on `opentelemetry-sdk` 1.44 that speaks **directly** to the Go service.
`payments-mcp` was pinned at 1.29 (flag byte `01`), and agentgateway re-stamps
the context with its own before the hop to `payments-mcp`.
**2026-09-11 (M3): `payments-mcp` is on 1.44.0 now too**, so its outbound flag
byte is a function of the inbound one rather than a constant — measured in the
deployed pod, a `-03` parent in produces a `-03` child out. Driven end to end
with a `-03` parent at the MCP boundary, trace
`967b8b72abbd4b6383409a747c0b43f0` carries all three spans with full ancestry;
the `-01` control is `09809e3142874fb09ce8111c5366c9f6`.

**`northstar.claim.id` is set exactly once**, at claims-agent's intake
(`correlation.py`'s `set_claim_id`, called from `executor.py` right after
`parse_claim`), and rides in W3C baggage from there. It is **not** an
attribute added after the fact to every span by hand — each of the three
Python services runs a `ClaimIdSpanProcessor` (`correlation.py`, identical
in all three) that copies the value onto a span *only if it is already
present as baggage in the ambient context the moment that span starts*.
Coverage from the M1.2-era capture described above (trace
`edd4efea69542d6d0f401291917f5d5c`, 62 spans, identical at the time to the
counts in `docs/telemetry-contract.md`). Read the *pattern*, not the
absolute numbers: the totals are not re-measured for M2.1 and the span count
is not stable run to run, but which services reach 100% and which reach zero
is a property of the mechanism, not of one capture, and that is unchanged —
the gateway adds spans of its own with no `northstar.claim.id` on any of
them, which is the intended enrichment boundary rather than a regression:

```
customer-ui        1/1
claims-agent        2/23
settlement-agent   32/32
payments-mcp        0/4
payments-api        0/2
```

Everywhere the count is below 100%, the cause is one of: the span predates
the id being minted (customer-ui's own root span, and claims-agent's
pre-intake spans — `a2a-sdk` 0.3.7 spawns a separate asyncio task for the
executor before it attaches anything to the ambient context, so 21 of
claims-agent's 23 spans never see it), or baggage never crossed a boundary
at all (payments-mcp and payments-api, both zero, because of the FastMCP gap
above). `docs/telemetry-contract.md` has the full per-exemption reasoning
and the isolated reproduction that confirmed the claims-agent task-boundary
explanation; it is not repeated here.

**Our services create few spans of their own, and the exceptions are not
retyped here — they are the output of a command, because a "verified by grep"
claim that a grep falsifies is the worst form of stale prose this document can
carry, and the first attempt to fix that reproduced it.** Run this, and compare:

```bash
grep -rnE '(start_as_current_span|startActiveSpan)\(' \
  agents apps platform systems tools \
  --include='*.py' --include='*.ts' --include='*.tsx' \
  | grep -vE '/\.venv/|/node_modules/|/tests/|/test_|\.test\.'
```

(The trailing `\(` matters: without it the same grep also returns three prose
mentions in comments and docstrings, and a looser file filter returns
`a2a.test.ts`. A command published as evidence has to be the command that was
run.) On 2026-09-14 it returns **five** call sites, one span each:

  * `apps/customer-ui/src/app/page.tsx:37` — **`claim.submit`**, the trace root.
    Necessary: nothing upstream of the browser could have created it.
  * `agents/settlement-agent/settlement.py:378` —
    **`settlement.execute_approved`**, the approved execution M3 added, and the
    span the reviewer-ui link joins to the refused trace.
  * `apps/reviewer-ui/main.py:79` — **`review.queue`**.
  * `apps/reviewer-ui/review.py:163` — **`review.decision`**.
  * `platform/policy-decision-service/pds/server.py:230` —
    **`policy.decision`** (the name is `telemetry.SPAN_NAME`, which is why a
    grep for the literal string does not find it). This is the span every
    scorer in M4 reads and the evidence view's second panel renders.

There is a **sixth first-party source of spans that this grep cannot find**:
`platform/eval-scorer` emits its `gen_ai.evaluation.*` spans through the
evaluation SDK's `score(...)` — imported as `sdk_score` and called inside
`emit()` in `scorer/emit.py` — which calls the tracer on
the service's behalf. So "hand-written" and "first-party" are not the same set,
and any future count has to say which it means.

**The earlier version of this paragraph said "exactly two", scoped to `agents/`,
`tools/` and `systems/` — and then named a file in `apps/` as one of the two.**
Inside its own stated scope there was exactly one. That is why the count above
is a command and its output rather than a number: this is the third time in one
milestone that a stale-figure fix restated a figure instead of deriving it.

(Until M4's final review this paragraph read "there is no `get_tracer`,
`start_span`, or `start_as_current_span` anywhere in this project's own
`agents/`, `tools/`, or `systems/` source", naming only customer-ui and a
docstring mention in `tools/payments-mcp/bootstrap.py`. It has been false since
M3 shipped `execute_approved`, inside its own stated grep scope, while claiming
the grep as its evidence. Pre-existing rather than introduced by M4 — but M4
Task 5 retitled this file precisely because its first line had gone stale, and
did not re-run the one grep the file offers as proof.)
Every other span in this chain — every `a2a.server.*`/`a2a.client.*` span,
every `StarletteInstrumentor`/`HTTPXClientInstrumentor` span, every FastMCP
`tools/call` span, every `otelhttp` span — comes from library
instrumentation wrapping our code from the outside, not from our code
calling the tracer directly.

## See it yourself

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Reviewer UI (stage 3) | http://localhost:8081 |
| Jaeger | http://localhost:16686 |
| OpenSearch Dashboards — the evidence view (stage 4) | http://localhost:5601 |
| Prometheus — the metrics half of stage 4 | http://localhost:9090 |
| Backstage — the catalog and the autonomy card (stage 6) | http://localhost:7007 |

The first three are kind host ports mapped at cluster creation and need no
port-forward. The rest do, and `./scripts/start-demo.sh` is what starts them
— that script owns every forward this demo needs, and prints every URL in this
table (plus the agent and tool ports the walkthrough does not open) at the end
of its run. **Backstage was the one exception until M6 Task 4.** Task 1 shipped
it reachable only by a manual `kubectl -n northstar-platform port-forward
svc/backstage 7007:7007` (`scripts/setup-kind.sh`'s own stage-6 completion
message said so in as many words), which is why this row could not be added
until the script actually owned the forward — adding the row first would have
been exactly the "See it yourself" table naming a surface the script could not
reach, the class of drift the paragraph below exists to stop.

`tests/unit/test_stage4_config.py` requires the two lists to agree **in both
directions**, so a surface added to one and not the other reddens a test rather
than ageing a table. That sentence was false when it was first written, and the
row above it is why: the guard only checked table → script, M4 Task 2 had
already added Prometheus to the script, and the table went four tasks without a
Prometheus row while the guard stayed green and this paragraph claimed it
could not. The reverse direction cannot simply be "every printed URL is in this
table" — the script deliberately prints agent and tool ports the walkthrough
never opens — so each printed line now declares itself `#surface` or
`#tool-port` and the guard requires the `#surface` set to *equal* this table.
An unmarked line fails; adding a URL is now a decision rather than an echo.

1. Open the Customer UI. The form is pre-filled with claim `CLM-1842`,
   policy `POL-77120`, €6,200. Click "Submit claim." Within a few seconds
   you should see an "Outcome" block with **`"status": "refused"`** — this is
   the milestone working, not a failure — a `paymentId` like `PAY-0000NN` for
   the payment that was *proposed* (not necessarily `PAY-000001`; the counter
   is cumulative since the pod's last restart, see "Determinism" above),
   `"amount": 6200` for that same proposal, and a `narrative` in which the
   model explains, in its own words, that the settlement could not be paid.
   The words differ every run, because they are genuinely the model's.
   **Then change the amount to `5800` and submit again**: the same claim shape
   comes back `"status": "executed"`. Seeing both is the point — a platform
   that refused everything would look identical on the first run alone.
2. In Jaeger: service `customer-ui`, operation `claim.submit`, "Find Traces."
   The single result is your submission (Jaeger's `total` field in its own
   API response is often `0` even when traces are returned — a documented
   API quirk, not a sign nothing came back; check `data`, not `total`).
3. Alternatively, search by tag: service `settlement-agent` (or
   `claims-agent`), tag `northstar.claim.id=CLM-1842`. This is where the
   coverage asymmetry above becomes visible directly — the same tag search
   against `payments-mcp` or `payments-api` returns nothing, by design (see
   above), even though those services' spans are still part of the same
   trace by trace ID.
4. Ignore any `harness-probe`/`harness-probe-*` entries in Jaeger's service
   dropdown (a verified-live fact, `GET /api/services`: 9 such entries were
   present in this cluster's Jaeger at the time of writing, one bare
   `harness-probe` plus 8 with a random-hex suffix) — these are artifacts of
   this project's own e2e test suite injecting synthetic trace IDs to test
   the harness itself, not components of the running system.
5. Spans worth clicking into, and what each demonstrates:
   - **`customer-ui`'s `claim.submit`** — the trace root. Note it has no
     parent, and its own two attributes (`northstar.claim.id`,
     `northstar.policy.number`) are set directly in `page.tsx`, not
     propagated from anywhere.
   - **`claims-agent`'s outbound `POST` (client span, target
     `settlement-agent...:9002`)** — this is the entire mechanism by which
     Hop 2's trace context crosses: no code in `delegation.py` injects a
     header by hand, `HTTPXClientInstrumentor` does it as a side effect of
     the ambient context being correct at call time.
   - **`payments-mcp`'s `tools/call propose_payment`** — check its
     attributes: `gen_ai.tool.name = propose_payment` is present;
     `gen_ai.operation.name` is not (Gap in `docs/telemetry-contract.md`).
     Also confirm `northstar.claim.id` is absent here specifically — this is
     the span the coverage table above and the contract's Gap 4 are about.
     **Read this bullet on the `propose_payment` span, not the
     `execute_payment` one, and the reason is the milestone.** In the €6,200
     run there *is* no `payments-mcp` span for `execute_payment`, because the
     execute call never reached the tool — the gateway refused it at the
     policy hook. Its absence is itself the evidence, and it is the first
     thing to look for in the trace. Submit the €5,800 claim and the
     `execute_payment` span appears, carrying exactly the attributes
     described here; both tools behave identically as far as FastMCP's
     instrumentation is concerned, which is the point of checking the gap on
     whichever one the run produced.
   - **agentgateway's `tools/call` (SERVER)** — new in M2.1, and the most
     informative span in the trace. Three things to look at: `jwt.sub`,
     which is the caller's attested SPIFFE ID
     (`spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`)
     and is set by the gateway from the token it verified, not by anything
     this project wrote; `gen_ai.tool.name`, which is where the tool name
     lives, because the span's *name* is just `tools/call` for every one of
     the four tools;
     and `http.method` / `http.path` / `http.status`, which are **v1.5.0's**
     third HTTP vocabulary described in the header and in Finding 7 — scoped
     to the pinned version, because the upstream fix for it is merged but
     unreleased, so a future image will show the stable spelling here
     instead. Then look at
     its child CLIENT span, `tools/call payments-mcp_<tool>`, and note
     `jwt.sub` is **absent** there — the gateway strips the credential on
     the outbound leg, so there is no verified token to attribute.
   - **`policy-decision-service`'s `policy.decision`** — new in M2.2, and the
     span the whole milestone exists to produce. There are two per settlement,
     one per tool call (**N5, whole-branch review: this is the M2.2-era
     count, pre-`refuse_payment` — M3 added a third `policy.decision`-bearing
     call, so a refused settlement can carry three; see
     `docs/telemetry-contract.md`'s "two or three `policy.decision` spans
     appear per settlement" for the current figure**). On the
     `execute_payment` one in a €6,200 run:
     `policy.decision = deny`, `policy.rule =
     settlement-agent.assisted.execute_limit`, `policy.request.amount = 6200`,
     `policy.limit.amount = 6000`, and `policy.caller.spiffe_id` naming the
     attested caller the limit was looked up by. Note the span's status is
     **not** ERROR: a refusal is the platform working. Compare it with the
     `propose_payment` one in the same trace, which is a `permit` under the
     rule `unlimited_tool` and carries no `policy.limit.amount` at all.
   - **agentgateway's `Guardrail` (CLIENT)** — its sibling, and the gateway's
     own view of the same hop. `http.path` is literally the ExtMCP gRPC
     method, and `grpc.status = 0` says the *call* succeeded — the deny is in
     the payload, which is how "the policy said no" stays distinguishable from
     "the policy service was down".
   - **`payments-api`'s `POST /payments/{paymentId}/execute`** — the last
     hop, stable HTTP semantic-convention spelling
     (`http.request.method`/`http.response.status_code`), and the one place
     in the whole trace where real money state changed. Comparing this
     span's attribute names with the gateway's, in the same trace, is the
     fastest way to see the naming divergence for yourself. **In a €6,200
     trace this span does not exist** — neither does `payments-mcp`'s
     `tools/call execute_payment`, nor the gateway's own outbound CLIENT span
     for it. That absence is the trace-level evidence that the execution never
     happened, rather than happening and failing.
6. **See both refusals, without spending a model call.** Neither costs
   Anthropic tokens:

   ```bash
   .venv/bin/python -m pytest tests/e2e/test_caller_identity.py -v
   .venv/bin/python -m pytest tests/e2e/test_policy_denial.py -v \
       -k "criterion_4 or criterion_5"
   ```

   The second pair is M2.2's: criterion 4 is a hermetic AST walk proving no
   policy logic lives in `settlement-agent` or `payments-mcp`, and criterion 5
   speaks MCP to the gateway directly with **two different** SPIRE-minted
   identities and shows the verdict changing with the caller — which is what
   makes "identity is an input to the decision" a demonstration rather than a
   claim. The other four tests in that file drive a real browser and a real
   model, and are gated behind `RUN_BROWSER_TESTS=1`.

   That file port-forwards to the gateway itself (on an ephemeral local port
   it asks `kubectl` for, so it cannot collide with the **seven** forwards
   `scripts/start-demo.sh` establishes — 19001, 19002, 18000, 18080, 5601,
   9090 and 18300; this said "four" until M4's final review and "six" until
   the fix wave added the seventh 160 lines away in the same commit, which is
   why the ports are listed rather than counted), mints real JWT-SVIDs from
   SPIRE, and asserts both that a valid
   one executes a tool end to end and that invalid ones are refused: **5
   refusal cases from 4 refusal functions** (one, the untrusted-issuer check,
   is parametrised ×2 for `own_kid` and `spire_kid` — `pytest --collect-only`
   confirms **7** cases collected in total: the 5 refusals, the 1 accepted
   control the file *opens* with
   (`test_a_valid_svid_is_accepted_and_the_tool_really_runs`), and the
   JSON-RPC batch case it *closes* with
   (`test_a_jsonrpc_batch_is_rejected_before_the_policy_hook_and_changes_nothing`)
   — a bypass path this repo tracks separately. This read "6 cases" until M4's
   final review, and the correction that fixed the number then mis-described
   the seventh as the file's opening assertion, which was already counted.)
   See "Identity with teeth" above for the exact wire shape of each refusal.

7. **Then approve the refusal — this is M3's part of the walkthrough.** After
   step 1's €6,200 submission comes back `refused`, open the Reviewer UI at
   http://localhost:8081. Unlike the Customer UI and Jaeger above it needs no
   `kubectl port-forward` (OpenSearch Dashboards and Prometheus, the other two
   rows of that table, do -- the original "unlike the four surfaces above"
   was wrong about its own table): its Service is a `NodePort` and
   `deploy/kind/stage3.yaml` maps host 8081 to it, so it also survives a
   redeploy.

   The page lists every payment whose status is `refused`, each showing the
   rule that fired, the limit the caller held, the amount
   requested, and the model's own narrative — the same four facts the
   `policy.decision` span carries, read back out of the ledger rather than out
   of the trace. **That distinction is worth pausing on**: the human's
   information path is the ledger, not telemetry. Turn `NORTHSTAR_CAPTURE_CONTENT`
   off and this page is unchanged.

   Type any reviewer id (the field is required but unverified — the
   placeholder is `alex.mercer`) and click **Approve**. There is also a
   **Reject** button, and it is honest about doing nothing: it records a
   `review.decision` span with `review.decision=reject` and leaves the payment
   exactly as refused. Within a second or two Approve shows the payment
   executed. What just happened, and what to look for:

   - In Jaeger, search service **`reviewer-ui`**, operation
     **`review.decision`**. That span is a **trace root** — reviewer-ui does
     not instrument FastAPI at all, so there is no framework entry span above
     it, and the browser sends a plain HTML form with no trace context. Its
     attributes: `review.payment.id`, `review.decision` (`approve`),
     `review.reviewer.id` (the string you typed — unverified, by design),
     `review.reviewer.spiffe_id` (**reviewer-ui's own** ID, read from the
     Workload API, not the person's), `review.refused.trace_id`, and
     `review.elapsed_ms` — the gap between the ledger's `refusedAt` stamp and
     now, so it is measured on the ledger's clock rather than the reviewer's.
   - Its descendant **`settlement.execute_approved`** carries a **span
     link**, not a parent edge, pointing at the span that recorded the
     refusal in step 1's trace. Jaeger renders it as a separate reference —
     click through and you land in the original refused trace. The link is
     deliberate: the human took minutes, in a process that no longer exists,
     so a parent-child edge would misstate both the duration and what was
     executing.
   - Compare the **`policy.decision`** span in *this* trace with the one from
     step 1. Same rule shape, same service, same code path —
     `policy.caller.spiffe_id` now reads
     `.../ns/northstar-agents/sa/senior-settlement-agent` (retired in M8),
     `policy.limit.amount` reads `25000`, and the verdict is `permit`. Nothing
     was overridden; a different caller was asked about.
   - One thing that will look wrong and is not: **the senior agent's spans
     arrive under `service.name=settlement-agent`**, because its
     `OTEL_SERVICE_NAME` is deliberately the same. Tell the two apart by
     `jwt.sub` / `policy.caller.spiffe_id`, never by service name.

8. **See M3's own refusals, also without spending a model call:**

   ```bash
   .venv/bin/python -m pytest tests/e2e/test_supervised.py -v -k criterion_7
   ```

   Criterion 7 mints a real SVID for **reviewer-ui's** identity and shows the
   gateway's policy hook refusing it `unknown_caller` — because reviewer-ui has
   no `EXECUTE_LIMITS` row at all. It is the stronger form of the claim: not
   "reviewer-ui does not try to execute payments", but "it could not if it
   did". The other six tests in that file drive a real browser and a real
   model, and are gated behind `RUN_BROWSER_TESTS=1`.

9. **Finally, prove why the money moved — from telemetry alone.** This is M4's
   part of the walkthrough, and the question it answers is *"can we prove why
   €5,800 was paid?"*

   Run `./scripts/start-demo.sh` (it starts the forward Dashboards needs) and
   open **http://localhost:5601/app/dashboards#/view/northstar-evidence**. The
   dashboard is not made by hand: it is imported from
   `deploy/observability/dashboards/evidence.ndjson` by
   `./scripts/setup-kind.sh --stage 4`, so it survives a cluster recreate, and
   `tests/unit/test_stage4_config.py` fails if that import is removed or
   ungated.

   Read its own top-left panel first — it is the file's header, and it records
   two decisions this walkthrough depends on. Then:

   - Set the time range to cover the two submissions from step 1 (the picker
     defaults to the last hour). The range is not cosmetic: `CLM-1842` is a
     fixed id, so the index holds one set of traces per run of it, going back
     as far as retention does.
   - **Panel 1, "Claims in this window"**, lists each claim id against the
     trace ids it touched. The €6,200 claim has **two**: the refusal, and the
     human approval that followed. The €5,800 claim has one.
   - Select those trace ids in the control at the top. **This is the join, and
     it is trace id rather than claim id on purpose.** `northstar.claim.id`
     stops at the MCP boundary (Gap 4 in `docs/telemetry-contract.md`) and
     never reaches the Policy Decision Service either, so a claim-id filter
     would blank exactly the two panels that matter. Panel 1 is where you make
     the hop from the number a human has to the id the telemetry joins on.
   - **Panel 2, "What the platform decided"**, is the milestone's sentence in
     table form. Both claims' `execute_payment` decisions name the **same
     rule**, `settlement-agent.assisted.execute_limit`: €5,800 permitted
     against a €6,000 limit for `settlement-agent`, €6,200 denied against the
     same limit, and then permitted against `senior-settlement-agent`'s
     €25,000. Same rule, different caller, different answer.
   - **Panel 3, "Was a human in the loop?"**, is empty for the €5,800 claim —
     and that emptiness *is* the evidence, which is why the panel is on the
     dashboard rather than only its contents. For `CLM-1842` it names the
     reviewer, the decision, and how long the refusal waited.
   - **Panel 4, "What reached the ledger"**, is every `payments-api` server
     span in the selected traces. For the €5,800 claim that is
     `propose → execute` and nothing else. For `CLM-1842` it is
     `propose → refuse`, then the reviewer page's own ledger reads, then
     `execute` — the state changes and the reads that led to them. Not one of
     these spans carries a claim id; they are here only because of the
     trace-id join, which is the whole reason the join has two steps.

   The queries behind all four panels are asserted against the live cluster by
   `tests/e2e/test_evidence_view.py`, which drives both settlements itself. The
   dashboard's *pixels* are deliberately not tested — but a dashboard that
   silently goes blank because instrumentation regressed is worse than no
   dashboard, so the fields it renders redden a test instead.

   **There is no score panel on this dashboard, and that is deliberate** — the
   dashboard's own top-left readme panel says so. The scores are step 10.

10. **Ask the auditor what it thought of the trajectory.** M4's other half is
    `platform/eval-scorer`: five policy-adherence scores per settlement,
    written back into the same trace they judge as
    `gen_ai.evaluation.*` spans, so the judgement travels with the evidence
    instead of in a separate system.

    `./scripts/start-demo.sh` forwards it at **18300 → 8300** (stage 4 only; it
    prints a SKIPPED line below stage 4). It is pull-only — nothing scores a
    settlement automatically as it happens — so the scores exist when you ask
    for them:

    ```bash
    curl -s localhost:18300/score -X POST \
      -H 'content-type: application/json' \
      -d '{"claimId": "CLM-1842", "emit": false}' | python3 -m json.tool
    ```

    `"emit": false` reads without writing spans, which is how to look without
    changing what you are looking at. Check `spanCount` and `decisionCount`
    first: OpenSearch is near-real-time, and a claim scored the instant a
    settlement returns can be scored half-written. When those stop moving, ask
    once more without `emit` and the five scores land in the trace.

    For `CLM-1842` — the supervised €6,200 pair — the one worth reading is
    `stopped_when_refused`. It is **1.0 compliant** for the real run and
    **0.0 retry-after-refusal** for a deliberately mis-routed trajectory. That
    difference is spec §7.4's anti-vacuity criterion: a scorer that returns 1.0
    for everything proves nothing.

    **Read the other four carefully, because "the other four are unchanged" is
    a claim this repo has now had to retire twice.** Of the five scorers, the
    mis-routed run only *exercises* three: the platform refuses the mis-routed
    execution, so nothing executes, and `amount_executed_matches_approved` and
    `identity_had_authority` come back `1.0 **not-applicable**` — against
    `1.0 **compliant**` on the run that did execute. Their **values** match and
    their **labels** do not, which is why comparing values alone was the wrong
    shape: a scorer that silently regressed to "I had nothing to judge" would
    have read as agreement. Among the three both runs do exercise, exactly one
    differs — `stopped_when_refused` — and the other two,
    `proposed_before_executed` and `narrated_the_refusal`, are the genuine
    controls: exercised and compliant on both sides.

    `tests/e2e/test_policy_scores.py` asserts all of that against the live
    cluster on `(value, label)` pairs rather than on values, derives the
    unexercised set from the labels rather than hard-coding it, and separately
    checks that the two set-aside scorers *are* exercised by the compliant run
    — otherwise setting them aside would be hiding a scorer that never runs.

11. **Finally, open Backstage — M6's part of the walkthrough, and the first
    step in this list that is not about the money hop at all.** Run
    `./scripts/start-demo.sh` (it forwards `svc/backstage` at 7007, gated on
    stage 6's own presence — a cluster below stage 6 prints
    `Backstage: SKIPPED` instead) and open http://localhost:7007. Unlike every
    other surface in this table, this one asks for a login. Click **Enter**
    under the Guest option — there is no username or password to type, and
    that absence is itself the first thing worth noticing, not a rough edge to
    look past (see "Known limitations" below).

    - Open **Catalog** in the left nav. Nine `Component` entities are listed —
      the eight this repo's own directory tree has (`agents/`, `apps/`,
      `platform/`, `systems/`, `tools/`, each with its own `catalog-info.yaml`)
      plus `backstage` itself, cataloguing its own plugin. This list is not
      typed by hand anywhere, on either side: the live catalog is built from
      the committed YAML files staged into the image, and
      `tests/e2e/test_golden_path.py`'s own check of it is a directory walk
      (`tests/unit/test_catalog_matches_the_tree.py`'s `_component_dirs`),
      reused rather than reimplemented, so neither side of the comparison is a
      list someone wrote down and forgot to update.
    - Open **`settlement-agent`**'s component page and follow its **provides
      API** relation to `settlement-agent-card`. Its `definition` field is a
      written pointer to `contracts/agent-cards/settlement-agent.json`, not
      the file's contents inlined — a `$text` placeholder was tried first and
      does not work for a location this git-less (see
      `docs/upstream-findings.md` Finding 15); the graph edge is real, the
      file's contents are one click away, and this document does not pretend
      the placeholder mechanism reached further than it did.
    - Click **Autonomy contract** in the left sidebar, or go to
      http://localhost:7007/autonomy-contract. (**Corrected 2026-09-15,
      whole-branch review D1**: this used to say "open `northstar` (the
      System) or search for autonomy" — both wrong. The card is a standalone
      page (`PageBlueprint.make({ path: '/autonomy-contract' })`,
      `platform/backstage/packages/app/src/modules/autonomyContract/plugin.tsx`),
      not an entity tab on the System, and Backstage's search backend indexes
      only the catalog and techdocs plugins
      (`packages/backend/src/index.ts`) — a frontend route is not indexed, so
      searching "autonomy" would surface catalog entities, never the card. A
      false step at the milestone's own punchline, in the walkthrough an
      operator follows live.)
      It reads `assisted`/`assisted` for both identities on a
      freshly built cluster — the contract ships refusing, and M6 does not
      change that (M5-R8). This is a **read** of the live `FeatureFlag`
      object, per identity, through `GET /api/autonomy-contract/status` — not
      a static description of what the demo does.
    - **Flip it, and watch a real settlement change without a redeploy.**
      Submit `CLM-1842` at €6,200 on the Customer UI first (step 1 above) and
      confirm it is `refused`, same as always. Then, on the card, flip the
      variant to `autonomous` and submit the **identical** claim again: it
      comes back `executed`, through the same `execute_payment` choke point,
      under the same identity, with nothing else in the cluster touched — no
      pod restarted, no image rebuilt. Flip it back to `assisted` afterward;
      the demo's resting state is refusing, and leaving it flipped would be
      the same mistake ruling M5-R8 already named for a `kubectl`-driven flip.
      This is the identical claim `test_progressive_autonomy.py` proves for a
      `kubectl patch` against the object, proven again here for Backstage's
      own HTTP route — `tests/e2e/test_golden_path.py`'s own anti-vacuity
      test, mutation-proved to fail if the card's write stops reaching the
      object (task report has the quoted RED).
    - In Jaeger, search service **`backstage`**, operation
      **`northstar.autonomy_contract.change`**. One span per successful flip
      through the card — not through `kubectl`, which never reaches this
      code path at all. This is the durable half of design §4's auditability
      claim: the object's own `metadata.managedFields` field-manager entry is
      overwritten by the very next write, whichever tool makes it, so it
      is a point-in-time fact rather than a history; this span is what
      survives both the object being reverted and the pod being recycled.
      `tests/e2e/test_golden_path.py::test_the_contract_change_span_lands_in_opensearch`
      asserts it reaches OpenSearch too, the same standard M4-R6 already
      holds `policy.decision` to.

**Known limitations, stated plainly (design §10).** An audience of platform
engineers will notice these; naming them first is better than letting them
assume otherwise:

- **Backstage here is unauthenticated.** The guest login above is real — it
  is a genuine `POST /api/auth/guest/refresh` round trip, not a fake — but it
  asks nothing of the caller. Anyone who can reach port 7007 can open the
  catalog and flip the autonomy contract. That is acceptable for a laptop
  demo cluster reached only through an explicit `kubectl port-forward` — there
  is no Ingress, no LoadBalancer, no node port — and it would not be
  acceptable anywhere else. What keeps this honest is the narrowness of the
  one write the UI can make, not a login: `backstage-autonomy-writer` holds
  `get`/`list`/`patch` on `resourceNames: [northstar-autonomy]` only, no
  `delete`, separate from the read-only ServiceAccount the Kubernetes plugin
  uses. Anyone reaching this port can flip the contract and nothing else —
  and see "The write credential's real cost" in design §10 itself for the
  sharper version of this trade, including the one this project did **not**
  build: a separate Deployment fronting the write, so a Backstage compromise
  yields the ability to *ask* rather than a credential to *keep*.
- **No scaffolder demonstrated.** M1's own claim — "the rest is
  implementation detail; a platform team could add a service in an afternoon
  through the same API" — is only half shown here: the platform API exists
  as a real object with a real UI, but nothing in this demo generates a new
  agent from it. Backstage ships a scaffolder; this milestone does not wire
  a template into it.
- **The catalog is static.** It reflects the `catalog-info.yaml` files
  committed to this repo and staged into the image at build time, not a
  discovered repository — there is no git remote for Backstage's own
  discovery processors to point at (see `docs/upstream-findings.md`
  Finding 15 for the specific mechanism that does not reach a local file).
  A component added to the tree without its own `catalog-info.yaml` does not
  appear here until the image is rebuilt, and `tests/unit/
  test_catalog_matches_the_tree.py` is what catches that drift before a demo
  does.
