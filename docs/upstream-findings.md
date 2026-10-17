# Upstream findings — held locally, not filed

Status: M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic, through **M2.1 (Interposed)** for the two findings the
interposed proxy produced — Findings 6 and 7, both against `agentgateway`
v1.5.0, added by M2.1 Task 5 and checked against commit `27a32df` — and
through **M2.2 (Decided)** for three more, Findings 8, 9 and 10, all against
the same pinned v1.5.0 and all about the `mcpGuardrails`/ExtMCP policy hook
this milestone put in the path. Those three were gathered by M2.2 Task 1 and
independently re-measured by Task 8 before being written here: the four
validator rejections in Finding 8 were re-run against the image, the
`examples/` sweep was done fresh over the whole v1.5.0 tree, and every source
line cited was read at the exact revision the running image reports as its
own.

**M3 (Supervised) adds one finding and one measurement.** The measurement is
OQ-U1 — whether agentgateway v1.5.0's `mcpGuardrails` evaluates every message
inside a batched JSON-RPC array, the one policy-bypass shape M2.2's final
review could not rule out — probed live against the deployed gateway and now
**closed**. It is not a bypass, so it did not become a Finding; see "Open
questions" at the end of this document for the probe, the evidence, and what
it does and does not settle. The finding is **Finding 11**, added 2026-09-11:
`opentelemetry-sdk` 1.44.0 emits a W3C Trace Context Level 2 flag byte that
`go.opentelemetry.io/otel` v1.41.0 rejects outright, so the hop silently
starts a second trace.

**M4 (Observable) adds one more, and it is the first here that is about an
absence rather than a misbehaviour.** **Finding 12**, added 2026-09-13 by M4
Task 2: once a `MeterProvider` existed in the five Python services that host
the four frameworks in the path — **not** in every Python service; see the
correction below this finding's own write-up, where `platform/eval-scorer` is
a sixth with none — exactly
one of the twelve GenAI metrics the conventions define turned out to be emitted
by anything in this system, and three of the four frameworks in the path create
no metric instrument at all. It is also the first finding here whose write-up
quotes an upstream maintainer arguing the *other* way — PydanticAI treats
agent-level telemetry as a span concern deliberately — because a finding that
suppressed the best counter-argument would be advocacy rather than evidence.

**M5 (Progressive autonomy) adds two more, both about the same pair of
packages and found while wiring the second of "two records per evaluation"
Task 4's own title promises.** **Finding 13**, added 2026-09-14 by M5 Task 4:
`openfeature-hooks-opentelemetry` 0.3.1's `TracingHook` puts the
`feature_flag.evaluation` event on the active **span**, citing the OTel
*trace* semantic conventions as its own compliance target — while
`openfeature-sdk` 0.10.0 ships a second, unused event-builder
(`openfeature.telemetry.create_evaluation_event`) built the other way, citing
the *logs* semantic conventions by name in its own docstrings, added to the
SDK core for exactly this purpose over a year before the hook's most recent
release and never once called by it. The two also disagree on the wire
spelling of every attribute they share, and the hook's own error-message key
spells that attribute a third way, off both packages' own spellings.
**Finding 14**, added the
same day: the fields that disagreement's log-shaped side already reads —
`feature_flag.set.id` and `feature_flag.version`, off the flag's own
`flag_metadata` — are two of the seven fields `TracingHook` declares constants
for and never fills in on the span event it actually emits. **Fourteen
findings, still none filed.**

**M6 (Golden path) adds two more, both against Backstage rather than the
telemetry path, and both are a framework assumption meeting this repo's
no-remote, no-Postgres posture rather than a bug in the usual sense — recorded
here anyway because each one cost real delivery time before it was understood.
Nothing is filed for either; both are genuinely working as designed, just
designed around an SCM-backed, multi-tenant deployment this demo deliberately
is not.** **Finding 15**, added 2026-09-15 by M6 Task 2 (this write-up added by
Task 4): a `$text` placeholder cannot inline a local, git-less file in
`@backstage/backend-defaults` — a `type: file` location's target reaches
`PlaceholderProcessor` as a bare filesystem path, `defaultScmResolveUrl`'s
`new URL(value, base)` throws because `base` is not itself an absolute URL,
and even a `file://`-prefixed value would find no reader: `UrlReaders.default()`
registers eleven SCM/cloud-storage factories and one generic http(s) fetcher,
none of them a local `file:` scheme. **Finding 16**, added the same day: the
guest auth provider refuses to issue a token at all outside
`NODE_ENV=development` unless `auth.providers.guest.dangerouslyAllowOutsideDevelopment`
is explicitly set — correct and by design, but its absence meant M6 Task 2
shipped a catalog that built cleanly, collated without error, and passed every
RBAC check, while being unreachable from any browser (ruling M6-R7 in this
project's own SDD ledger). **Sixteen findings, still none filed.**

> **Corrected 2026-09-11, when Finding 11 was written.** This paragraph opened
> *"**M3 (Supervised) adds no finding.** It adds a *measurement*"* and closed
> *"Ten findings, still none filed"*, and both clauses were false by the time
> the milestone ended. M3's final fix round measured the Level 1/Level 2
> interop defect above, fixed it, and recorded it in source comments,
> `docs/architecture.md` and `docs/telemetry-contract.md` — every place except
> this ledger, which is the one the README advertises as where such things go.
> The claim is quoted and dated rather than overwritten, for the reason this
> document keeps giving: a sentence true when written and falsified by a later
> edit, with nobody re-reading it, is this repository's most-repeated defect.
> Finding 11 is also the first entry here found by *this project's own fix
> round* rather than by a task that set out to survey a dependency, which is
> precisely how it came to be written down everywhere but here.

No upstream repository exists for this project yet, and filing
these upstream is a deliberate decision this project defers to its
maintainers rather than one any implementation task makes unilaterally. The
standard held here is the honest one: *we found this, here is the evidence,
we have not filed it* — never an implied or claimed upstream success, and
never a claim that filing has happened when it has not.

Every finding below was re-verified for this document — by reading the
installed package source directly, or by querying the live upstream tracker
(`gh issue view`/`gh pr view` against the real repository, read-only, no
comment or issue was posted anywhere as part of writing this) — not copied
from an earlier draft without checking. Each is written to stand alone: a
reader who has never seen this repository should be able to understand the
problem, the evidence, and the consequence without any of this project's
own history.

## A note on numbering

`docs/telemetry-contract.md`'s "Confirmed convention gaps" section numbers
eight items, 1 through 8 (M1.2 added Gap 7; M4 Task 2 added Gap 8, the
metrics-side counterpart of Finding 12). Two of those are not findings
about a third-party library's behaviour at all — they were open design
questions this project had not yet answered itself, and **as of M3 both are
closed, each in the milestone it said it would be**:

- **Gap 2** ("human-in-the-loop approval spans") — **closed in M3, and closed
  the way it said it would be.** This entry read "deferred to M3, when this
  project has a working implementation to argue from" for the whole of M2, and
  M3 built the implementation. No OTel convention exists for an approval,
  escalation or human-interrupt step inside an agent trajectory — re-checked
  for M3 rather than assumed — so this was never an upstream defect either; it
  was this project's own attribute vocabulary to define. M3 defined it and
  measured it: a `northstar.review.decision` span carrying six `review.*` attributes,
  emitted by this project's own `reviewer-ui`, and a
  `settlement.execute_approved` span carrying two `northstar.*` attributes and
  this project's first span **link**, back to the span that recorded the
  refusal. The measured inventories are in
  [`docs/telemetry-contract.md`](telemetry-contract.md)'s "reviewer-ui's own
  instrumentation: `northstar.review.decision` (M3 Task 4)" and "settlement-agent's own
  instrumentation: `execute_approved` (M3 Task 3)", and the closure argument —
  including what it deliberately does not cover — is at that document's Gap 2.
  It stays listed here for the same reason Gap 3 does: a reader checking this
  document against the contract's **eight** gaps should find all eight
  accounted for.
- **Gap 3** ("policy and autonomy decision spans") — **closed in M2.2, and
  closed the way it said it would be.** No OTel convention exists for a
  policy/autonomy decision span, so this was never an upstream defect; it was
  this project's own attribute vocabulary to define. M2.2 defined it and
  measured it: a `northstar.policy.decision` span carrying ten `policy.*` attributes,
  emitted by this project's own Policy Decision Service, with the keys owned
  by one module (`platform/policy-decision-service/pds/telemetry.py`) rather
  than typed into a document. The measured inventory is in
  [`docs/telemetry-contract.md`](telemetry-contract.md)'s "The policy
  decision, as measured (M2.2)". It stays listed here because a reader
  checking this document against the contract's **eight** gaps should find all
  eight accounted for, and because "we defined our own attributes, under our
  own prefix, and did not pretend they were a convention" is the outcome this
  entry was predicting.

Neither was ever "filing-ready" in the sense the findings below are: there is
no specific library behaviour to point at, no version to cite, no issue number
to check. Both were recorded in the contract as open questions this project
would have to resolve itself, not as upstream defects, and both were resolved
that way — by defining this project's own attributes under this project's own
prefixes and saying so out loud. This document covers
the remaining **six** contract items — Gaps 1, 4, 5, 6, 7 and 8. Gaps 1 and
4–7 are renumbered below as Findings 1–5, in the order a reader would hit them
walking the chain from the A2A hop down to the model's own outbound call; Gap 8
is the metrics-side item M4 Task 2 added, written up as **Finding 12**.
(This paragraph said "five … Gaps 1, 4, 5, 6, and 7" until M4's final review,
and the two sentences above it said "seven gaps" — all three left behind when
Gap 8 was added, while the numbering note at the head of this section was
updated to "eight items" in the same commit. Three restatements of one count,
one of them corrected.)

**Findings 6 through 10 do not come from that list at all**, and the numbering
would mislead a reader who assumed they did. Findings 6 and 7 are M2.1
findings and Findings 8, 9 and 10 are M2.2 findings; all five are against
`agentgateway` v1.5.0, about a component that did not exist in this system
when Gaps 1–7 were enumerated — there was no proxy in the path to have a
finding about, and no policy hook inside it. They continue the numbering
because that is what a reader scanning this document expects, not because they
map onto a contract gap. They are also the only entries here whose subject is
a **Linux Foundation** project rather than an independent Python package —
worth stating because "agentgateway is CNCF" is an easy and wrong assumption,
and this document is the wrong place to launder it.

**One more distinction between the two groups, since it changes how they
should be read.** Findings 6 and 7 are about *telemetry*: what the gateway
names things, and where trace context comes from. Findings 8, 9 and 10 are
about the *policy extension point* — its configuration, its error contract and
its header filter — and none of them is a telemetry gap at all. Finding 10 is
the one that touches both, and it is worth reading in that light: a header
filter defaulting one way and documented accurately still cost this project
its trace across the policy hop, because the first sensible edit to it is the
one that breaks tracing.

**Finding 11 sits outside both groups, and is the only entry here that is not
about a single dependency.** It is an M3 finding, added 2026-09-11, and its
subject is an *interop boundary* between two projects that are each correct on
their own: OpenTelemetry **Python** (`opentelemetry-sdk` 1.44.0) and
OpenTelemetry **Go** (`go.opentelemetry.io/otel` v1.41.0), one release apart
across W3C Trace Context's Level 1/Level 2 line. Both are **CNCF** projects,
which makes Finding 11 the only entry here with a CNCF subject — Findings 1–5
are independent Python packages and Findings 6–10 are Linux Foundation, so
each of the three groups sits under a different roof, and none of them should
be assumed into another. It is also the only finding
this project found by *fixing its own system* rather than by surveying a
dependency, and the only one whose evidence includes a live A/B control: the
same request, twice, differing in one hex digit.

**Finding 12 is the metrics-side counterpart of Gap 8**, as the numbering note
above already says, and is the only finding after Finding 5 that *does* map
onto one of the contract's eight gaps. **Findings 13 and 14 sit outside the
gap list entirely, the same way Findings 6–11 do** — they are M5 findings,
both added 2026-09-14 by M5 Task 4, and their subject is a third roof: the
`open-feature` GitHub organization's own Python packages
(`openfeature-hooks-opentelemetry` and `openfeature-sdk`), an **independent
project**, not CNCF (Finding 11's roof) and not Linux Foundation (Findings
6–10's). Unlike every finding before them, both are about the two packages
**disagreeing with each other**, not about either one disagreeing with a
convention on its own — Finding 13 is the disagreement itself (which OTel
signal, which attribute spelling); Finding 14 is a consequence of it (the
package that already reads two fields correctly is the one nothing calls).

**There is also an "Open questions" section at the end of this document**, added
in the M2.2 fix round (ruling R46). Entries there are deliberately *outside* the
finding numbering: they name a behaviour nobody has demonstrated, record what
was checked and what would settle it, and become findings only if a measurement
turns them into one.

The evidence for Findings
1–4 was originally gathered as A8 upstream-contribution material and lives,
in full, in `docs/telemetry-contract.md` and the per-component `NOTES.md`
files (`agents/claims-agent/NOTES.md`, `agents/settlement-agent/NOTES.md`,
`tools/payments-mcp/NOTES.md`); Finding 5's evidence was gathered in M1.2
Task 4 and re-verified, including a fresh tracker search, for this document —
this document consolidates and re-verifies it into one filing-ready list; it
does not re-derive it.

**Finding 5 was never filing-ready the way Findings 1–4 are, and that was
itself the finding.** It was first suspected as a genuine upstream gap (a
library not supporting a newer HTTP client). A tracker search showed the
opposite: the fix was already merged and already shipped in the exact
library version this project has installed. What remained was a one-line
change in this project's own code — and M1.2 Task 8 made that change,
closing Gap 7. It stays in this document anyway, in the same format as the
other four, because "we searched and there was nothing to file because it
was already fixed" is exactly the kind of claim this document exists to
state precisely rather than assume — a closed finding is not a finding worth
deleting.

---

## Finding 1: `a2a-sdk` 0.3.7 does not extract inbound trace context, and its spans carry no identity attributes

**The problem, plainly:** A2A (Agent-to-Agent) is a protocol whose entire
purpose is one agent delegating to another. `a2a-sdk` — the reference Python
implementation — ships an OpenTelemetry `telemetry` extra that instruments
its own internal call graph, but that instrumentation does two things wrong
for a protocol built around cross-process delegation: it never reads an
inbound `traceparent` header, so every request becomes a disconnected root
trace regardless of what the caller sent; and every span it produces, on
both the server and client side, carries no attribute of any kind beyond
OTel's own internal scope bookkeeping — no operation name, no conversation
or task identifier, not even the agent's own name. A trace that cannot join
the caller's trace, and that carries no identity information once joined, is
close to useless for the one thing A2A tracing would need to do.

**Project and version:** `a2a-sdk[telemetry]==0.3.7` (PyPI:
`a2a-sdk`), the reference Python A2A SDK maintained under the `a2aproject`
GitHub organization.

**Where the behaviour lives:** No code path in `a2a/server/apps/jsonrpc/*.py`
reads a `traceparent` header at all — confirmed by grepping the installed
package tree for `propagate`/`traceparent`/`extract`/`TextMapPropagator`
(zero matches). The spans themselves come from `@trace_class`/
`@trace_function` decorators (`a2a/utils/telemetry.py`) applied to
`DefaultRequestHandler` and `JSONRPCHandler` (server side,
`a2a/server/request_handlers/`) and to `JsonRpcTransport`,
and the `grpc`/`rest` transport equivalents (client side,
`a2a/client/transports/`) — all three transports carry the identical
decorator, confirmed by `grep -rn "trace_class\|trace_function"` over the
installed package.

**What the convention says should happen:** the GenAI semantic conventions'
attribute registry (`docs/registry/attributes/gen-ai.md`,
`open-telemetry/semantic-conventions-genai`, verified directly) states
instrumentations SHOULD populate `gen_ai.conversation.id` "when they have an
identifier for the conversation readily available," and, symmetrically,
SHOULD NOT fall back to "a new UUID, a trace identifier, or a hash of
request content" when no such identifier exists. A2A supplies exactly such
an identifier already, in its own `contextId` and `taskId` fields — the
convention's own preferred case, not a stretch.

**What we observed instead:** Verified twice — once with
`starlette.testclient.TestClient` against an in-process app, and once
against a real deployed pod through its Kubernetes Service. In both cases, a
hand-minted `traceparent` header sent on the request has no relationship at
all to the trace ID the resulting span reports; the entry span's `parent_id`
reads `null` even though a valid parent span ID was sent. On the attribute
side: capturing a real `message/send` call end to end and reading every
`a2a.server.*`/`a2a.client.*` span's full attribute set back from Jaeger
returns, verbatim, only `{internal.span.format, otel.scope.name,
otel.scope.version, otel.status_code, span.kind}` — the same five
bookkeeping keys on the root span and on the innermost helper method alike.
No `gen_ai.operation.name`, no `gen_ai.conversation.id`, no `contextId`, no
`taskId`, no agent name, on any span, on either side of the connection.

**Why it matters, beyond this project:** Anyone instrumenting an A2A-based
system today gets a trace per hop that cannot be stitched into the caller's
trace by the SDK itself, and — even once stitched by some other means — a
trace with no way to tell, from the span data alone, which conversation,
task, or agent produced it. For a protocol whose entire value proposition is
multi-agent delegation, this means the SDK's own telemetry cannot answer
"which agent did what, as part of which conversation," which is the
question anyone building on it will eventually ask.

**What a fix would need to do:** On the server side, the `A2AStarletteApplication`
(or whichever transport app) needs to extract inbound trace-context headers
before entering its own request-handling call graph — the natural place is
wherever the ASGI app is constructed, so that framework-level extraction
happens once, ahead of every `@trace_class`-decorated call. On the attribute
side, both the request handler and the client transports would need to set
`gen_ai.operation.name` (`invoke_agent` per the GenAI convention) and
`gen_ai.conversation.id` (populated from A2A's own `contextId`) on their
entry/exit spans.

**How we worked around it, and whether it should be adopted upstream:** We
fixed only the extraction half, and did it with real framework
instrumentation, not a patch: `opentelemetry-instrumentation-starlette`,
applied to the built Starlette app via `.instrument_app()` (since the app
object doesn't exist until `A2AStarletteApplication(...).build()` returns).
This is a legitimate technique any `a2a-sdk` user could adopt today without
waiting for an upstream fix — it works because `A2AStarletteApplication`
returns a plain `starlette.applications.Starlette` instance underneath — but
it is a workaround for the SDK's own gap, not a substitute for the SDK doing
this itself; a general-purpose SDK shouldn't require every integrator to
independently discover that ASGI instrumentation is the fix for its own
missing extraction. The attribute half (`gen_ai.operation.name`,
`gen_ai.conversation.id`, agent/task identity) is untouched — we did not
invent a bespoke `a2a.*`/`gen_ai.*` attribute to fill it, on the principle
that a convention gap should be reported, not papered over with a
project-specific name nobody else would recognize.

**Existing upstream activity (verified live against the trackers, read-only,
2026-09-08):**

- [`open-telemetry/semantic-conventions-genai#70`](https://github.com/open-telemetry/semantic-conventions-genai/issues/70)
  ("Add semantic conventions for A2A (Agent-to-Agent) protocol") — **open**,
  filed by a third party (`xiaocang`). Proposes reusing
  `gen_ai.conversation.id` for A2A's `contextId` and `gen_ai.operation.name`
  for the A2A operation — the exact position taken above. This is one of
  several related open proposals in the same repository:
  [`#254`](https://github.com/open-telemetry/semantic-conventions-genai/issues/254)
  ("Semantic Conventions for Agent-to-Agent (A2A) Protocol Telemetry",
  open, `pwkowalski`),
  [`#195`](https://github.com/open-telemetry/semantic-conventions-genai/issues/195)
  ("semconv for a2a protocol", open, `eternalcuriouslearner`), and
  [`#447`](https://github.com/open-telemetry/semantic-conventions-genai/issues/447)
  ("Unified OpenTelemetry model for tracking agent-to-agent interactions",
  open, `nikhilNava`) — four independent proposals from four different
  people, all still open, all arguing for A2A identity to be representable
  in GenAI semantic conventions. Independent third-party interest in exactly
  this gap, from four separate authors, is stronger evidence the gap is real
  than if only this project had noticed it — and it means a new issue on the
  semconv side specifically would be low-value: it would duplicate existing,
  already-argued proposals rather than add anything.
- On the extraction side specifically (the SDK not reading `traceparent` at
  all): `a2aproject/a2a-python` has two related but **closed** issues —
  [`#168`](https://github.com/a2aproject/a2a-python/issues/168)
  ("A2AClient should propagate trace context?", closed) and
  [`#281`](https://github.com/a2aproject/a2a-python/issues/281)
  ("EventQueue should propagate trace context?", closed) — verified live;
  neither addresses the specific gap found here, that
  `A2AStarletteApplication` extracts nothing at the ASGI/HTTP boundary by
  default. No open issue was found covering that exact point.

**Status: not filed by this project.** The semconv-side half is already
well covered by four independent open proposals and would gain nothing from
a fifth. The `a2a-python` extraction gap and the total-absence-of-attributes
finding are not covered by any open issue found on that exact point, and
remain filing-ready — installed version, exact behaviour, and the capture
evidence above — whenever the decision to file is made.

**Re-checked against `a2a-sdk` 1.1.2, 2026-09-11 (M3 remaining-bumps, step 3).
Both halves of this finding survive the major version. Nothing here is
closeable.** The check was made because a 1.x that fixed inbound extraction
would have let this project delete the `StarletteInstrumentor` workaround
(amendment A18) — a real event for this repository, not a version number. It
did not, so the workaround stays and this finding stays open.

The evidence is the same two greps this finding was originally written from,
re-run over the installed `a2a-sdk[telemetry,http-server]==1.1.2` package tree:

- **Inbound trace-context extraction: still absent.** `propagate` → 0 hits,
  `traceparent` → 0 hits, `TextMapPropagator` → 0 hits across every `.py` in
  the package. `extract` returns 19 hits and not one is trace context: they are
  `_extract_task_id`/`_extract_task_and_config_id` in the v0.3 gRPC compat
  handler, and the `attribute_extractor` parameter of `a2a/utils/telemetry.py`.
  So a 1.1.2 server still starts a disconnected root trace for every inbound
  request unless something else extracts the header first.
- **Identity attributes on its spans: still none.** Every instrumented class
  is decorated `@trace_class(kind=SpanKind.SERVER)` or
  `@trace_class(kind=SpanKind.CLIENT)` with **no** `attributes=` argument, in
  all 17 decorated modules — and `attribute_extractor=` (which exists in
  0.3.7 too, 12 mentions) has **zero** call sites outside `telemetry.py` in
  either version. The span body only sets attributes `if attributes:`, so the
  attribute set is empty for the same reason it was at 0.3.7.

What *did* change at 1.x is everything around it, which is why this project is
still on 0.3.7 and why the re-check stopped at reading the package rather than
deploying it. `a2a-sdk` 1.x implements **A2A protocol v1.0**, with v0.3 behind
an opt-in compatibility layer (`a2a/compat/v0_3/`, and
`create_jsonrpc_routes(..., enable_v0_3_compat=False)` by default). Measured,
not inferred from a changelog:

- **The JSON-RPC method names changed.** `jsonrpc_dispatcher.py` dispatches
  `'SendMessage'` / `'SendStreamingMessage'` / `'SubscribeToTask'`; 0.3.7's
  app dispatches `message/send` / `tasks/get` / `tasks/cancel`. This repo sends
  raw JSON-RPC `message/send` from **13 files across three languages** —
  production Python (`agents/claims-agent/delegation.py`,
  `apps/reviewer-ui/approval.py`), production TypeScript
  (`apps/customer-ui/src/a2a.ts`) and nine test files.
- **The agent-card format changed.** `a2a.types.AgentCard` is no longer a
  pydantic model; it is a protobuf message (`lf.a2a.v1.AgentCard`), and
  `AgentCard.model_validate()` does not exist. Parsing this repo's own
  committed card through it fails outright: `ParseError: Message type
  "lf.a2a.v1.AgentCard" has no field named "protocolVersion"`. `url` and
  `preferredTransport` are gone too, replaced by `supportedInterfaces`. That
  card is `contracts/agent-cards/*.json`, this project's declared single source
  of truth, and two e2e tests diff the served document against it whole.
- **The server API changed.** `a2a.server.apps.A2AStarletteApplication` — the
  class both agents' `main.py` builds their app with, and the object A18's
  `StarletteInstrumentor().instrument_app(app)` is attached to — **does not
  exist**; the module `a2a.server.apps` is gone. Apps are assembled from
  `a2a.server.routes.create_jsonrpc_routes(...)` /
  `create_agent_card_routes(...)`. `DefaultRequestHandler` gained a required
  `agent_card` argument, and `a2a.utils.new_agent_text_message` — used by both
  executors — is no longer exported from `a2a.utils`.

That is a wire-shape change, a contract-document change and a server-API change
at once: a milestone, not a bump. It is recorded here rather than attempted,
and `a2a-sdk[telemetry]==0.3.7` stays pinned in both agents.

**One more fact worth pinning, because it will look like an error later:** the
newest `a2a-sdk` **on PyPI** is 1.1.2 (2026-07-22). `a2aproject/a2a-python` has
published GitHub releases v1.1.3 (2026-08-18) and v1.1.4 (2026-09-08), and
neither is on PyPI — `https://pypi.org/pypi/a2a-sdk/1.1.3/json` and `/1.1.4/json`
both return 404. So "latest" for anything installable is 1.1.2, and a reader
comparing the GitHub releases page against this document has not caught a stale
pin.

---

## Finding 2: FastMCP 4.0.3 never emits `gen_ai.operation.name` on tool-call spans

**The problem, plainly:** When an MCP tool is called, the OTel GenAI/MCP
semantic conventions expect the resulting span to record what kind of GenAI
operation happened via `gen_ai.operation.name`, with `execute_tool` as the
value for a tool invocation. FastMCP's tool-call spans carry the sibling
attribute correctly — `gen_ai.tool.name` — but never the operation
classifier itself.

**Project and version:** `fastmcp==4.0.3` (PyPI: `fastmcp`, the
`jlowin`/`PrefectHQ`-maintained MCP server framework).

**Where the behaviour lives:** FastMCP's own span-creation code
(`fastmcp/telemetry.py` in the installed package) sets `gen_ai.tool.name`
when building a `tools/call` span but never sets `gen_ai.operation.name`
anywhere in that path.

**What the convention says should happen:** the GenAI semantic conventions
specify `gen_ai.operation.name = execute_tool` for a tool-execution span,
alongside `gen_ai.tool.name` identifying which tool.

**What we observed instead:** capturing a real `tools/call
propose_payment`/`tools/call execute_payment` invocation end to end and
reading the span's full attribute set back from Jaeger:

```
fastmcp.component.key   = tool:execute_payment@
fastmcp.component.type  = tool
fastmcp.provider.type   = LocalProvider
fastmcp.server.name     = payments-mcp
fastmcp.span.seam       = True
gen_ai.tool.name        = execute_payment
internal.span.format    = otlp
mcp.method.name         = tools/call
mcp.protocol.version    = 2025-03-26
mcp.session.id          = 962209c96d0340c39fd4b259641807f3
otel.scope.name         = fastmcp
span.kind               = server
```

`gen_ai.tool.name` is present and correct; `gen_ai.operation.name` never
appears, on either tool, in any capture taken.

**Why it matters, beyond this project:** anyone trying to build a
cross-framework view of "which GenAI operations happened where" — the whole
point of the GenAI semantic conventions existing — cannot filter or group
FastMCP's tool-call spans by operation type the way the convention intends,
even though the sibling attribute (`gen_ai.tool.name`) that identifies
*which* tool is already correct. This reads as a half-migrated convention
surface: one attribute from the same namespace landed, the other did not.

**What a fix would need to do:** set `gen_ai.operation.name = execute_tool`
alongside the existing `gen_ai.tool.name` assignment in the same code path.

**How we worked around it, and whether it should be adopted upstream:** we
did not add a bespoke `gen_ai.operation.name` to our own `server.py` to fill
the gap while waiting — doing so would mean this project's telemetry
disagrees with what a future, fixed FastMCP would emit natively, which is
exactly the kind of invented-convention risk this project's governing rule
exists to avoid. There is no workaround to recommend upstream adopt; the
correct fix is simply for FastMCP to set the attribute itself.

**Existing upstream activity (verified live, 2026-09-08):**
[`PrefectHQ/fastmcp#3534`](https://github.com/PrefectHQ/fastmcp/issues/3534)
("Support GenAI semantic convention in open telemetry") — **open**, filed by
a third party (`CaselIT`), and it asks for exactly this:
`gen_ai.operation.name = execute_tool` on these spans. It also asks for
`gen_ai.tool.name`, which the installed 4.0.3 already emits correctly —
the issue's own text predates, or was filed unaware of, that half already
being fixed. This is independent third-party confirmation of the same gap
this project found by direct measurement.

**Status: not filed by this project.** The gap is already upstream, filed
by someone else, open, and precisely on point — filing a second issue would
add nothing. No workaround was added to this project's own code to
compensate in the meantime.

---

## Finding 3: FastMCP 4.0.3 never reads the `baggage` key out of JSON-RPC `params._meta`

**The problem, plainly:** MCP has no HTTP layer of its own to carry trace
context in headers the way a plain HTTP call would — everything rides
inside the JSON-RPC envelope, in an unprefixed `_meta` object. SEP-414 (the
MCP enhancement proposal covering this) documents three keys that belong
there: `traceparent`, `tracestate`, and `baggage`. FastMCP's server-side
extraction reads the first two and silently ignores the third. The result is
that a trace correctly joins across an MCP boundary — the parent/child span
relationship is intact — but any OTel Baggage value the caller was carrying
(a claim ID, a tenant ID, anything set via `baggage.set_baggage`) is dropped
at exactly this boundary, invisibly: nothing errors, nothing warns, the
value is simply gone on the other side.

**Project and version:** `fastmcp==4.0.3`, same package as Finding 2.

**Where the behaviour lives:** `fastmcp/telemetry.py`'s
`extract_trace_context()` function builds a carrier dict by copying only two
specific keys out of the incoming `_meta` object:

```python
carrier: dict[str, str] = {}
if TRACE_PARENT_KEY in meta:
    carrier["traceparent"] = str(meta[TRACE_PARENT_KEY])
if TRACE_STATE_KEY in meta:
    carrier["tracestate"] = str(meta[TRACE_STATE_KEY])
if carrier:
    return propagate.extract(carrier, context=otel_context.get_current())
```

`baggage` never appears as a key checked or copied. The symmetric outbound
function, `inject_trace_context()` (used when FastMCP itself acts as an MCP
*client*), has the identical omission — it populates only `traceparent`/
`tracestate` in its own outbound `_meta`, never `baggage`. A repository-wide
`grep -rn baggage` over the installed `fastmcp` package tree returns exactly
one match, in a code comment about preserving already-active baggage on
extract — not a `_meta` key read or write anywhere.

**What the convention says should happen:** SEP-414 documents `baggage` as
one of the three unprefixed `_meta` keys MCP trace-context propagation
carries, alongside `traceparent` and `tracestate` — not an optional extra,
the same tier as the other two.

**What we observed instead:** a client (this project's own settlement-agent)
was made to inject a `baggage` key into `params._meta` on a real `tools/call
execute_payment` invocation, alongside a correctly-propagating `traceparent`.
Captured against the real deployed FastMCP server: the resulting span is
correctly parented into the caller's trace (the `traceparent` half works
exactly as documented) but carries **zero** occurrences of the baggage value
that was sent, on that span or on the server's own outbound call further
downstream — confirming the value was never read into the process's ambient
OTel context in the first place, not merely dropped somewhere later.

**This finding therefore rests on live observation, not source reading and
`grep` alone**, and both halves of that observation are at versions this
project actually has installed: the server is `fastmcp==4.0.3`
(`tools/payments-mcp/requirements.txt:1`, and confirmed by the
`.dist-info` directory name in that service's own `.venv`); the client is
this project's own settlement-agent. **That pin is now `mcp==2.2.0`**
(`agents/settlement-agent/requirements.txt`, cited by name rather than by
line); this sentence still read `mcp==1.9.4` at line 13 -- a version two majors
behind and a line holding a comment -- until M4's final review. The finding
below is written against 1.9.4 and is left numbered and worded as it was
filed-ready; what changed is this project's pin, not the finding's subject. Two different, and
differently-weighted, tests are cited here — kept distinct rather than
merged, since they demonstrate different things:

- The live capture against the real deployed server is
  `tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`:
  it drives a real HTTP request through the real deployed chain
  (`claims-agent` → `settlement-agent` → `payments-mcp` → `payments-api`)
  and polls real Jaeger for the resulting trace, then asserts
  `northstar.claim.id` is present on every `settlement-agent` span and
  absent from every `payments-mcp`/`payments-api` span. This is the test
  `docs/telemetry-contract.md`'s Gap 4 cites ("`northstar.claim.id` reaches
  payments-mcp on **0** of its tool-call spans... in a live capture of the
  full chain"). The finding originates in the **M1.1** milestone's Task 7,
  recorded in that milestone's SDD workspace — a git-ignored working
  artifact, so treat this as provenance rather than a document you can
  open; the runnable test in this repository is the evidence. (Note the
  numbering collision: M1.2 has its own, unrelated Task 7.) It predates
  M1.2's Task 6, but it is a genuine live observation at this project's
  real, installed versions, not a prediction from reading source alone.
- `agents/settlement-agent/tests/test_mcp_tools.py::test_call_injects_params_meta_baggage_from_the_ambient_context`
  is a different kind of evidence and is **not** a capture against any
  deployed service: it monkeypatches both `mcp_tools.streamablehttp_client`
  and `mcp_tools.ClientSession` with in-process fakes
  (`_FakeReadWriteContext`, `_FakeSessionCapturingRequest`) that never open
  a socket — `send_request` simply stores the outgoing request object in a
  class dict and returns a canned result, no bytes cross a process
  boundary. It is useful evidence of a narrower, but still real, claim:
  that this project's own production client code
  (`agents/settlement-agent/mcp_tools.py`) genuinely constructs a `baggage`
  key in `params._meta` before ever reaching the wire — confirming the
  *client's* half of the gap is not the missing half. It observes nothing
  about the deployed `payments-mcp` server, which is what the live capture
  above is for.

**Note on a withdrawn evidence-upgrade plan.** A later planning step (M1.2
Task 6, Step 3) proposed re-observing this same gap "at
`fastmcp-slim==4.0.3` with `mcp==2.2.0`," with a tool handler seeing an
`ambient_baggage_northstar_claim_id: None` attribute alongside `baggage` on
the wire. **That specific claimed observation was never made and could not
have been:** `ambient_baggage_northstar_claim_id` does not appear anywhere
in any installed package in this project, in any of this project's own
code, or in any test — a repo-wide search finds that string in exactly one
place, the withdrawn plan text itself. (One nuance worth recording precisely
rather than waved away: `fastmcp-slim==4.0.3` and a close-but-not-identical
`mcp==2.1.1` *do* exist as real, imported, transitive dependencies inside
`payments-mcp`'s own `.venv` — `fastmcp==4.0.3`'s own package metadata
requires `fastmcp-slim[client,server]==4.0.3`, whose `[server]` extra in
turn requires `mcp<3.0.0,>=2.0.0`, which this venv resolved to `mcp==2.1.1`,
genuinely imported by `fastmcp/server/sessions.py` and
`fastmcp/server/low_level.py`, not merely present and unused. So the
package-name coincidence with settlement-agent's unused PydanticAI `[mcp]`
extra is real but beside the point: nothing in this project's own code
drives that transitive `mcp` copy through the scenario the withdrawn step
describes, and the specific attribute name it names was never observed
anywhere, at any version.) That evidence-upgrade step was withdrawn before
implementation, as an observation that cannot be traced to anything this
project has measured — recorded here so a future reader does not go looking
for a measurement that was never taken.

**Why it matters, beyond this project:** OTel Baggage is the standard
mechanism for carrying an application-defined identifier (a tenant ID, a
user ID, a request-scoped correlation key — anything) across process
boundaries without threading it through every function signature by hand.
Anyone building an MCP server on FastMCP who relies on baggage for
correlation — which SEP-414 explicitly anticipates as a supported pattern —
will find it silently vanishes the moment a call crosses into their FastMCP
process, with no error and no indication anything was dropped. This is the
kind of gap that is invisible until someone specifically checks for the
attribute downstream and finds it missing, exactly as this project did.

**What a fix would need to do:** add a `baggage` key, read and write, to
both `extract_trace_context()` and `inject_trace_context()`, symmetric with
how each already handles `traceparent`/`tracestate` — mechanically the same
shape of change in two functions in one file.

**How we worked around it, and whether it should be adopted upstream:** we
did not work around it. No code in this repository reaches into FastMCP's
own `_meta` handling to bridge this gap by hand — doing so would mean either
patching third-party behaviour at runtime or inventing a parallel
correlation mechanism outside the standard propagation APIs this project
relies on everywhere else, both of which this project's governing rule
rules out. The consequence is accepted and documented instead: trace-ID
correlation stays intact end to end; the domain attribute
(`northstar.claim.id`, carried in baggage) does not cross this one boundary,
and this project's own tests assert that precise, narrow boundary rather
than silently working around it or asserting a broader "everything
propagates" claim that would be false.

**Existing upstream activity (verified live, 2026-09-08):**
[`PrefectHQ/fastmcp#3998`](https://github.com/PrefectHQ/fastmcp/issues/3998)
("OpenTelemetry: FastMCP client does not propagate trace context on
initialize and list methods") — **open**, filed by a third party
(`strawgate`). Its own body states the server "does not receive
`traceparent` / `baggage` through MCP `_meta`" and asks that FastMCP's
*client* propagate `_meta.traceparent` and `_meta.baggage` on
`initialize`/`list` calls specifically. This is genuinely independent
third-party corroboration that a `baggage`-through-`_meta` gap exists in
this package — but its premise is narrower than, and does not fully cover,
this project's own finding: `#3998` assumes `tools/call` (the method this
project's own chain actually uses) already propagates baggage correctly, and
is only asking about `initialize`/`list`. Our own live capture shows that
premise does not hold for the `baggage` key specifically, on `tools/call`
either — the server-side omission is total, across every method, not
limited to `initialize`/`list`. Separately,
[`PrefectHQ/fastmcp#4358`](https://github.com/PrefectHQ/fastmcp/issues/4358)
("Incoming W3C traceparent header not propagated to FastMCP's internal
spans") — **open**, filed by a third party (`reachrazamair`) — is on the
same file's trace-context handling but does not mention `baggage` anywhere;
a related but distinct complaint. No open issue was found reporting that the
FastMCP **server** never reads `baggage` out of `_meta` at all, across every
method, which is this project's precise finding. The MCP spec repository
also has a related but distinct open proposal,
[`modelcontextprotocol/modelcontextprotocol#2028`](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2028)
("SEP-2028: Automatic `_meta` to HTTP header forwarding for distributed
tracing", open) — about forwarding `_meta` context (including `baggage`) to
downstream HTTP calls a server makes, not about a server reading `baggage`
out of an inbound `_meta` into its own OTel context in the first place;
adjacent motivation, a different gap.

**Status: not filed by this project.** Unlike Finding 2, filing here would
not be low-value if the decision were made: `#3998`'s corroboration covers
only part of this finding (client-side, `initialize`/`list` methods, an
assumption that `tools/call` already works), and no open issue covers the
server-side, all-methods omission this project found. It is recorded here
complete and filing-ready — installed version, exact file and function, what
is read versus not, and precisely how it differs from `#3998`'s and
`#4358`'s adjacent territory — for whenever that decision is made.

---

## Finding 4: `mcp==1.9.4`'s high-level client has no way to attach `params._meta` to a tool call

**The problem, plainly:** MCP's own wire format supports attaching an
arbitrary `_meta` object to a tool-call request — the mechanism Finding 3's
`baggage`/`traceparent` propagation depends on existing at all. The standard
Python MCP client's convenience method for calling a tool,
`ClientSession.call_tool()`, has no parameter for setting it: there is
simply no supported route from the ordinary client API to the wire feature
that trace-context propagation needs.

**Project and version:** `mcp==1.9.4` (PyPI: `mcp`, the official
Model Context Protocol Python SDK, maintained under the
`modelcontextprotocol` GitHub organization) — the client package, distinct
from FastMCP (Findings 2–3), which is a separate project implementing the
server side.

**Where the behaviour lives:** `mcp/client/session.py`'s
`ClientSession.call_tool(name, arguments, read_timeout_seconds,
progress_callback)` — verified by reading the installed package's actual
signature directly — has no `_meta` or `meta` parameter of any kind, and
internally always constructs `CallToolRequestParams(name=name,
arguments=arguments)` with no way to populate `params._meta`. The type that
*does* support it, `mcp.types.CallToolRequestParams`, has a `meta` field
aliased to the wire key `_meta` and backed by `RequestParams.Meta` (which
accepts arbitrary extra keys) — the capability exists in the type system,
just not in the convenience method's parameter list. The only way to attach
`_meta` on this version is to hand-build a `CallToolRequestParams(...,
_meta=types.RequestParams.Meta(**carrier))` and call the lower-level
`ClientSession.send_request(ClientRequest(CallToolRequest(...)),
CallToolResult)` instead — duplicating `call_tool()`'s own
name/arguments/result-type/timeout/progress-callback plumbing by hand.

**What the convention/spec says should happen:** SEP-414 (the same MCP
proposal Finding 3 cites) expects a client to be able to attach
`traceparent`/`tracestate`/`baggage` to a tool call's `_meta`. A client SDK
implementing that proposal needs a supported way to set `_meta` on an
ordinary tool call — not only on hand-built lower-level requests.

**What we observed instead:** reading `mcp/client/session.py` directly
confirms the signature has no such parameter; using the client for a real
tool call the ordinary way provides no route to set `_meta` at all. The
project's own client code (`agents/settlement-agent/mcp_tools.py`) and its
own e2e test infrastructure (`tests/e2e/test_payments_mcp_trace.py`) both
independently arrived at the identical workaround — hand-building the
request and calling `send_request()` directly — because there was no other
way to exercise this feature at all.

**Why it matters, beyond this project:** any client author who wants
SEP-414-style trace-context propagation on an ordinary tool call, on this
SDK version, must always drop to the low-level request API and re-implement
`call_tool()`'s own plumbing rather than simply adding a keyword argument to
an existing call. That is a meaningfully higher bar than the feature ought
to require, and it means most integrators are unlikely to discover or use
this propagation path at all unless they specifically go looking for it, the
way this project did.

**What a fix would need to do:** add an optional `meta: dict | None`
parameter to `ClientSession.call_tool()`, threaded into the
`CallToolRequestParams` it already builds internally — mirroring what the
underlying type already supports, with no new capability needed at the
protocol level, only at the convenience-wrapper level.

**How we worked around it, and whether it should be adopted upstream:** we
used the lower-level `send_request()` technique described above — this is a
workaround for a missing convenience method, not a hack around broken
behaviour, and it is exactly the technique a future, fixed `call_tool(meta=
...)` would replace. It is reasonable for any current `mcp==1.9.4` (or
earlier) integrator to adopt the same technique until they can upgrade, but
it is not something we'd recommend upstream *keep* — the whole point of the
fix below is to make this unnecessary.

**Existing upstream activity (verified live, 2026-09-08):**
[`modelcontextprotocol/python-sdk#1216`](https://github.com/modelcontextprotocol/python-sdk/issues/1216)
("Expose `_meta` in `ClientSession` methods") — filed by a third party
(`samchenatti`) — proposed exactly this: an optional `meta: dict | None`
parameter on `call_tool()`. **It is already resolved**: the issue is closed,
and [`#1231`](https://github.com/modelcontextprotocol/python-sdk/pull/1231)
("Expose `RequestParams._meta` in `ClientSession.call_tool`"), by the same
author, merged 2025-10-17 — verified live via the PR's own `mergedAt`
timestamp. That fix landed in an `mcp` release newer than `1.9.4`, the
version pinned in this project (`tests/requirements.txt`,
`agents/settlement-agent/requirements.txt`) — so the gap this project found
independently was real for the version actually running here, and is
already fixed for anyone on a current `mcp` release.

**Status: not filed by this project** — and there is nothing to file: the
fix already exists upstream and merged before this document was written. No
workaround was added to route around the merged fix; the `send_request()`
technique in `mcp_tools.py` remains the correct approach for the version
this project is actually pinned to. Bumping the `mcp` pin to pick up
`#1231`'s simpler `call_tool(meta=...)` interface is a real, available
simplification, deliberately left out of scope here — verifying a new `mcp`
version against the rest of this project's pin set is its own piece of work
with its own re-verification obligations, not something to fold unexplained
into a documentation pass.

**Update, Task 7 (M1.2, dependency-advisory remediation):** the `mcp` pin
this project actually runs moved from `1.9.4` to `1.28.1` — verified
directly against the installed `1.28.1` distribution's own
`ClientSession.call_tool()` signature, which now carries the `meta` keyword
parameter `#1231` added. The simplification described above is therefore no
longer hypothetical: `mcp_tools.py` could today call
`session.call_tool(tool, arguments, meta=carrier)` instead of hand-building
a `CallToolRequest` and calling `send_request()` directly, and the same is
true of `tests/e2e/test_payments_mcp_trace.py`'s own use of the identical
technique. **This task deliberately did not make that change.** The bump's
own isolation requirement — proving the *existing*, already-verified
`send_request` path still propagates correctly at the new `mcp` version,
with nothing else moving in the same deploy — is exactly what would be lost
by also switching call paths in the same change; `tests/e2e/
test_payments_mcp_trace.py`'s full pass at `1.28.1`, including with a
deliberately broken `_meta` key proven to fail first, is evidence for the
path this project actually runs, not for the simpler path now available.
Adopting `call_tool(meta=...)` remains recorded, available work for a future
task with its own re-verification obligation, not decided here.

**Update, 2026-09-11 (M3 remaining-bumps, step 1): `mcp` is now 2.2.0, and
this finding has gone past "fixed" to "obsolete".** At 2.2.0 the client does
not need to be asked to attach trace context at all — the JSON-RPC dispatcher
injects it into `params._meta` on every outbound request, unconditionally,
with a source comment naming SEP-414 by number:

```python
# mcp/shared/jsonrpc_dispatcher.py, read from the installed 2.2.0
with otel_span(span_name, kind=SpanKind.CLIENT, attributes={...}):
    # SEP-414: inject W3C trace context; `_meta` stays on the wire even with a no-op tracer.
    inject_trace_context(out_meta)
```

Measured live rather than read off the changelog: that injection is what
parents payments-mcp's server span under the SDK's own new `MCP send
tools/call <tool>` CLIENT span (trace `e086b1efd1ae4fba819455c5d9f5fa75`; the
span inventory and full parent chain are in `docs/telemetry-contract.md`'s
"The `mcp` client's own telemetry, as measured"). `mcp` 1.28.1, by contrast,
ships no OpenTelemetry code whatsoever — `grep -rl opentelemetry` over its
installed package tree returns nothing.

So the two halves of "available future work" now have different answers.
Adopting `call_tool(meta=...)` is still available and still not taken. But the
*reason* to hand-build `_meta` at all is gone: `mcp_tools.py`'s injection is
now redundant, overwritten by the SDK's own with a strictly deeper parent in
the same trace. It was **left in place** in this step, on purpose — removing
production code that carries trace context is a behavioural change with its
own proof obligation, and this step's obligation was to prove the bump alone
did not break the chain. Recorded so the next reader finds a decision rather
than dead code nobody noticed.

---

## Finding 5 (closed, Task 8): the model's own outbound HTTP hop had no CLIENT span — and, unlike Findings 1–4, the fix was already shipped upstream

**The problem, plainly:** the settlement agent's calls to the Anthropic model
travel over a distribution named `httpx2`, not the `httpx` this project
instruments everywhere else. The standard OpenTelemetry HTTP instrumentation
this project applies covers only `httpx`. The consequence is that one entire
HTTP hop in the trace — the model call itself — produces no CLIENT span at
all: no HTTP status code, no retry count, no way to separate wire latency
from SDK-side processing, no connection-level failure detail. Every other
outbound hop in this project's chain gets exactly this information; this one
hop does not.

**Project and version:** `anthropic==1.4.0` (PyPI: `anthropic`, the official
Python SDK), which requires `httpx2<3,>=2.0.0` — `httpx2` (PyPI: `httpx2`,
GitHub `pydantic/httpx2`) is a distribution genuinely separate from `httpx`,
not a typo or a private vendor fork: it is a Pydantic-maintained,
API-compatible fork of `httpx`, created in response to concerns about the
upstream `httpx` maintainer's own activity (`httpx`'s last release was over
a year old and its maintainer had closed off issue discussions at the time
the fork's rationale was written — see the tracker evidence below). The
instrumentation package in question is
`opentelemetry-instrumentation-httpx==0.65b0`, already installed in this
project (`agents/settlement-agent/requirements.txt` and
`agents/claims-agent/requirements.txt`, via `opentelemetry-instrumentation-starlette`'s
own dependency chain).

**Where the behaviour lives:** confirmed by reading the installed packages
directly. `anthropic/_base_client.py:41`:
`from httpx2 import URL, Proxy, HTTPTransport, AsyncHTTPTransport`;
`pydantic_ai/providers/anthropic.py` types its client as
`httpx2.AsyncClient`. This project's own `agents/settlement-agent/bootstrap.py`
applies `HTTPXClientInstrumentor()` (`_module_name="httpx"`,
`opentelemetry/instrumentation/httpx/__init__.py:1796-1800` in the installed
package) and never applies the sibling `HTTPX2ClientInstrumentor`
(`_module_name="httpx2"`, same installed file, lines 1821-1825) — which is
present in the very same package, not a separate dependency that would need
adding.

**What the convention/library says should happen:** the installed
`opentelemetry-instrumentation-httpx` package's own module docstring states
the fix directly: "When instrumenting `httpx2` clients, use
`HTTPX2ClientInstrumentor`" — with a code example. The library does not
silently degrade or warn when only `HTTPXClientInstrumentor` is applied
against a codebase that also uses `httpx2`; the two instrumentors are
independent, opt-in calls, and using only one is a valid but incomplete
configuration.

**What we observed instead, before the fix:** capturing a real settlement end
to end and reading every httpx-instrumented CLIENT span back from Jaeger: 12
spans, all of them settlement-agent's or payments-mcp's calls to
payments-mcp or payments-api (`POST`/`GET`/`DELETE` against
`payments-mcp...svc.cluster.local:8000` and
`payments-api...svc.cluster.local:8080`). **Zero** spans targeted
`api.anthropic.com`. The only trace of the Anthropic call anywhere in the
captured spans was `server.address=api.anthropic.com`, appearing as an
attribute on pydantic-ai's own `chat claude-sonnet-5` span — a real, useful
span, but not an HTTP CLIENT span, and carrying none of the HTTP-layer
attributes an httpx span would.

**What we observed after the fix (Task 8):** the same live-capture method,
re-run against a rebuilt and redeployed settlement-agent. Each of the three
`chat claude-sonnet-5` spans in a three-turn conversation now has exactly
one httpx-instrumented CLIENT span nested directly under it — three such
spans total, one per model turn, no duplicates.

The evidence for that is a before/after comparison of live trace dumps, and
it is worth being precise about what such a comparison can and cannot show
here, because this trace is **not** deterministic.

**Fix round (final-review.md SF-4): the paragraph that used to sit here —
itself written to correct an earlier overstatement of this same
measurement — was wrong in three ways, all found by measuring it again
rather than trusting the review's own table either.** Measured directly off
nine traces already in Jaeger (trace IDs and per-service breakdown below),
not copied from any report:

1. **"Varies between 11 and 13" is falsified.** The a2a-sdk's internal
   `a2a.server.events.event_queue.EventQueue.dequeue_event` polling span
   (settlement-agent) varies **10 to 16** across the nine traces measured
   here — a post-fix direct-A2A run of the same shape the original range was
   drawn from shows 16. This is a **sample of nine**, not a bound; a
   different nine runs would likely show a different range. Whole-trace
   totals differ run to run for reasons unrelated to any change — the same
   variance this project already documents when it declines to publish an
   exact span total — and a raw total-span delta must not be read as the
   effect of this fix.
2. **"The service's total httpx CLIENT count exactly 15, every time" was
   scenario-scoped but stated unconditionally.** It is 15 on a direct-A2A
   run and **16** on a browser- or claims-agent-rooted run, because
   claims-agent contributes one outbound-A2A httpx CLIENT span under the
   same instrumentation scope in those scenarios — and the browser-rooted
   run is the one the talk actually drives.
3. **"The service's total" was never one service's total.** The 15 (or 16)
   is settlement-agent's own httpx CLIENT spans plus payments-mcp's 2. The
   number that *is* stable across every post-fix trace measured here, in
   both scenarios — **settlement-agent's own httpx CLIENT count: exactly
   13**, of which **exactly 3** target `api.anthropic.com` — is the claim
   this finding actually needs, and it holds:

| trace | scenario | settlement-agent `dequeue_event` | settlement-agent httpx CLIENT | anthropic-bound |
|---|---|---|---|---|
| `18372fc00318` | browser-rooted | 10 | 13 | 3 |
| `85489d704685` | claims-rooted | 11 | 13 | 3 |
| `abfed25aec96` | claims-rooted | 12 | 13 | 3 |
| `a6d0e07addd6` | direct A2A | 12 | 13 | 3 |
| `6099a9509c66` | direct A2A | 13 | 13 | 3 |
| `21ca441e8fec` | direct A2A | 16 | 13 | 3 |
| `f80b118405a9` | direct A2A | 12 | 13 | 3 |
| `410e94191314` | direct A2A, **pre-fix** | 13 | 10 | **0** |
| `0defba41e0fb` | direct A2A, **pre-fix** | 11 | 10 | **0** |

So: settlement-agent's own httpx CLIENT spans, 10 pre-fix to 13 post-fix, of
which exactly 3 target `api.anthropic.com` in every post-fix trace measured
here, across both the direct-A2A and browser/claims-rooted scenarios. That is
the before/after evidence this finding rests on. `dequeue_event` varies run
to run (10-16 observed across this sample) for reasons unrelated to this fix
and is not part of the claim. Reading a
full attribute dump directly, rather than assuming the spelling: the
settlement-agent deployment sets `OTEL_SEMCONV_STABILITY_OPT_IN=http`, so
this new span carries the **stable** HTTP semconv spelling, matching every
other outbound hop this service makes —
`http.request.method=POST`, `http.response.status_code=200`,
`server.address=api.anthropic.com`,
`url.full=https://api.anthropic.com/v1/messages?beta=true`,
`network.peer.address=api.anthropic.com`, `network.protocol.version=1.1` —
not the legacy `http.method`/`http.url`/`http.status_code` spelling. Both
`HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor` share one
`get_tracer(__name__, ...)` call in the same
`opentelemetry.instrumentation.httpx` module, so `otel.scope.name` alone
cannot distinguish the two — the model-bound span is identified by
`server.address == "api.anthropic.com"` instead, which is how
`tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`
finds it.

**Why it matters, beyond this project:** any integrator instrumenting
`httpx` in the ordinary way, and depending — directly or, as here,
transitively through the Anthropic SDK — on a library that has moved its
transport to `httpx2`, gets a silent, total gap for that one hop. Nothing
errors, nothing warns; the span for that hop simply never appears. The
transition from `httpx` to `httpx2` is not unique to this project's
dependency tree: at the time of this search, GitHub issue search turns up
dozens of open issues and PRs across widely-used projects (`stripe-python`,
`datasette`, `huggingface_hub`, `litestar`, `reflex`, `chroma`, `AutoGPT`,
Apache Airflow's own provider packages, among many others) either migrating
to `httpx2` or asking to. Anyone instrumenting HTTP calls generically across
a dependency tree that includes even one such library needs to know both
instrumentors exist and apply both — the library's own docstring says so,
but nothing forces or checks it.

**What the fix needed, and what Task 8 did:** for this project specifically,
one line — `HTTPX2ClientInstrumentor().instrument()` alongside the existing
`HTTPXClientInstrumentor().instrument()` call in `bootstrap.py`. No
dependency bump, no waiting on anyone: the class needed was already
installed. Task 8 added exactly that line, rebuilt and redeployed
settlement-agent, and confirmed via the before/after diff above that it
produces one CLIENT span per model turn, correctly parented to that turn's
`chat` span, with no duplicate.

**How we fixed it, and whether it should be adopted upstream:** there is no
upstream action to recommend — unlike Findings 1–4, upstream already did the
work; this project's own omission was the only thing missing. Before the
fix, the gap was pinned rather than silently left to rot:
`tests/e2e/test_settlement_llm_trace.py::test_the_models_own_http_hop_has_no_client_span`
asserted the absence directly and was written to fail loudly the day someone
added the missing instrumentor call. Task 8 added that call; that test was
retired (per its own docstring's instruction) and replaced by
`test_anthropic_client_span_exists_and_is_parented_to_chat`, which asserts
the positive shape instead.

**Existing upstream activity (verified live against the real repositories,
read-only, via `gh issue`/`gh pr`, 2026-09-08 — no comment or issue was
posted anywhere as part of writing this):**

- [`open-telemetry/opentelemetry-python-contrib#4635`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4635)
  ("httpx2 instrumentor/httpx instrumentor support for httpx2") — **closed**,
  filed by a third party (`gloshkajian`), explaining the `httpx`-maintenance
  concerns behind the `httpx2` fork's existence and asking for matching
  OTel instrumentation.
- [`open-telemetry/opentelemetry-python-contrib#4730`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4730)
  ("Feature/httpx2 instrumentor") — **merged** 2026-07-15 (`mergedAt`
  timestamp read directly from the PR), by the same author, closing #4635.
  Adds `HTTPX2ClientInstrumentor` as an independent sibling to
  `HTTPXClientInstrumentor`, tested against dedicated `httpx2` and
  coexistence test environments.
- The package's own `CHANGELOG.md`, read directly from the repository,
  confirms the release: **"Version 1.44.0/0.65b0 (2026-07-16) — Added —
  `opentelemetry-instrumentation-httpx`: add support for httpx2 (#4730)"** —
  `0.65b0` is the exact version string this project already pins and has
  installed. The fix was not merely available before this milestone started;
  it was already inside the dependency this project had already chosen.
- Two open, unrelated-but-adjacent issues on the same instrumentation
  package show the `httpx2` surface is still actively maturing, not that it
  is missing: [`#4953`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4953)
  ("`openai` 3.x: `server.address`/`server.port` are never recorded
  (`isinstance` check misses `httpx2.URL`)") and its in-progress fix
  [`#4954`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4954)
  — both about a narrower attribute-extraction bug in OpenAI's own client
  specifically, with the instrumentor already applied; not about the
  instrumentor's existence, and not about Anthropic's client.
- Two other observability vendors show the identical "httpx2 exists, our own
  tracer doesn't cover it yet" gap independently, corroborating that this is
  a real, ecosystem-wide instrumentation transition rather than a quirk of
  this project's own dependency choices: [`DataDog/dd-trace-py#18780`](https://github.com/DataDog/dd-trace-py/issues/18780)
  ("[FEATURE]: Support httpx2", open) and
  [`elastic/apm-agent-python#2706`](https://github.com/elastic/apm-agent-python/issues/2706)
  ("feat: add httpx2 instrumentation", open).
- Searched specifically and found **nothing**: no open issue on
  `anthropics/anthropic-sdk-python`, `pydantic/pydantic-ai`, or
  `open-telemetry/opentelemetry-python-contrib` asks for the Anthropic
  client's own outbound hop to gain a CLIENT span, or documents this
  project's specific instance of the gap (an integrator applying only
  `HTTPXClientInstrumentor` against a dependency tree that includes
  `httpx2` via the Anthropic SDK). This is not because the gap is
  unreportable — it is because the generic fix that would close it is
  already shipped, so there is nothing left for a report to ask for beyond
  "apply the instrumentor you already have," which is this project's own
  homework, not upstream's.

**Status: closed by Task 8 — not filed by this project, and, unlike every
other finding in this document, there was never any upstream action to
file.** The fix existed, merged, already installed in this repository's own
dependency tree, before this milestone ever measured the gap. The one
remaining step — applying `HTTPX2ClientInstrumentor()` in
`agents/settlement-agent/bootstrap.py` — was this project's own deferred
work, not an upstream gap, and Task 8 did it: one line added alongside the
existing `HTTPXClientInstrumentor()` call, verified by a before/after trace
diff to produce exactly one CLIENT span per model turn with no duplicate.
Recording this here in full, in the same format as Findings 1–4, rather than
silently dropping it once the search came back negative for "a gap worth
filing," because the accurate statement — "we suspected an upstream defect,
searched, and found it already fixed" — is itself a correction to how this
milestone's own working notes had framed the finding before this document
was written, and the project's governing rule is to correct a telemetry
claim the moment it is found to be wrong, not to quietly let the
more-interesting-sounding version stand.

---

## Finding 6: agentgateway v1.5.0 reads trace context from HTTP headers only, never from JSON-RPC `params._meta`, and mints a new trace root when no header is present

**The problem, plainly:** MCP carries W3C trace context inside the JSON-RPC
message, in `params._meta`. agentgateway is an MCP proxy, and it *writes*
that carrier correctly on the way out — but it never *reads* it on the way
in. Inbound trace context is taken from the HTTP `traceparent` header and
from nowhere else. A proxied MCP request that carries context only in
`_meta` — which is the protocol's own carrier, and the only one a stdio
client has — is treated as having no parent, and the gateway starts a fresh
trace of its own.

**Project and version:** `agentgateway` v1.5.0 (image
`cr.agentgateway.dev/agentgateway:v1.5.0`; the binary reports its internal
version as the bare `1.5.0`). A **Linux Foundation** project — *not* a CNCF
one.

**Where the behaviour lives**, read from the v1.5.0 source, not inferred
from behaviour:

- **Ingress.** `TraceParent::from_request`
  (`crates/agentgateway/src/telemetry/trc.rs:690-696`) is four chained calls
  on `req.headers()`: get `traceparent`, to a `str`, `try_from`. The request
  body is never touched, and there is no other extraction site.
- **Egress.** `IncomingRequestContext::stamp_trace_context`
  (`crates/agentgateway/src/mcp/upstream/mod.rs:110-122`) copies the three
  W3C keys out of the outbound *headers* into `_meta`, unprefixed, and its
  own comment states the intent: *"SEP-414: copy W3C trace context into the
  message's `_meta` (un-prefixed keys, per spec). The only trace carrier for
  stdio upstreams, which have no request headers."* It is called from the
  outbound send paths (`mod.rs:338`, `mod.rs:395`), immediately after
  `start_mcp_outbound_span` has injected the gateway's own CLIENT-span
  context into those headers (`mod.rs:146`) — which is why the value that
  lands in `_meta` is the gateway's outbound span, correctly parenting the
  upstream MCP server's spans under it.

So the two directions are asymmetric by construction: `_meta` is an output
format, chosen for a transport that has no headers, and there is no
corresponding input path.

**What the convention says should happen:** MCP's SEP-414 defines
`traceparent`, `tracestate` and `baggage` as unprefixed keys inside the
JSON-RPC `params._meta` object — the three exceptions to `_meta`'s usual
reverse-DNS prefixing rule. It is the protocol-level carrier, deliberately
transport-independent, and an intermediary that both consumes and produces
MCP messages would be expected to honour it in both directions. agentgateway
already agrees with that on the write side; the read side is where it stops.

**A second-order detail, stated precisely because the obvious phrasing
overstates it:** the write-out *overwrites* any inbound `_meta.traceparent`
without ever having read it — `meta.0.insert(...)`, not an
insert-if-absent — but only when the corresponding header is present.
Upstream's own unit test `stamp_trace_context_noop_without_trace_headers`
(`mod.rs:867-873`) pins the no-header case as a no-op. With tracing enabled
the gateway always injects its own header first, so in practice the
overwrite is unconditional; with tracing disabled a client's `_meta` passes
through untouched.

**What we observed instead:** this project's MCP hop
(`settlement-agent → agentgateway → payments-mcp`) stays a single trace, and
the reason is not that the gateway understands `_meta`. Two independent
carriers happen to both be present:

```
settlement-agent  --  Authorization: Bearer <JWT-SVID>   (identity)
                  --  traceparent: HTTP header           <- the gateway reads THIS
                  --  params._meta.traceparent           <- payments-mcp reads this
```

`agents/settlement-agent/mcp_tools.py` injects `_meta` by hand for FastMCP's
benefit, and *separately*, `HTTPXClientInstrumentor` auto-injects a
`traceparent` **header** because the MCP streamable-HTTP transport is
httpx-based. The gateway reads the header; the `_meta` bag rides along for
payments-mcp. Both were measured live, and
`tests/e2e/test_gateway_trace.py`'s module docstring records the same
distinction so that a reader of the test does not conclude agentgateway
supports SEP-414 on ingress. It does not.

**Blast radius — and this is where an earlier draft of this finding
overstated the case: it does not affect us.** `settlement-agent` always
sends the header, so the trace holds, and nothing in this project needs a
fix. The failure mode belongs to a *header-less* MCP client: a pure stdio
transport, or a client library whose HTTP layer is not auto-instrumented. In
that case the split is specific rather than total — the gateway mints its own
root for its own SERVER/CLIENT span pair, while the untouched
`_meta.traceparent` still carries the client's context through to the MCP
server behind it. The result is two traces where there should be one, with
the proxy hop in neither of them alongside its own caller.

**Why it matters, beyond this project:** this is the seam where an
observability claim about an agentic platform is won or lost. A proxy exists
partly to be the place you look when a tool call misbehaves, and a proxy that
silently re-roots a trace is worse than an untraced one — the spans exist,
they look healthy, and the causal link to the caller that made the request is
gone. Anyone adopting agentgateway in front of stdio-transport MCP servers,
or in front of a client that does not auto-instrument httpx, will get exactly
that, and the symptom (an orphan trace per proxied call) does not obviously
point at trace-context extraction as the cause.

**What a fix would need to do:** extract inbound trace context from
`params._meta` when no HTTP `traceparent` header is present — the mirror of
`stamp_trace_context`, using the same three unprefixed SEP-414 keys — and
prefer the header when both are present, so today's behaviour is unchanged
for HTTP clients. It would also make the existing overwrite honest rather
than lossy: the value being replaced would have been read first.

**How we worked around it, and whether it should be adopted upstream:** we
did not work around it, and there is nothing here to recommend upstream
adopt. The header this project already sends is not a workaround — it is
`HTTPXClientInstrumentor` doing its ordinary job, and it was present before
the gateway was interposed. Adding a bespoke second carrier to compensate
would be the invented-convention risk this project's governing rule exists to
avoid.

**Existing upstream activity (verified live, read-only, 2026-09-09).**

- Searched the tracker for `_meta traceparent extract`, `SEP-414` and
  `stdio trace context` and found **nothing asking for ingress extraction from
  `_meta`**. This half appears unreported.
- The closest neighbour, and it is close enough to be worth reading before
  anyone acts on this finding, is
  [`agentgateway/agentgateway#2904`](https://github.com/agentgateway/agentgateway/issues/2904)
  ("Tracing: MCP backends forward the inbound traceparent, so the upstream
  span parents to the caller instead of the gateway") — filed by a third party
  (`suren-khatana`) against **v1.4.1** and **closed as completed** on
  2026-08-17. It is the *write*-side sibling of this finding, in the same
  file: on `mcp:` backends the gateway forwarded the stale inbound
  `traceparent` verbatim, so the upstream MCP server's span came out a
  *sibling* of the gateway's rather than its child, and — the issue notes this
  explicitly — *"the same snapshot feeds `stamp_trace_context`, so the SEP-414
  `_meta` carrier gets the stale value too."*
- That fix is present in the pinned v1.5.0 and this project depends on it:
  `start_mcp_outbound_span` injects the gateway's own outbound span into the
  headers (`mod.rs:146`) *before* `stamp_trace_context` copies them into
  `_meta` (`mod.rs:338`, `:395`), which is why payments-mcp's spans correctly
  parent under the gateway's CLIENT span in this project's measured traces
  rather than under settlement-agent. Worth recording because it means the
  write side was actively worked on one release ago and the read side still
  was not — the asymmetry is current, not ancient.

**Status: not filed by this project.** No issue, comment, pull request or
message of any kind was posted to any upstream repository or tracker as part
of recording this — the tracker was read only, via `gh issue view` and
`gh search issues`. The evidence above is a source read of the pinned release
plus this project's own live measurements. Whether to raise it upstream is a
decision for this project's maintainers.

---

## Finding 7: agentgateway v1.5.0's span attributes are its access-log field schema, so they match neither HTTP semantic convention — and identity lands on `jwt.sub` where semconv has `user.id`

**Why these two are one finding.** They look like two unrelated naming
complaints and are in fact the same defect with the same single cause, in the
same list of code, which is why they are not numbered separately: the
gateway's OTel span attributes are literally its structured access-log
fields, reused verbatim. A log field vocabulary is not a semantic-convention
vocabulary, and every divergence below follows from that one decision.
Upstream's own merged fix (see "Existing upstream activity") confirms that
shared cause — it splits the two sinks apart, which is the only thing that
*could* fix them together — while addressing just the HTTP half and leaving
`jwt.sub` where it is.

**The problem, plainly:** agentgateway v1.5.0's spans carry `http.method`,
`http.path`, `http.status`, `http.host` and `http.version`. That set matches
neither the current *stable* HTTP semantic conventions nor the *legacy*
pre-stabilisation spelling — `http.path` and `http.status` belong to neither
vocabulary. Separately, the verified JWT subject is recorded as `jwt.sub`,
where the semantic conventions have a registry attribute for exactly this
(`user.id`).

**Project and version:** `agentgateway` v1.5.0, as above. Linux Foundation,
not CNCF.

**Where the behaviour lives:** one `Vec` of `(&str, value)` pairs built in
`crates/agentgateway/src/telemetry/log.rs` — `http.method` at :1514,
`http.host` :1515, `http.path` :1516, `http.version` :1518, `http.status`
:1520, `jwt.sub` :1534 — and then handed to three sinks from the same
variable: the tracer (`t.send(&log, ..., kv.as_slice())`, :1793-1799), the
OTLP logger (:1818-1834), and the access log (:1837 onward). The two keys
that *are* correct stable semconv are the two that go through a different
path: `crates/agentgateway/src/telemetry/trc.rs` has a `semconv` module
(:159-164) whose entire contents are `network.protocol.version` and
`url.scheme`, pushed at :306-313. Everything else is a raw string literal.

**What the convention says should happen:**

| What it is | Stable semconv | OTel legacy | agentgateway v1.5.0 |
|---|---|---|---|
| request method | `http.request.method` | `http.method` | `http.method` |
| request path | `url.path` | `http.target` | `http.path` |
| response status | `http.response.status_code` | `http.status_code` | `http.status` |
| server host | `server.address` | `http.host` | `http.host` |
| protocol version | `network.protocol.version` | `http.flavor` | `network.protocol.version` ✓ |
| url scheme | `url.scheme` | — | `url.scheme` ✓ |
| authenticated principal | `user.id` | `enduser.id` (deprecated in favour of `user.id`) | `jwt.sub` |

**What we observed instead:** measured on live spans from the deployed
gateway and pinned by exact set equality in
`tests/e2e/test_gateway_trace.py` (`GATEWAY_TOOLS_CALL_SERVER_ATTRS`,
`GATEWAY_TOOLS_CALL_CLIENT_ATTRS`), with the full attribute inventory in
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "agentgateway
instrumentation, as measured". Every one of those names was read off a real
span and none was predicted from the source; the source citations above
explain names that were *already* measured, in that order.

For contrast, in the same trace: the three Python services emit the stable
spelling (`http.request.method`, `http.response.status_code`,
`server.address`, `url.full`) because
`OTEL_SEMCONV_STABILITY_OPT_IN=http` forces them onto it, and payments-api's
`otelhttp v0.66.0` emits it by default (v0.71.0 as of 2026-09-11, and
re-measured on that bump: the attribute set is unchanged). So a single trace in this system
contains three HTTP attribute vocabularies, one of which is not an OTel
vocabulary at all — again a property of the pinned v1.5.0 specifically, and the
count drops to two once a release carrying PR #3182 is adopted with its opt-in
enabled.

**Why it matters, beyond this project — and the honest version of the claim
is the stronger one.** The tempting thing to say about this system is "one
convention end to end." That is measurably false, and the true statement is
better: *the trace holds together end to end across components that do not
agree on attribute names.* Trace context is what makes the chain one thing;
attribute naming is what makes it queryable. Those are separable, and this
finding is the demonstration. Concretely, an operator cannot write one
`http.response.status_code` filter across this trace, and cannot group by
authenticated principal across a fleet where one component says `user.id`
and another says `jwt.sub` — while every span still sits in the right place
in the right tree. Anyone building a dashboard over a mixed fleet pays this
cost, and paying it at the query layer is a normalisation problem rather
than a tracing one.

**What a fix would need to do:** route the HTTP keys through the `semconv`
mechanism that already exists in `trc.rs` and already holds two keys —
emitting `http.request.method`, `url.path`, `http.response.status_code` and
`server.address` on spans — and record the verified subject as `user.id`. It
should *not* mean renaming the human-facing access-log fields: a log schema
is legitimately its own thing, and the real fix is to stop having one list
serve both purposes, so the OTel sinks can follow the convention while stdout
stays readable.

**Existing upstream activity (verified live, read-only, 2026-09-09).** This
section changed the finding, and it is the reason this document requires one:
the first draft of Finding 7 presented the HTTP half as an unnoticed defect.
It is neither unnoticed nor, any longer, unfixed.

- [`agentgateway/agentgateway#1151`](https://github.com/agentgateway/agentgateway/issues/1151)
  ("fix(telemetry): HTTP span/log attributes use deprecated OTel semantic
  conventions") — **closed as completed**, filed 2026-03 by a third party
  (`AryanParashar24`), and it lists *exactly* the same six divergences this
  project measured independently, plus one more we had not noted
  (`src.addr` → `client.address`). Its own root-cause paragraph names the same
  place: *"In `log.rs` inside `impl Drop for DropOnLog`, the `kv` vec hardcodes
  old attribute string literals directly with no semconv package
  dependency."* Independent confirmation, arrived at from the other end.
- The discussion on that issue is worth reading rather than summarising away,
  because a maintainer (`howardjohn`) initially argued *against* changing the
  defaults — "I don't want to hurt users to appease a specification many users
  do not care about at all" — and proposed a preset instead, which
  `krisztianfekete` (contributor) supported after arguing the cross-signal
  consistency case. So the divergence was a considered position for a while,
  not an oversight. Our own "don't rename the human log" conclusion above was
  reached separately and lands in the same place as theirs; the credit for it
  is upstream's.
- [`agentgateway/agentgateway#3182`](https://github.com/agentgateway/agentgateway/pull/3182)
  ("fix(telemetry): align HTTP attributes with OTel semconv", by `harukz`,
  approved by `howardjohn`) — **merged 2026-09-01**, which is **after
  v1.5.0 was released (2026-08-27)**. That timing is the whole point and it
  is easy to get wrong: the PR was *opened* on 2026-08-26, one day before the
  release, so a glance at a PR list suggests it shipped. It did not. The
  pinned v1.5.0 this project runs contains none of it — verified by reading
  the release's own source, where `src.addr` is still a raw literal at
  `log.rs:1504` and no `preset` or `semconv.rs` exists at all — which is
  exactly why the measurements above are real rather than stale.
- What that PR does, and it is essentially the fix proposed above: renames the
  six keys for **request traces and OTLP access logs only**, adds a new
  `crates/agentgateway/src/telemetry/semconv.rs`, deletes 21 lines from
  `trc.rs`, keeps stdout's defaults for backward compatibility, and adds an
  opt-in `accessLog.preset: Otel`. It also fixes value-level divergences we
  had not looked at (`http.version` as `1.1` rather than `HTTP/1.1`, host and
  port split, `url.query` separated).
- **The `jwt.sub` half is not covered by any of it.** Checked directly against
  the merge commit's own diff of `log.rs`: it touches neither `jwt.sub` nor
  `user.id`. Searching the tracker for `user.id`, `enduser` and `jwt.sub`
  turns up nothing asking for the authenticated principal to follow the
  convention. So of this finding's two halves, one is fixed-and-unreleased and
  one appears genuinely unreported.

**What this means for the finding, stated plainly rather than quietly
dropped.** The HTTP half is Finding-5-shaped: real in the version this project
runs, already solved upstream, awaiting a release. It is kept here in full
because "we measured a divergence, searched, and found the fix already merged
but not yet released" is a different and more useful claim than either "we
found a bug" or silence — and because the divergence is live in the pinned
image, so this project's own documents and tests must describe it accurately
today. When the next release lands, `tests/e2e/test_gateway_trace.py`'s exact
set equality will fail on the rename, which is the intended way for this
project to find out.

**How we worked around it, and whether anything should be adopted upstream:**
we did not rewrite or normalise agentgateway's attributes anywhere — not in
the collector, not in the test harness, not in the contract. They are
published under their real names, marked as a third vocabulary, and asserted
by exact set equality so an upstream change fails a test here instead of
quietly ageing a document. The no-invented-telemetry rule applies to
third-party components without exception: a collector rename would make this
project's telemetry disagree with what agentgateway actually emits, which is
worse than the divergence it would hide. There is nothing to recommend
upstream adopt for the HTTP half — the merged PR is better than anything we
would have proposed.

**Status: not filed by this project.** No issue, comment, pull request or
message of any kind was posted to any upstream repository or tracker as part
of recording this — the tracker was read only, via `gh issue view` /
`gh pr view` / `gh search issues`. The HTTP half needs no filing (merged
upstream already). The `jwt.sub` half is unreported and *remains* unfiled:
whether to raise it is a decision for this project's maintainers, not one an
implementation task takes unilaterally.

---

## Finding 8: agentgateway v1.5.0's ExtMCP guardrail has two config vocabularies with different casing, and the only worked example in the repository is the one a standalone deployment cannot use

**The problem, plainly:** the `mcpGuardrails` / ExtMCP feature can be
configured two ways — through the Kubernetes CRDs (`AgentgatewayPolicy`) or
through a standalone `config.yaml` — and the two spell the same settings
differently: PascalCase enum values and a nested `remote:` key in the CRD,
camelCase values and a `kind: remote` discriminator in the standalone config.
At v1.5.0 the repository's `examples/` tree, which is where a reader is
directed for working configuration, contains **no example of either**. The one
worked ExtMCP configuration anywhere in the tree is a CRD manifest under
`controller/test/e2e/testdata/`. A reader who reconstructs a standalone
config from it gets four of five spellings wrong, and finds out one key at a
time.

**Project and version:** `agentgateway` v1.5.0 (released 2026-08-27), image
`cr.agentgateway.dev/agentgateway:v1.5.0`. Linux Foundation, not CNCF. The
image's `--version` reports `git_revision:
fe6732474a96a0363dfb9822859af4e9bab360fa`, which is exactly the commit the
`v1.5.0` tag points at — so every source line cited below is the source of the
binary this project runs, not of a nearby revision.

**What we observed. [measured]** Listing the v1.5.0 tree read-only
(`gh api repos/agentgateway/agentgateway/git/trees/v1.5.0?recursive=1`):
`examples/` holds 33 directories and 230 paths, including `mcp-authorization`,
`mcp-telemetry`, `mcp-basic`, `mcp-multiplex`, `mcp-openapi`, `mcp-apps` and
`mcp-tls`. Fetching and grepping every YAML file under `examples/` for
`mcpGuardrails`, `ext_mcp` or `extMcp` returns **zero** hits. The nearest
neighbour, `examples/mcp-authorization/config.yaml`, configures
`mcpAuthorization` — the *native CEL* authorization mechanism, a different
feature with a different schema — which is precisely the kind of near-miss
that makes the absence hard to notice.

The only worked ExtMCP configuration at that tag is
`controller/test/e2e/testdata/extmcp/extmcp.yaml`:

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
spec:
  backend:
    mcp:
      guardrails:
        processors:
        - remote:
            backendRef: { name: ext-mcp, port: 4445 }
            failureMode: FailClosed
          methods:
            tools/call: Request
            tools/list: Response
```

The standalone config that the same binary accepts is a different shape with
different casing:

```yaml
policies:
  mcpGuardrails:
    processors:
      - kind: remote
        host: policy-decision-service.northstar-platform.svc.cluster.local:9100
        failureMode: failClosed
        methods:
          "tools/call": request
        requestHeaders:
          allowed: ["traceparent", "tracestate"]
```

**The cost, measured against the pinned image's own validator. [measured]**
Starting from the working config above and changing exactly one token at a
time, `docker run --rm -v .:/c cr.agentgateway.dev/agentgateway:v1.5.0
--validate-only -f /c/config.yaml` says:

| Change | Validator output |
|---|---|
| `kind: remote` → `kind: Remote` | ``unknown variant `Remote`, expected `remote` `` |
| `"tools/call": request` → `Request` | ``unknown variant `Request`, expected one of `off`, `request`, `response`, `full` `` |
| `failureMode: failClosed` → `FailClosed` | ``unknown variant `FailClosed`, expected `failClosed` or `failOpen` `` |
| `requestHeaders.allowed` → `.allow` | ``unknown field `allow`, expected `allowed` or `disallowed` `` |

Three of those four are exactly the spellings the CRD example uses. The fifth
key a reader must guess — how to name the policy server's address — is `host`,
the `TargetOrUri` arm of a flattened `SimpleBackendReferenceWithPolicies`,
where the CRD says `backendRef: {name, port}`.

**Where the behaviour lives:** the casing is not accidental and not
per-field. `schema!` and `schema_enum!` in `crates/core/src/serdes.rs` expand
to `#[serde(rename_all = "camelCase", deny_unknown_fields)]`, and every
standalone-config type in `crates/agentgateway/src/types/` carries one of
them. The CRD types are Go, in `api/` and `controller/`, and follow Kubernetes
convention. Both are internally consistent; nothing reconciles them for a
reader, and no example shows the second one.

**Why it matters, beyond this project.** ExtMCP is the extension point for
putting an external policy decision on an MCP tool call — the thing an
organisation reaches for when "which agent may call which tool with what
arguments" has to be answered somewhere other than in the agent. That is a
security control, and the failure mode of an undocumented security control is
not that people configure it wrongly and see an error; the validator is
strict, so they do see an error. The failure mode is that people give up and
put the check somewhere weaker, or reach for the CEL mechanism that *does*
have an example even where an external decision is what they need. A
`deny_unknown_fields` validator is a good property; it is not a substitute for
one worked example.

**What a fix would need to do:** add `examples/mcp-guardrails/` in the same
shape as the existing `mcp-*` examples — a `config.yaml` in the standalone
vocabulary, a README, and something runnable on the other end of the gRPC
call. The `crates/agentgateway/src/test_helpers/extmcpmock.rs` already in the
tree is most of that other end. A second, smaller fix would be a note in the
CRD testdata pointing out that its casing is CRD-specific.

**Existing upstream activity (verified live, read-only, 2026-09-10).** Nothing
found. `gh search issues --repo agentgateway/agentgateway` for `mcpGuardrails`,
`ext_mcp`, `ExtMCP`, `guardrails example` and `mcpGuardrails documentation
example` returns issues about guardrail *telemetry* (#3178, #3168, both
closed), an ExtMcp replay feature request (#3003, open), and a backend-resolution
bug (#2302, closed) — none about missing examples or the two-vocabulary skew.
No issue, comment or pull request was posted anywhere as part of this search.

**How we worked around it:** by iterating against the pinned image's validator
until the config parsed, and then recording the working shape and every
rejected spelling in `deploy/platform/agentgateway/config.yaml`'s comments, so
the next reader of *this* repository does not repeat the loop. That file also
records the validator's one useful ordering property: it checks the schema
before it fetches the JWKS, so reaching the JWKS stage already proves the
schema parsed.

**Status: not filed by this project.** Tracker read only, via `gh search
issues` and `gh api`. Whether to raise it is a decision for this project's
maintainers.

---

## Finding 9: `AuthorizationError.mcp_error` is documented as overriding the JSON-RPC error and does not — it is nested whole under `error.data` while the gateway assigns its own code

**Superseded at agentgateway v1.6.0 (2026-10-13): a `tools/call` deny is now an `isError` tool result and `error.data` is not sent. See Finding 17. The text below describes v1.5.0.**

**The problem, plainly:** the ExtMCP protobuf's own comment on
`AuthorizationError.mcp_error` reads *"Optional explicit JSON-RPC error
payload, as raw JSON bytes. **Overrides the code-derived default.**"* At
v1.5.0 it overrides nothing. The gateway computes the JSON-RPC `code` from the
`AuthorizationError.Code` enum unconditionally, puts the `reason` string in
`message`, and parks the entire parsed `mcp_error` payload — its own `code`,
`message` and `data` included — under `data`. A policy server that sets
`mcp_error` to name a specific JSON-RPC error therefore ships that code to a
place no MCP client looks for it.

**Project and version:** `agentgateway` v1.5.0, as in Finding 8, same
revision.

**What we observed. [measured]** With a policy server answering every
`CheckRequest` with
`AuthorizationError{code = PERMISSION_DENIED, reason = "echo-pds denies
everything", mcp_error = {"code": -32003, "message": "PDS-DENY-MARKER",
"data": {"marker": "task-1"}}}`, the body on the wire, byte for byte:

```json
{"jsonrpc":"2.0","id":"7876ec8661114e6d97b45e39255dfb80","error":{"code":-32001,"message":"echo-pds denies everything","data":{"code":-32003,"message":"PDS-DENY-MARKER","data":{"marker":"task-1"}}}}
```

| The policy server sent | The MCP client sees | |
|---|---|---|
| `code = PERMISSION_DENIED` | `error.code = -32001` | the gateway's own constant, **not** the `-32003` inside `mcp_error` |
| `reason` | `error.message` | the free-text reason is the client-visible message |
| `mcp_error` (JSON bytes) | `error.data` | the *whole* payload, parsed, nested one level down |

**Where the behaviour lives:** `translate_error` in
`crates/agentgateway/src/mcp/guardrails/client.rs` at that revision:

```rust
let code = match C::try_from(e.code).unwrap_or(C::Unknown) {
    C::PermissionDenied => PERMISSION_DENIED,      // ErrorCode(-32001)
    C::ResourceExhausted => crate::mcp::RESOURCE_EXHAUSTED,
    C::Invalid => ErrorCode::INVALID_REQUEST,
    C::Unknown => ErrorCode::INTERNAL_ERROR,
};
let data = e.mcp_error.as_deref().and_then(|b| serde_json::from_slice::<Value>(b) ... );
ErrorData::new(code, e.reason, data)
```

`code` is derived from the enum on every path; `mcp_error` only ever reaches
the `data` argument. An unparseable `mcp_error` is dropped with a `warn!` and
the error is returned regardless — so a malformed payload is silently reduced
to the code-derived error, which is the same shape as success at the
protocol level.

**What the convention says should happen:** nothing in JSON-RPC or MCP
requires `mcp_error` to behave either way — this is agentgateway's own
extension point, and either design is defensible. The defect is that the
proto's comment describes one design and the implementation is the other, and
the proto is the artifact a policy-server author reads. `ext_mcp.proto` is
also what implementers vendor: this project vendors it verbatim, header
excepted, precisely so the contract is unambiguous.

**Why it matters, beyond this project.** A policy server's most useful
client-visible signal is a machine-readable one — "this was refused by policy,
do not retry" is a different instruction from "the tool failed, try again",
and an agent framework will act on the difference. If an author believes
`mcp_error.code` reaches the client, they will encode that distinction in a
code, and the agent will never see it: what arrives is `-32001` for every
`PERMISSION_DENIED`, with the intended code buried in `data`. The bug is
silent in both directions — nothing errors, and the deny still works.

**What a fix would need to do:** either honour the comment (when `mcp_error`
parses to an object with a numeric `code`, use it) or correct the comment to
describe nesting. The second is cheaper and arguably better: overriding the
code from an arbitrary payload lets a policy server emit codes rmcp assigns to
unrelated conditions, and the existing constant deliberately skips `-32002`
because rmcp uses it for `RESOURCE_NOT_FOUND`. If the comment is what changes,
it should also say that `data` is where the payload lands, so authors put the
machine-readable marker there on purpose.

**How we worked around it, and it is the reason this finding is here rather
than in a scratch note:** this project's policy server puts its
machine-readable deny marker (`decision: "deny"`, `retryable: false`) in
`mcp_error`, and its agent reads that marker at `error.data`, never by
pattern-matching the reason prose. That works *because* the nesting was
measured before the agent was written. A `tests/`-level contract test
(`agents/settlement-agent/tests/test_policy_deny_contract.py`) drives the real
chain end to end so the two sides cannot drift apart silently.

**Existing upstream activity (verified live, read-only, 2026-09-10).** Nothing
found. `gh search issues --repo agentgateway/agentgateway` for `mcp_error`
returns no results at all; the `ExtMCP` and `mcpGuardrails` searches return
only the unrelated items listed in Finding 8. The tracker was read only.

**Status: not filed by this project.** No issue, comment or pull request was
posted anywhere. Whether to raise it is a decision for this project's
maintainers.

---

## Finding 10: `mcpGuardrails.processors[].requestHeaders.allowed` is an exhaustive allowlist, so naming any header silently drops `traceparent` and every policy decision starts its own trace

**The problem, plainly:** `requestHeaders.allowed` reads like an addition to
whatever the gateway already forwards. It is a replacement. An empty list
forwards everything; a non-empty list forwards *only* what it names. So the
moment an operator adds one header for the policy server's benefit, W3C trace
context stops being forwarded — and the only symptom is that the policy
server's spans are no longer in the caller's trace. Nothing fails, nothing is
logged, and the policy itself keeps working perfectly.

**Project and version:** `agentgateway` v1.5.0, as above, same revision.

**Where the behaviour lives:** `crates/agentgateway/src/mcp/guardrails/mod.rs`
at that revision. The doc comment is accurate and the code matches it:

```rust
/// Allow/deny filter over request headers, mirroring ext_authz: empty `allowed`
/// forwards every header plus all pseudo-headers (`:authority`, `:method`, ...);
/// a non-empty `allowed` forwards only the listed names. `disallowed` always wins.
pub struct HeaderFilter { pub allowed: Vec<...>, pub disallowed: Vec<...> }

pub fn allows(&self, name: &HeaderOrPseudo) -> bool {
    if self.disallowed.iter().any(|n| n == name) { return false; }
    self.allowed.is_empty() || self.allowed.iter().any(|n| n == name)
}
```

`collect_headers` then builds the policy server's view of the request from
that filter alone. So this is not a bug in the sense of code disagreeing with
its own documentation — it is a **default that is safe and a first edit that
is not**, in a field whose name invites exactly that first edit.

**What we observed. [measured]** Three values, each measured against the
running cluster by driving one real MCP `tools/call` with a minted JWT-SVID
and a known incoming `traceparent`:

| `requestHeaders` | Where the policy server's span landed |
|---|---|
| `allowed: ["authorization"]` | **a new root trace of its own** — orphan single-span traces, `refs=[]` |
| block absent entirely | correctly parented under the gateway's `tools/call` span |
| `allowed: ["traceparent", "tracestate"]` | correctly parented |

The first row is the shape this project shipped first, and it is worth being
precise about what it looked like from the outside: the policy decided
correctly, the payment was refused correctly, the policy server appeared in
the trace backend's service list, its spans existed and carried every
attribute they should — and the milestone's central observability claim, that
the decision appears in the same trace as the call it decided, was quietly
false. No log line anywhere mentioned a header.

**Why it matters, beyond this project.** Anyone wiring an external
authorization or policy hook has two reasons to name headers: forwarding a
credential, and trimming what a second service gets to see. Both are
legitimate and both are the first edit an operator makes to this field. The
security-minded edit — "forward only `authorization`" — is precisely the one
that costs you distributed tracing across the policy hop, which is the hop
whose latency and outcome an operator most wants in the trace. `ext_authz`,
which this filter explicitly mirrors, has the same shape and therefore the
same trap.

**What a fix would need to do:** the cheapest fix is documentation — say in
the field's doc comment, and in whatever example ships for this feature (see
Finding 8), that trace-context headers are subject to the same filter and must
be listed explicitly. A stronger fix is to exempt `traceparent`/`tracestate`
from the allowlist the way pseudo-headers are already handled specially, on
the reasoning that trace context is transport metadata rather than request
content; that is a design call for upstream, and it has a real counter-argument
(explicit is better than magic).

**How we worked around it:** by naming the pair explicitly —
`allowed: ["traceparent", "tracestate"]` — rather than by deleting the block.
Removing `requestHeaders` also restores parenting, but it forwards every
header, and the contents of "every header" were not measured; naming exactly
what the hook needs is the same argument this repository's network policies
make. `authorization` is deliberately *not* in that list: the policy server
reads the caller's identity from the gateway's verified `metadata_context`
rather than from a header, so forwarding a live bearer credential to a second
service would be a cost with no benefit.

**Existing upstream activity (verified live, read-only, 2026-09-10).** Nothing
found. `gh search issues --repo agentgateway/agentgateway` for
`requestHeaders` returns only unrelated LLM-provider header bugs (#3383,
#3401, #1777); `traceparent guardrails` returns nothing. The tracker was read
only.

**Status: not filed by this project.** No issue, comment or pull request was
posted anywhere. Of the three M2.2 findings this is the one whose fix is most
plausibly a one-line doc comment, and it is still the maintainers' call.

---

## Finding 11: `opentelemetry-sdk` 1.44.0 emits a W3C Level 2 flag byte that `go.opentelemetry.io/otel` v1.41.0 rejects outright, so the hop silently starts a second trace instead of failing

> **2026-09-11 (M3): resolved in this repository, by taking the upstream fix.**
> `systems/payments-api` is on `go.opentelemetry.io/otel` **v1.46.0**
> (2026-08-25, the newest stable release on the module proxy; `v1.47.0-rc.1`
> exists and was not taken) with `otelhttp v0.71.0`, built on
> `golang:1.27.1-alpine`, `go.mod` declaring `go 1.25.0` and pinning
> `toolchain go1.27.1`. The workaround described at the end of this finding is
> deleted. **The finding itself stands**: it is the record of a real interop
> defect between two current, correct OpenTelemetry SDKs, it is still
> unreported anywhere, and everything measured below was measured. Read the
> rest in the past tense; the dated corrections inside it mark the sentences
> the fix falsified.

**The problem, plainly:** W3C Trace Context **Level 2** added a second flag
bit — `0x02`, "this trace id was generated randomly". `opentelemetry-sdk`
1.44.0 sets it alongside `SAMPLED`, so the `traceparent` it injects ends
`-03`. `go.opentelemetry.io/otel` v1.41.0's extractor refuses any version-`00`
traceparent whose flag byte exceeds `2` and returns an **empty** `SpanContext`
— which `otelhttp` reads as "no parent" and turns into a brand-new trace root.
Neither end is wrong about anything: one implements Level 2, the other
validates against Level 1, and the two pins are one minor release apart across
that boundary. The failure mode is the expensive one: nothing errors, nothing
is logged, both services are healthy, both emit spans with every attribute
they should — and the spans are in two different traces.

**Projects and versions.** Two, and the defect exists only in the *pair*:

- `opentelemetry-sdk` **1.44.0** (OpenTelemetry Python) — pinned by
  `apps/reviewer-ui`, `agents/settlement-agent`, `agents/claims-agent` and
  `platform/policy-decision-service`.
- `go.opentelemetry.io/otel` **v1.41.0** (OpenTelemetry Go) — pinned by
  `systems/payments-api` (`go.mod`, with `go 1.24.0`; built on
  `golang:1.24-alpine`).

**Where the behaviour lives. [read from source]** Both halves were read in the
installed artefacts, not inferred from release notes:

- Python, `opentelemetry/trace/span.py:220` — `RANDOM_TRACE_ID = 0x02`, with
  an `is_random` accessor at :232.
- Python, `opentelemetry/sdk/trace/__init__.py:1234-1242` — and this is the
  line that decides the blast radius below. The bit is **generated** only on a
  trace root (`if parent_span_context is None: random_trace_id =
  self.id_generator.is_trace_id_random()`); on any **child** of an extracted
  remote context it is **inherited** from the parent's flag byte
  (`else: random_trace_id = parent_span_context.trace_flags.random_trace_id`).
  So a 1.44.0 service emits `-03` if it roots the trace, and otherwise emits
  whatever bit its caller sent it.
- Go, `propagation/trace_context.go:107` —
  `if version == 0 && (h != "" || opts[0] > 2) { return trace.SpanContext{} }`.
  An empty `SpanContext` is not an error and is not reported; it is
  indistinguishable from "no header arrived".

**What we observed. [measured — by M3's final fix round, not re-derived here]**
The account below is the fix round's, recorded in
`.superpowers/sdd/2026-09-10-m3-supervised/final-fix-report.md` and in
[`docs/telemetry-contract.md`](telemetry-contract.md); this document cites it
rather than re-running it. It was found the hard way — as the *second* cause
of a propagation failure whose first cause had already been fixed and
verified, which is why it is worth recording as a finding rather than as a
line in a fix report:

| Probe | Result |
|---|---|
| 30 most recent `reviewer-ui` outbound `GET` traces, and 30 most recent `payments-api` `GET /payments` traces, before the fix | all 1 span each, every one its own root; **zero shared trace ids** between the two sets |
| the header `reviewer-ui` actually emits, captured from a local repro of `ledger.py`'s own client against a capturing HTTP server | `traceparent: 00-9946bc5707f7d4a0bbd77773b18f2a8f-9919a1db7019e831-03` |
| **the control** — a hand-injected traceparent ending `-01`, through the repo-managed port-forward | `GET /payments`, span `4fbcdb1162a83475`, parent `['e4763e4e0f4241a6']` — correctly parented |
| the byte-identical request ending `-03` | **no span under that trace id at all**; a fresh root was minted instead |

The control is what makes this a finding rather than a guess: two requests
differing in one hex digit, one parented and one not.

**The blast radius, which is the part with teeth, and which is latent rather
than fixed. [measured — first-hand, for this document]** The workaround (below)
ships in **one** service. The defect is a property of the **system**, and the
only thing standing between it and the money path is a version pin nobody
chose for this reason.

Verified for this document by reading all **seven** `requirements.txt` files in
the repository (six until M4 Task 4 added `platform/eval-scorer/`) and the installed `.venv` of each service that has one:

| Service | `opentelemetry-sdk` pin | Talks directly to `payments-api`? |
|---|---|---|
| `apps/reviewer-ui` | 1.44.0 | **Yes** — and it is the one carrying the workaround |
| `tools/payments-mcp` | **1.29.0** | **Yes** — and it carries nothing |
| `agents/settlement-agent` | 1.44.0 | No (reaches it via the gateway and `payments-mcp`) |
| `agents/claims-agent` | 1.44.0 | No |
| `platform/policy-decision-service` | 1.44.0 | No — `pds/` imports no HTTP client at all; its only outbound HTTP is the OTLP exporter's, to the collector |
| `tests/` (the e2e harness) | 1.29.0 | mints `traceparent` by hand |

> **2026-09-11 (M3): the `opentelemetry-sdk` column is now `1.44.0` in all six
> rows.** `tools/payments-mcp` and `tests/` were bumped from 1.29.0 once
> payments-api reached otel-go v1.46.0; the "Talks directly to `payments-api`?"
> column is unchanged and still correct. The table is left as it was measured
> because the rest of this finding reasons from it.

`tools/payments-mcp/.venv/.../opentelemetry/trace/span.py` has **no
`RANDOM_TRACE_ID` constant at all** — it stops at `SAMPLED = 0x01` on line 210
— and its `sdk/trace/__init__.py:1145-1149` derives the outgoing flag byte
*solely* from the sampling decision, never inheriting any other bit from the
parent. That is the whole reason the `payments-mcp → payments-api` hop
correlates today: at 1.29.0 it emits `-01` unconditionally, whatever arrives.

**So the money path — the `execute_payment` hop, the one in the demo's
headline trace — is correlated by a pin fifteen minor releases behind its
siblings, rather than by anything anyone decided.** `payments-mcp` registers the stock
`TraceContextTextMapPropagator` (`tools/payments-mcp/bootstrap.py`, in the
`set_global_textmap(CompositePropagator([...]))` call -- by name, because M4
Task 2 moved it from the cited `:49-51` to `:61-62` and left `:49-51` holding
the new metrics comment, falsifying this citation inside this same branch) and
relies on it to inject on the outbound call to `payments-api`. Bumping that
one pin to match its four siblings changes its flag byte from a constant into
a function of whatever the gateway sends it.

**What that means precisely, and the honest version is weaker than the
alarming one.** Because 1.44.0 *inherits* the bit on a child span rather than
re-generating it, a bumped `payments-mcp` would emit `-03` **only if the
context it extracts already carries `0x02`**. What agentgateway v1.5.0 puts on
the wire toward `payments-mcp` was **not measured**, by the fix round or by
this document — and Finding 6 establishes that the gateway is a trace-context
participant in its own right rather than a pass-through. So this is a loaded
gun with an unverified chamber, not a demonstrated break. One measurement
settles it: read the flag byte on the `traceparent` `payments-mcp` receives.
Until someone does, "bumping this pin is safe" is not a claim this project can
make.

> **2026-09-11 (M3): settled, and settled in the stronger direction — the
> chamber's contents stopped mattering.** The pin was bumped to 1.44.0 after
> payments-api reached otel-go v1.46.0. Measured inside the deployed
> `payments-mcp` pod, the inheritance mechanism behaves exactly as this
> paragraph predicted:
>
> ```
> child of a `…-03` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-ff9fecefb59e0be7-03
> child of a `…-01` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-d8b6b402ca358a12-01
> ```
>
> So the outbound flag byte really is a function of the inbound one now. What
> makes that safe is the other end: driven end to end with a `-03` parent
> injected at the MCP boundary (`params._meta.traceparent`), trace
> `967b8b72abbd4b6383409a747c0b43f0` holds all three spans with full
> ancestry — `tools/call execute_payment` SERVER `a5a0e0df10bc2223` (parent
> `0e2bb76df975444f`, the injected one) → payments-mcp `POST` CLIENT
> `1d3cb4342ba6e48e` → payments-api `POST /payments/{paymentId}/execute` SERVER
> `1346117449d1a702`. The `-01` control is trace
> `09809e3142874fb09ce8111c5366c9f6`, same shape.
>
> **What agentgateway v1.5.0 forwards is still not measured**, and this entry
> does not pretend otherwise. It is simply no longer load-bearing: both flag
> bytes now traverse the hop, so the answer changes nothing either way.

**What would and would not catch it. [measured — first-hand, and it corrects
the brief that commissioned this entry]** This finding was commissioned with
the claim that nothing in the repository would warn about such a bump and that
the gate would stay green. That is **false**, and the true version is more
useful:

- **The e2e gate would catch it.**
  `tests/e2e/test_payments_mcp_trace.py::test_mcp_span_is_parent_of_payments_api_span`
  asserts genuine ancestry (`is_descendant_of(api_spans[0], mcp_span.span_id)`),
  not co-membership, over a trace fetched by an id the test injects — and its
  first assertion, `assert api_spans, "payments-api span is not in the same
  trace as the MCP span"`, is exactly the shape a dropped traceparent produces.
  `scripts/smoke-test.sh`'s final `pytest "$ROOT/tests" -v` runs the whole
  `tests/` tree, so it is gated. (Cited as `:229` until M4's final review; that
  line is a comment. The fix cited a *distance* instead — "112 lines further
  down", which was itself wrong by 20 — so it is now cited by the text of the
  line, which is what survives an edit above it.)
- **Nothing catches it before that.** `tools/payments-mcp/tests/test_pins.py`
  says nothing about `opentelemetry-sdk` — none of the five per-service pin
  guards does, except `apps/reviewer-ui/tests/test_pins.py:47`, which asserts
  `== "1.44.0"` for an unrelated reason. So the bump is invisible at the point
  it is made, in a one-line `requirements.txt` edit, and surfaces only in an
  expensive test that needs a live cluster and whose failure message names a
  missing span rather than a flag byte.

That gap is a unit-level guard on `payments-mcp`'s pin, plus a comment saying
why `1.29.0` is load-bearing. **This document does not build it** — recording
it is this entry's job, and the guard is a code change for whoever takes it.

> **2026-09-11 (M3): built, pointing the other way.**
> `tools/payments-mcp/tests/test_pins.py` now asserts
> `importlib.metadata.version("opentelemetry-sdk") == "1.44.0"`, with a
> docstring carrying the reasoning above. It is not the guard this paragraph
> imagined — that one would have pinned 1.29.0 *down*, and the direction taken
> was forward instead, to the current release, with the Go end fixed rather
> than the Python end held back. What the paragraph actually asked for is what
> shipped: the pin is no longer invisible at the point of edit.

**Why it matters, beyond this project.** Any fleet mixing OpenTelemetry Python
≥ 1.4x with OpenTelemetry Go ≤ v1.41.0 has this on every Python→Go hop, and
the spec boundary guarantees it will keep appearing: Level 2 tells senders to
set a bit that Level 1 receivers were told to reject. The generic lesson is the
one about *strictness at a version boundary* — a validator that refuses an
unrecognised flag bit, rather than ignoring it, converts a forward-compatible
spec extension into silent data loss. And the loss is silent in the worst
direction: a rejected parent does not orphan a span, which a suite might
notice; it starts a **second trace**, which looks perfectly healthy from every
angle except the one that asks whether the two are the same trace.

**What a fix would need to do — and upstream already did it.** Widen the
extractor to accept the flag bits the spec now defines, and preserve rather
than discard the bitmask. That is exactly what landed.

**Existing upstream activity (verified 2026-09-11, read-only).**

- **`open-telemetry/opentelemetry-go` PR
  [#7834](https://github.com/open-telemetry/opentelemetry-go/pull/7834)**,
  *"Feat: Have SpanContext support the new W3C random flag"* — **merged
  2026-03-04**, released in **v1.42.0** (2026-03-06). Confirmed two ways: the
  release's own `CHANGELOG.md` in the local module cache lists, under
  v1.42.0's **Fixed** section, *"Preserve W3C TraceFlags bitmask (including
  the random Trace ID flag) during trace context extraction and injection in
  `go.opentelemetry.io/otel/propagation`. (#7834)"*; and the GitHub PR page
  was read directly (no comment, review or reaction was posted).
- **Read in the module cache rather than taken from the changelog:**
  `otel@v1.41.0/propagation/trace_context.go:107` is `opts[0] > 2`;
  `otel@v1.42.0/propagation/trace_context.go:107` is `opts[0] > 3`. Same file,
  same line number, one character apart.
- **Why this project could not take it, stated as a version fact rather than a
  preference — and how that was resolved.** `otel@v1.42.0/go.mod` declares
  `go 1.25.0`, and v1.42.0's changelog says **"Drop support for Go 1.24.
  (#7984)"** while v1.41.0's says **"This release is the last to support Go
  1.24. The next release will require at least Go 1.25."**
  **2026-09-11 (M3):** this bullet used to close *"`systems/payments-api/go.mod`
  declares `go 1.24.0` and its `Dockerfile:1` builds `FROM golang:1.24-alpine`;
  the host toolchain is go1.24.10 and `scripts/smoke-test.sh:183` runs the Go
  tests under `GOTOOLCHAIN=local`. So adopting the fix is a **Go toolchain
  bump, not a module bump** — carried debt, recorded rather than taken."*
  Every clause of that was true when written. The debt is paid: `go.mod`
  declares `go 1.25.0` with `toolchain go1.27.1`, the Dockerfile builds on
  `golang:1.27.1-alpine`, and the gate runs the Go suite under
  `GOTOOLCHAIN=auto` — which is the change that actually mattered, because
  `local` is what made the toolchain a property of the operator's PATH instead
  of this repository's. The Go suite is unchanged at 31 tests, passing.
  One thing the bump forced that this finding did not predict:
  `otel/sdk` v1.46.0's `resource/builtin.go:16` imports `semconv/v1.43.0`, and
  `resource.Merge` refuses two different schema URLs, so `telemetry.go`'s
  `semconv/v1.39.0` import had to move with it — left alone it crash-looped
  the pod with *"initialising tracing: building resource: conflicting Schema
  URL: https://opentelemetry.io/schemas/1.43.0 and .../1.39.0"*.
- No issue was searched for or filed on `opentelemetry-python`, because there
  is nothing to report there: setting `0x02` is what Level 2 requires, and the
  Python SDK is correct.

**What this does NOT establish, stated so the finding is not overread.** It
does not establish that any *other* Python→Go hop in this system is currently
broken — only `reviewer-ui` was measured, and it is fixed. It does not
establish what flag byte agentgateway v1.5.0 forwards to `payments-mcp`
(above). It does not establish behaviour for otel-go versions between the two
read, nor for any non-HTTP propagator. And it says nothing about what happens
under a **non-sampled** parent: every measurement above was taken on a sampled
trace, where the flag byte is `01` or `03`; the `00`/`02` pair was never
exercised, and by the source read above `opts[0] > 2` would reject `02` the
same way.

**How we worked around it, and what we did not do.** *(Historical: the
workaround described in this section was deleted on 2026-09-11 when
payments-api reached otel-go v1.46.0, which is exactly the exit condition the
section's last sentence names. Kept because the reasoning is the record, and
because a reader hitting this defect on a pinned otel-go still needs it. What
replaced it is at the end of this section.)*
`apps/reviewer-ui/bootstrap.py`'s `_Level1CompatibleTraceContextPropagator`
masks the **injected** flag byte to `SAMPLED`, by delegating to the upstream
propagator with a masked `NonRecordingSpan` context rather than by formatting
the header itself — so the wire format stays upstream's and only the flag byte
is ours. Trace id, span id, `tracestate` and **all extraction** are untouched;
the service still accepts a Level 2 parent from anyone
(`tests/test_bootstrap.py::test_extraction_is_untouched_and_still_accepts_a_level_2_parent`).
The only thing lost is an advisory bit nothing in this system reads. It is a
workaround, not a position on the spec.

Deliberately **not** done: rewriting the flag byte in the collector (which
would make this project's telemetry disagree with what its services actually
emit — the same no-invented-telemetry rule Finding 7 applies to agentgateway's
attributes), and pinning the other three 1.44.0 services down to 1.29.0 to
match `payments-mcp` (which would trade a known, guarded, one-hop workaround
for an unmeasured downgrade across four services).
`apps/reviewer-ui/tests/test_bootstrap.py` pins the workaround from both
sides, including its *premise* — that the installed SDK really does still set
`0x02` — so the day that stops being true the test goes red and points the
reader at the deletion. **The exit condition is exact: delete the workaround
and that test file the moment `payments-api` is on otel-go ≥ v1.42.0.**

**2026-09-11 (M3): the exit condition was met and acted on.** payments-api is
on otel-go v1.46.0. `_Level1CompatibleTraceContextPropagator` is gone and
`bootstrap.py` registers the stock `TraceContextTextMapPropagator`;
`apps/reviewer-ui/tests/test_bootstrap.py` survives rather than being deleted
outright, because the *positive* claim became pinnable for the first time —
its two remaining tests assert that the registered trace-context propagator is
upstream's own class and not a subclass of it (`type(...) is`, deliberately,
since the retired workaround *was* a `TraceContextTextMapPropagator` and would
pass an `isinstance` check), and that the injected flag byte is now `03`. That
file's docstring names all five retired tests. Re-measured on the live cluster
on the day of the deletion, the same two-request experiment recorded above:

```
00-df5aa13c6d534c0f9c36ef5c187d014d-31966bf1692149b0-01
  -> payments-api GET /payments SERVER da5830097b2aa97e, parent 31966bf1692149b0
00-9b855ee5e0a442619dc8d48118324644-51665f1dbec74c0b-03
  -> payments-api GET /payments SERVER ca981a384c75fc22, parent 51665f1dbec74c0b
```

The `-03` request is the one that used to produce no span at all. Measured
inside the deployed reviewer-ui pod, so the byte above is the one the service
really sends: `get_global_textmap()._propagators` is
`['TraceContextTextMapPropagator', 'W3CBaggagePropagator']`, injecting
`00-4fd1495b2de7214c10469a745ba986aa-cdb290f086f73d06-03`. And end to end,
one live `GET /` against reviewer-ui — trace
`eacff63cc15a0868cee60332b1728735`: `northstar.review.queue` SERVER
`e40bafb90b4a5483` (root) → reviewer-ui `GET` CLIENT `9280bc1b88bb4120` →
payments-api `GET /payments` SERVER `a418e6258a9c1bac`.

**Status: nothing filed, nothing asked upstream, and nothing to file on the Go
side.** No issue, comment, pull request, review or discussion post was made to
any upstream repository or tracker as part of recording this; PR #7834 and the
module cache were read only. The Go half is Finding-5-shaped and
Finding-7-shaped both: real in the version this project runs, already fixed
upstream, and blocked here on a toolchain rather than on upstream. The Python
half is not a defect. What remains unreported anywhere is the *interop*
observation — that the Level 1/Level 2 boundary drops trace context silently
between two current, correct OpenTelemetry SDKs — and whether that is worth
raising with either project is a decision for this project's maintainers, not
one an implementation task takes unilaterally.

---

## Finding 12: of the twelve GenAI metrics the conventions define, the four frameworks in this system emit exactly one between them — and three of them create no instrument at all

**The problem, plainly:** `open-telemetry/semantic-conventions-genai` defines
twelve GenAI metrics, all at Development stability — token usage, operation
duration, per-agent invocation duration, inference-call and tool-call counts,
tool-execution duration, and server-side latencies. A system built from an
agent framework, an agent-to-agent protocol SDK and two MCP libraries emits
**one** of them: `gen_ai.client.token.usage`. Three of the four packages
contain no OpenTelemetry metrics instrumentation whatsoever — not a partial
implementation, not a gated one, no `get_meter(` call anywhere in the
distribution. Their spans are instrumented; their metrics surface does not
exist.

The practical consequence is specific and not obvious from the span coverage:
there is no metrics-only path to "how long does an agent invocation take", "how
many tool calls does a run make", or "how long do tool executions take". Those
questions are answerable only by reconstructing them from spans, which requires
a tracing backend and a join, and which is exactly what the metric definitions
exist to make unnecessary.

**Projects and versions:** `pydantic-ai-slim==2.42.0`, `a2a-sdk==0.3.7`,
`fastmcp==4.0.3`, `mcp==2.2.0` — the versions this repository pins and runs.

**What the convention says should happen:** read 2026-09-13 from
`open-telemetry/semantic-conventions-genai` at `main` (`0c87594975`), file
`docs/gen-ai/gen-ai-metrics.md` (last changed `8d3e4a0f3c34`, 2026-08-10).
Note the repository: the GenAI conventions are no longer in the main
`open-telemetry/semantic-conventions`, whose `docs/gen-ai/README.md` and
`docs/gen-ai/gen-ai-metrics.md` are now stubs reading *"GenAI semantic
conventions have moved to the OpenTelemetry GenAI semantic conventions
repository. This page has moved and is no longer maintained in this
repository."* Both old paths still return HTTP 200, so a stale link leads to an
unmaintained page rather than to an error.

The six this project measured against (M4 spec §6's table), all Histogram, all
**Development**:

| Metric | Unit |
|---|---|
| `gen_ai.invoke_agent.duration` | `s` |
| `gen_ai.invoke_agent.inference_calls` | `{inference_call}` |
| `gen_ai.invoke_agent.tool_calls` | `{tool_call}` |
| `gen_ai.execute_tool.duration` | `s` |
| `gen_ai.client.token.usage` | `{token}` |
| `gen_ai.client.operation.duration` | `s` |

**What we observed instead.** Two independent measurements, at two different
layers, agreeing.

*(1) At the source.* Every `.py` file in each installed distribution, scanned
for the six names and for any metrics instrumentation at all:

```
pydantic_ai (2.42.0): 328 .py files | get_meter( x1 | create_<instrument>( x5
   gen_ai.invoke_agent.duration               ABSENT
   gen_ai.invoke_agent.inference_calls        ABSENT
   gen_ai.invoke_agent.tool_calls             ABSENT
   gen_ai.execute_tool.duration               ABSENT
   gen_ai.client.token.usage                  ['models/instrumented.py']
   gen_ai.client.operation.duration           ABSENT

a2a (a2a-sdk 0.3.7):   80 .py files | get_meter( x0 | create_<instrument>( x0
   (all six ABSENT)

fastmcp (4.0.3):      257 .py files | get_meter( x0 | create_<instrument>( x0
   (all six ABSENT)

mcp (2.2.0):          123 .py files | get_meter( x0 | create_<instrument>( x0
   (all six ABSENT)
```

*(2) At runtime, in the deployed images.* PydanticAI's instrumentation settings
constructed inside the running pod, and every instrument it builds enumerated:

```
pydantic_ai version: 2.42.0
INSTRUMENTS PydanticAI CREATED:
    ('cost_histogram', 'operation.cost', '{USD}', '_Histogram')
    ('time_to_first_chunk_histogram', 'gen_ai.client.operation.time_to_first_chunk', 's', '_Histogram')
    ('tokens_histogram', 'gen_ai.client.token.usage', '{token}', '_Histogram')
```

Three instruments, and the `x5` above is not in tension with that: it counts
`create_histogram(` **call sites**, and two of the five are `except TypeError`
fallbacks for older OTel API versions
(`pydantic_ai/models/instrumented.py:175/181` for `tokens_histogram`,
`:195/201` for `time_to_first_chunk_histogram`) — two ways to create the same
instrument, not two instruments. Read 2026-09-13 at the installed 2.42.0. One
is a convention metric this system records into
(`gen_ai.client.token.usage`); one is a convention metric recorded only for
*streaming* requests, which this system does not make, so it exists and takes
no measurement; one (`operation.cost`) is PydanticAI's own name in no OTel
namespace at all.

**Why this is not a measurement artefact, stated because a false negative here
would be the expensive kind of wrong.** **The five services given a
`MeterProvider`** — `settlement-agent`, `claims-agent`, `payments-mcp`,
`reviewer-ui` and `policy-decision-service`, the exact five whose wiring
`tests/unit/test_metrics_wiring.py` asserts is identical — each had its export
path demonstrated working in the same window before any absence was recorded.
**All four framework-hosting services are inside that set**, which is what the
claim needs: `pydantic_ai` runs in `settlement-agent`, `a2a-sdk` in
`claims-agent` and `settlement-agent`, `fastmcp` and `mcp` in `payments-mcp`.

(This read "**every service in the system**" until M4's final review. It was
true when M4 Task 2 wrote it and was falsified inside this same branch two
tasks later: `platform/eval-scorer` is a sixth Python service with **no**
MeterProvider — `scorer/emit.py` says so in its own words, and
`tests/unit/test_metrics_wiring.py` asserts the absence — and
`systems/payments-api` is Go and has no meter at all. Neither hosts any of the
four frameworks, so the finding's *result* is untouched. The *argument* was
overstated, and this is the paragraph whose entire job is to make a public
assertion about four third-party libraries safe to stand behind — an
overstated safety argument here is worse than a narrower true one, and M4 Task
5's stale-prose pass read this file without noticing that the milestone's own
new service had falsified it.)

The evidence, in the order the data crosses it:

* SPIRE's 271 metric names reach the same Prometheus through the same
  collector, with every Prometheus scrape job disabled, so the
  collector→Prometheus hop is proved by data no Python service produced.
* `settlement-agent`, `claims-agent`, `payments-mcp` and `reviewer-ui` each have
  their own series in Prometheus under their own `job` label.
  `payments-mcp` is the decisive one: tool execution happens in that process,
  under FastMCP, so that is where `gen_ai.execute_tool.duration` would be
  recorded — and other metrics from that same process arrive.
* `policy-decision-service` records nothing of its own, so a probe counter was
  sent from inside its pod through its own telemetry module, and arrived.
* The collector's and every service's logs were read over their **whole**
  windows (`--since` covering pod start, never a `--tail`): zero export
  failures, zero errors, zero warnings.

The full transcript is in
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "Metrics, as measured
(M4 Task 2)".

**Why it matters, beyond this project:** the GenAI metric set is what an
operator would build a cost and latency SLO on without needing a trace store.
With only `gen_ai.client.token.usage` available, per-model token volume is
measurable and *agent* behaviour is not: no invocation duration, no tool-call
rate, no tool-execution latency. For MCP servers the gap is total — FastMCP and
the reference `mcp` package emit no metric at all, so a fleet of MCP servers has
no metric-level health signal of its own, only spans. And because every one of
these metrics is at **Development** stability, an implementer today is aiming at
a target that can still be renamed, which is a plausible part of why coverage is
thin.

**What a fix would need to do:** for PydanticAI, create and record
`gen_ai.invoke_agent.duration`, `.inference_calls` and `.tool_calls` around the
agent-run it already creates an `invoke_agent` *span* for, and
`gen_ai.client.operation.duration` alongside the existing client histograms —
the timing data is already computed for the spans. For FastMCP and `mcp`, the
work is larger because there is no meter at all: a `MeterProvider`-derived meter
and `gen_ai.execute_tool.duration` around the tool-invocation path their spans
already wrap.

**How we worked around it, and whether it should be adopted upstream:** we did
not. No `gen_ai.*` metric was hand-rolled in this project to fill the gap, for
the same reason Finding 2 gives for not hand-setting `gen_ai.operation.name`
on FastMCP's spans: an invented metric under a convention namespace makes this
project's telemetry disagree with what a future, fixed framework emits
natively. Two tests enforce that, at the two layers it takes — and the split is
stated rather than glossed, because an earlier version of this paragraph cited
only the first and claimed more for it than it can do (M4 Task 2 review finding
F5). `tests/e2e/test_prometheus_metrics_ingest.py::
test_no_metric_was_invented_to_fill_a_framework_gap` asserts that Prometheus
holds **no `gen_ai.*` name beyond the one PydanticAI emits**; it reads a store,
so it can see what a series is called and never who authored it.
`tests/unit/test_metrics_wiring.py::
test_no_service_authors_a_metric_under_a_convention_namespace` is the half that
sees origin: it walks the AST of every Python file under `agents/`, `apps/`,
`platform/`, `tools/` and `systems/` for an instrument created with a
`gen_ai.`-prefixed name, and needs no cluster. There is no workaround to
recommend; the correct fix is for the frameworks to emit the metrics.

**Existing upstream activity (verified live, 2026-09-13, read-only — no issue,
comment or pull request was posted anywhere):**

* [`PrefectHQ/fastmcp#3733`](https://github.com/PrefectHQ/fastmcp/issues/3733)
  ("Support mcp.server.* OTEL metrics") — **open**, filed by a third party
  (`verdie-g`), assigned to a maintainer (`strawgate`), and exactly on point:
  its entire body asks FastMCP to "Emit OTEL metrics following the OTEL semconv
  for MCP server". Independent confirmation that FastMCP's metrics surface does
  not exist and that upstream knows.
* [`PrefectHQ/fastmcp#3887`](https://github.com/PrefectHQ/fastmcp/issues/3887)
  ("OTEL: Instrument all MCP methods, not just tools/call, resources/read,
  prompts/get") and
  [`#3891`](https://github.com/PrefectHQ/fastmcp/issues/3891) ("OTEL: Instrument
  ctx.sample() / sampling loop with spans and metrics") — both **open**, both
  adjacent.
* [`pydantic/pydantic-ai#6204`](https://github.com/pydantic/pydantic-ai/issues/6204)
  ("Option to emit `gen_ai.agent.name` as a dimension on GenAI client metrics")
  — **open**, filed and assigned to a maintainer (`dmontagu`). It does not ask
  for the missing metrics, but it **corroborates this finding's central
  measurement from the maintainers' own side**, naming the complete set as
  "the existing `gen_ai.client.token.usage` and `operation.cost` histograms"
  plus the streaming `gen_ai.client.operation.time_to_first_chunk` — the same
  three instruments enumerated above, and no others.
* **And it supplies the reason, which changes how this finding should be
  read.** That issue states, as PydanticAI's position: *"The semconv metric
  attribute group for GenAI client metrics deliberately excludes agent identity
  today; `gen_ai.agent.name` is only specified for spans (`invoke_agent`)"*, and
  separately that *"per-agent aggregation on the platform comes from spans
  (`model_calls` joined to agent runs), which is higher fidelity"*. So the
  absence of the `gen_ai.invoke_agent.*` metrics from PydanticAI is at least
  partly a **deliberate architectural position** — agent-level telemetry as a
  span concern — and not simply an unnoticed omission. That is recorded here
  rather than left out because it is the strongest available argument *against*
  this finding, and a finding that omits it would be advocacy rather than
  evidence.
* No open issue was found in any of the four trackers asking specifically for
  `gen_ai.invoke_agent.duration`, `.inference_calls`, `.tool_calls` or
  `gen_ai.execute_tool.duration` by name. Searched
  `pydantic/pydantic-ai`, `PrefectHQ/fastmcp` and `a2aproject/a2a-python` for
  each of those names and for "metrics"/"OpenTelemetry", open and closed,
  issues and pull requests.

**Status: not filed by this project.** The FastMCP half is already upstream,
filed by someone else, open and precisely on point, so a second issue would
duplicate it. The PydanticAI half is a gap this project measured and upstream
has not been asked about by name — and filing is a decision this project
reserves for its maintainers, not one an implementation task takes
unilaterally, exactly as Findings 1-11 record. Nothing was posted to any
tracker; every issue above was read, never written to. No workaround was added
to this project's own code in the meantime.

---

## Finding 13: OpenFeature's own Python packages disagree with each other on which OTel signal carries `feature_flag.evaluation` — and on every attribute's spelling

**The problem, plainly:** Two packages under the same `open-feature`
GitHub organization, both installed by this project, both claiming to
implement the same specified telemetry event, disagree with each other about
which OpenTelemetry signal that event belongs on and how its attributes
should be spelled. `openfeature-hooks-opentelemetry`'s `TracingHook` — the
only one of the two actually wired into anything, in this project or (as far
as could be found) anywhere — puts `feature_flag.evaluation` on the active
**span**, as a span event, and cites the OTel **trace** semantic conventions
as the reason. `openfeature-sdk` itself ships a second, complete, unused
event-builder for the identical event name, built for a **log record**, and
cites the OTel **logs** semantic conventions in its own class docstrings —
added to the core SDK for exactly this purpose over a year before the hook
package's most recent release, and never called by it. A downstream author
who trusts either artifact's own citation, in isolation, would build
something the other artifact disagrees with.

**Project and version:** `openfeature-hooks-opentelemetry==0.3.1` (PyPI,
`open-feature/python-sdk-contrib`, installed at
`platform/policy-decision-service/.venv/lib/python3.13/site-packages/openfeature/contrib/hook/opentelemetry/__init__.py`)
and `openfeature-sdk==0.10.0` (PyPI, `open-feature/python-sdk`, installed at
`.../site-packages/openfeature/telemetry/{__init__,attributes,body,metadata}.py`
in the same venv) — the exact two packages this project's `pds/autonomy.py`
already depends on for the `AutonomyResolution` machinery Task 3 built.

**Where the behaviour lives, read directly from both installed packages:**

*The hook (span event, actively used).* `TracingHook.finally_after` hand-builds
an attribute dict and ends with `current_span.add_event(OTEL_EVENT_NAME,
event_attributes)` — a span event, full stop; no log record is ever
constructed. Its package's own README states the justification in one
sentence: *"a [spec compliant][otel-spec] way to automatically add a feature
flag evaluation to a span as a span event,"* with `[otel-spec]` resolving
to `https://opentelemetry.io/docs/reference/specification/trace/semantic_conventions/feature-flags/`
— the **trace**-side semantic conventions page, cited by the package itself.

*The SDK's own sibling utility (log record, unused).* `openfeature/telemetry/__init__.py`
defines `create_evaluation_event(hook_context, details) -> EvaluationEvent`,
returning a dataclass with **separate `attributes` and `body` fields** — the
log-record data model, which has no equivalent split for a span event — and
its sibling module `openfeature/telemetry/body.py` states outright: *"OpenTelemetry
event body fields. See: https://opentelemetry.io/docs/specs/semconv/feature-flags/feature-flags-logs/"*
— the **logs**-side page, a different URL from the one the hook cites.
`openfeature/telemetry/metadata.py`'s `TelemetryFlagMetadata` cites a third
page, `https://openfeature.dev/specification/appendix-d/#flag-metadata`
(OpenFeature's own spec, not OTel's). A repository-wide grep of the entire
venv, `grep -rln "create_evaluation_event\|EvaluationEvent"`, returns exactly
one file: the definition itself. Nothing in `openfeature-hooks-opentelemetry`
0.3.1 — the only hook package this project or, per the tracker search below,
anyone else has published — calls it.

*Provenance of the unused half, confirmed via the tracker rather than
assumed:* [`open-feature/python-sdk#448`](https://github.com/open-feature/python-sdk/issues/448)
("Add OpenTelemetry-Compatible Telemetry Utility Function", closed) asked for
"a telemetry utility function... to create OpenTelemetry-compatible telemetry
signals," explicitly requiring it to "adhere to... [Appendix D] of the
OpenFeature Specification and the [OpenTelemetry Semantic Conventions for
Feature Flags]" — linking the same `feature-flags-logs` URL found in
`body.py` above — modeled on a sibling JS-SDK implementation.
[`#451`](https://github.com/open-feature/python-sdk/pull/451) ("feat: add OTel
utility function") merged **2025-02-22**, closing it. `openfeature-hooks-opentelemetry`'s
two most recent releases, v0.3.0 (2025-12-18) and v0.3.1 (2026-02-17), both
postdate that merge by ten months and a year respectively — there was ample
time to adopt it, and neither release does.

**What the convention says should happen:** the OpenFeature specification's
own "signal emission patterns" section
(`https://openfeature.dev/specification/appendix-d/#signal-emission-patterns`)
allows more than one shape here — confirmed via
[`open-feature/python-sdk-contrib#363`](https://github.com/open-feature/python-sdk-contrib/issues/363)
("Add Span Hook", **open**, filed by a third party, `danjuv`), whose own body
states *"[the spec] specifies emitting spans as an option... The js-sdk
supports this already, but the rest of the SDKs don't."* Note precisely what
`#363` is and is not: it asks for a hook that creates a whole **span**
(matching the JS SDK's separate `traces` hook), which is a third shape,
distinct from both artifacts measured here — it does not resolve, or even
mention, the span-event-vs-log-record disagreement this finding is about.

**What we observed instead:** read directly, not inferred — the two packages'
own attribute spellings for the same four fields, none of which is an
invented name; all eight cited values below came off this project's own
installed `.venv`, cross-checked against
`opentelemetry.semconv._incubating.attributes.feature_flag_attributes`
(the same module Task 4's `DEPRECATED_SPELLINGS` derivation reads):

| Field | Current OTel semconv | `TracingHook` (span event, used) | `openfeature.telemetry` (log-shaped, unused) |
|---|---|---|---|
| provider name | `feature_flag.provider.name` | `feature_flag.provider.name` (current) | `feature_flag.provider_name` (**deprecated**) |
| variant | `feature_flag.result.variant` | `feature_flag.result.variant` (current) | `feature_flag.variant` (**deprecated**) |
| reason | `feature_flag.result.reason` | `feature_flag.result.reason` (current) | `feature_flag.evaluation.reason` (**deprecated**) |
| error message | `feature_flag.error.message` | `error.message` (**neither current nor deprecated**) | `feature_flag.evaluation.error.message` (**deprecated**) |

The right-hand column's three "deprecated" cells and the left-hand column's
four "current" cells are not this project's characterization — they are the
literal `Final`/`@deprecated`-marked values already read out of the installed
`opentelemetry-semantic-conventions==0.65b0` package for Task 4's own guard
test (`tests/unit/test_autonomy_contract.py`). The hook gets three of four
right and invents the fourth (`error.message`, off both the current
and the deprecated OTel spelling — it is simply a different, unnamespaced
key). The SDK's own unused utility, despite citing the correct current-spec
page in its own docstring, uses the deprecated spelling on all three
attributes where a deprecated alternative exists.

**Why it matters, beyond this project:** anyone consuming
`feature_flag.evaluation` downstream — a query, a dashboard panel, an alert —
has to know in advance which OTel signal to look on, because OpenFeature's own
Python ecosystem does not agree with itself, and cannot query by the current
attribute names alone, because the one utility that already reads
`flag_metadata` (see Finding 14) spells three of four shared attributes the
deprecated way. A vendor whose backend supports log-based events well and
span-events poorly (or vice versa — the spec's own `#363` names this
directly) gets a worse experience purely from which OpenFeature package an
integrator happened to install, not from any choice available to them.

**What a fix would need to do:** either `TracingHook` adopts
`create_evaluation_event()` (fixing its error-message key and aligning its
signal choice with the SDK's own logs-cited utility), or the SDK's utility is
updated to current spellings and the hook is left as the span-event path,
with the package's own README stating which of the spec's several emission
patterns it implements and why — either resolution is legitimate; what is not
is two co-maintained packages each citing a different OTel page as their
compliance target for the identical event name.

**How we worked around it, and whether it should be adopted upstream:** this
project's own `pds/autonomy.py::emit_evaluation_event` (Task 4) took neither
package's attribute spelling on faith — it imports every key directly from
`opentelemetry.semconv._incubating.attributes.feature_flag_attributes` (the
**current** spellings only) and emits a genuine log record via
`opentelemetry._logs`/`opentelemetry.sdk._logs`, deliberately choosing the
signal `openfeature.telemetry`'s own citations argue for, independent of
either OpenFeature package's own inconsistency. `TracingHook` remains
registered too (Task 3), on the active span, exactly as installed and
unmodified — this project relies on it for the span-event signal
(`tests/test_autonomy.py::test_the_tracing_hook_puts_a_feature_flag_evaluation_event_on_the_active_span`)
and does not attempt to patch its attribute spelling or its error-message key
at runtime. Two records per evaluation, correctly attributed on one side and
inherited as-is, imperfections included, on the other — recorded rather than
silently patched, because patching a third-party hook's own span event in
place would be exactly the kind of invented-behind-the-library's-back fix
this project's governing rule rules out.

**Existing upstream activity (verified live against the tracker, read-only,
2026-09-14 — no issue, comment or pull request was posted anywhere as part of
writing this):** beyond `#363` and `#448`/`#451` cited above, a further search
of `open-feature/python-sdk-contrib` for "log record", "feature_flag.version"
and "error.message" (issues and pull requests, matched against title and body)
returned no results — no open issue asks the hook to adopt the SDK's own
utility, or to fix its error-message key, or names the signal-choice
disagreement this finding describes.

**Status: not filed by this project.** No open issue covers the specific gap
found here — that two co-maintained packages disagree on signal and spelling
for the same event — and `#363` is adjacent but distinct (a third, span-scoped
shape, not a resolution of the span-event/log-record split). Recorded
filing-ready: both installed versions, the exact citations each makes, and the
attribute table above.

---

## Finding 14: `TracingHook` declares `feature_flag.set.id` and `feature_flag.version` as constants and never emits either — while its own SDK's sibling utility already reads them

**The problem, plainly:** `openfeature-hooks-opentelemetry`'s `EventAttributes`
class names all eight attributes its `TracingHook` might plausibly emit,
including `SET_ID = "feature_flag.set.id"` and `VERSION = "feature_flag.version"`.
`finally_after` — the one method that builds the event — references six of
those eight names and never once references `EventAttributes.SET_ID` or
`EventAttributes.VERSION`. Both fields come from a flag's own `flag_metadata`,
which `FlagEvaluationDetails` already carries into the hook on every call;
`finally_after` has the data sitting in `details.flag_metadata` and does not
read it for these two keys, though it does read `details.variant`,
`details.reason`, and three others from adjacent parts of the same object on
the very same code path.

**Project and version:** `openfeature-hooks-opentelemetry==0.3.1`, the same
package and installed file as Finding 13.

**Where the behaviour lives:** `TracingHook.finally_after`, read in full —
every attribute it sets:

```python
event_attributes = {
    EventAttributes.KEY: details.flag_key,
    EventAttributes.RESULT_VALUE: json.dumps(details.value),
    EventAttributes.RESULT_REASON: str(details.reason or Reason.UNKNOWN).lower(),
}
if details.variant:
    event_attributes[EventAttributes.RESULT_VARIANT] = details.variant
if details.reason == Reason.ERROR:
    ...
context = hook_context.evaluation_context
if context.targeting_key:
    event_attributes[EventAttributes.CONTEXT_ID] = context.targeting_key
if hook_context.provider_metadata:
    event_attributes[EventAttributes.PROVIDER_NAME] = hook_context.provider_metadata.name
```

`details.flag_metadata` — the object `FlagEvaluationDetails` defines
specifically to carry provider-supplied metadata such as a flag's set ID and
ruleset version — is never named in this method at all. A repository grep of
the installed hook package for `flag_metadata` returns zero matches.

**What the convention says should happen:** OpenFeature's own specification,
Appendix D (`https://openfeature.dev/specification/appendix-d/#flag-metadata`)
— cited, as noted in Finding 13, directly inside this same SDK's
`openfeature/telemetry/metadata.py` — documents `flagSetId` and `version` as
well-known `flag_metadata` keys specifically for telemetry events. The OTel
feature-flag semantic conventions (`feature_flag.set.id`, `feature_flag.version`,
both confirmed present and current in the installed
`opentelemetry-semantic-conventions==0.65b0`) name the identical two
attributes as the wire spelling for exactly this data once resolved.

**What we observed instead:** two independent pieces of evidence, neither
requiring speculation about what `TracingHook` was "supposed" to do:

1. *Read directly, in the previous section:* `finally_after`'s own attribute
   dict, quoted in full above, has no `SET_ID` or `VERSION` key under any
   circumstance — not gated behind a `None` check that happens to always be
   `None` in this deployment (Task 4's own tension, addressed on its own
   emission path — see `pds/autonomy.py`'s module comment on ruling M5-R5),
   but genuinely absent from the code that builds the dict, regardless of
   what `flag_metadata` contains.
2. *The sibling utility already does this correctly*, read directly from
   `openfeature/telemetry/__init__.py::create_evaluation_event` (Finding 13):
   `if set_id := details.flag_metadata.get(TelemetryFlagMetadata.FLAG_SET_ID):
   attributes[TelemetryAttribute.SET_ID] = ...` and the identical pattern for
   `TelemetryFlagMetadata.VERSION`. The SDK this hook depends on
   (`openfeature-sdk>=0.8.4` per the hook's own `METADATA`) has shipped a
   correct, working reference implementation of exactly the read
   `TracingHook` omits, since 2025-02-22 (Finding 13's `#451`) — nine months
   before the hook package's own most recent minor release.

Against this project's own live deployment specifically: `resolution.set_id`
and `resolution.version` measure `None` for every real evaluation (Task 3's
measurement, re-confirmed by Task 4 — `featureflag.yaml`'s `autonomy-level`
flag defines no `flag_metadata`), so `TracingHook`'s omission happens to cost
nothing observable in this deployment today. It would cost something the
moment any flag in this system's flagd configuration defined a
`flag_metadata` block, silently: no error, no warning, the two fields simply
never appearing on the span event, exactly the shape every other finding in
this document about a silent gap takes.

**Why it matters, beyond this project:** an operator relying on
`TracingHook`'s span event to answer "which ruleset version produced this
decision" or "which flag set was this flag part of" — precisely the audit
question this project's own Task 5 needs answered — gets neither, for any
flag that defines them, regardless of provider, and with no indication
anything is missing. The gap is not a hard problem: the fix already exists,
tested and shipped, four lines long, in a sibling module of the same SDK
this hook already imports.

**What a fix would need to do:** two `if` blocks in `finally_after`, reading
`details.flag_metadata.get("flagSetId")` and `.get("version")` and setting
`EventAttributes.SET_ID`/`EventAttributes.VERSION` when present — the exact
four lines already written and shipped in
`openfeature/telemetry/create_evaluation_event`, ported into the span-event
path rather than invented fresh.

**How we worked around it, and whether it should be adopted upstream:** this
project's own `pds/autonomy.py::emit_evaluation_event` (Task 4) reads both
fields itself, off the same `AutonomyResolution.set_id`/`.version` Task 3's
`resolve_autonomy` already carries (ruling M5-R5), and includes them in its
own log record when not `None` — this project did not patch `TracingHook` at
runtime to add the missing read; it emits the complete record itself, on the
second of the two signals Task 4 wires, leaving the span event exactly as the
installed hook produces it. The consequence for this deployment specifically
is stated in `pds/autonomy.py`'s own module comment and carried forward to
Task 5: reading `feature_flag.version` off `TracingHook`'s span event will
find it absent for every real evaluation today (the flag defines no
`flag_metadata`, independent of this gap), and Task 5's audit is specified
(ruling M5-R6) to resolve the current contract version from flagd directly
rather than depend on either event carrying it.

**Existing upstream activity (verified live against the tracker, read-only,
2026-09-14 — no issue, comment or pull request was posted anywhere as part of
writing this):** a search of `open-feature/python-sdk-contrib` for
"flagSetId", "flag_metadata" (with "version"), and "metadata" (issues and pull
requests, matched against title and body) found nothing naming this specific
omission. The closest related history is
[`#215`](https://github.com/open-feature/python-sdk-contrib/pull/215)
("feat: add support for flagd flag metadata", merged) and
[`#211`](https://github.com/open-feature/python-sdk-contrib/pull/211)
("chore(flagd): Add sync metadata disabled", merged) — both about the
**flagd provider** learning to carry `flag_metadata` at all, not about
`TracingHook` reading it once carried; neither touches
`hooks/openfeature-hooks-opentelemetry`.

**Status: not filed by this project.** No open issue covers `TracingHook`'s
specific omission of `set.id`/`version`, and the fix is small, already proven
correct elsewhere in the same SDK, and filing is — as with every finding in
this document — a decision reserved for this project's maintainers. Nothing
was posted to any tracker. This project's own emission path (`pds/autonomy.py`)
supplies both fields itself rather than waiting on or patching the upstream
hook.

---

## Finding 15: a `$text` placeholder cannot inline a local, git-less file — `type: file` locations reach the resolver as a bare path, and no reader is registered for `file:` anyway

**The problem, plainly:** this project's catalog entities want to point an
API entity's `definition` at the actual A2A agent-card JSON committed in
`contracts/agent-cards/`, inlined rather than restated, which is exactly what
the `$text` placeholder exists for. It does not work for a `type: file`
location with no git remote behind it, for two independent reasons stacked on
top of each other: the URL-resolution step throws before the read step is
ever reached, and the read step would fail anyway because no reader in the
default set understands a local file at all.

**Project and version:** `@backstage/backend-defaults`, declared
`^0.17.7` in `packages/backend/package.json`, resolved and installed as
`0.17.8` (checked directly against `node_modules/@backstage/backend-defaults/
package.json` in this build — the declared range and the installed file are
not the same string, so this finding cites the file actually running, per
this project's own "verify against what shipped, not a stale local index"
habit). `@backstage/plugin-catalog-backend` (installed `3.9.1`) owns
`PlaceholderProcessor`; `@backstage/integration` (installed `2.1.1`) owns
the URL resolver the processor calls through.

**Where the behaviour lives, read in full:**

1. `PlaceholderProcessor.preProcessEntity` (`node_modules/@backstage/
   plugin-catalog-backend/dist/processors/PlaceholderProcessor.cjs.js`) finds
   a `$text` key, resolves the URL via `relativeUrl`, and only then reads it:
   ```js
   function relativeUrl({ key, value, baseUrl, resolveUrl }) {
     ...
     const resolved = resolveUrl(value, baseUrl);   // throws here, first
     assertSameRepository(resolved, baseUrl);
     return resolved;
   }
   ```
2. `resolveUrl` is `ScmIntegrations.resolveUrl` (`node_modules/@backstage/
   integration/dist/ScmIntegrations.cjs.js:88-94`): it looks the location's
   `base` up by host among the registered SCM integrations (GitHub, GitLab,
   Bitbucket, Azure, Gerrit, Gitea, Harness, the two cloud-storage backends);
   a bare filesystem path matches none of them, so it falls through to
   `helpers.defaultScmResolveUrl`.
3. `defaultScmResolveUrl` (`node_modules/@backstage/integration/dist/
   helpers.cjs.js:62-86`) tries `new URL(url)` first (fails — `value` is a
   relative path, e.g. `../../contracts/agent-cards/claims-agent.json`), then
   falls to `new URL(url, base)`. `URL`'s two-argument form requires `base`
   to itself be an absolute URL with a scheme; `base` here is `location.target`,
   which for a `type: file` location is the bare path Backstage read from disk
   (`/app/catalog/agents/claims-agent/catalog-info.yaml`, not
   `file:///app/...`). `new URL` throws `TypeError: Invalid URL` on a base with
   no scheme, unconditionally.
4. Even a location authored with an explicit `file://` URL so step 3 does not
   throw would not reach content: `UrlReaders.default()` (`node_modules/
   @backstage/backend-defaults/dist/entrypoints/urlReader/lib/
   UrlReaders.cjs.js:44-60`) registers exactly twelve factories — Azure,
   Bitbucket Cloud, Bitbucket Server, Gerrit, GitHub, Gitea, GitLab, Google
   GCS, Harness, AWS S3, AWS CodeCommit/Azure Blob Storage, and a generic
   http(s) `FetchUrlReader` — and none of them claims a `file:` scheme.
   `readUrl('file:///...')` would fall through the whole predicate chain with
   no matching reader.

**What we actually saw, live, before either of the two steps above was traced**
(recorded verbatim in `agents/claims-agent/catalog-info.yaml`'s own header
comment, M6 Task 2): with `contracts/agent-cards/claims-agent.json` staged
into the image at `./catalog/contracts/agent-cards/claims-agent.json`, a
`$text: ../../contracts/agent-cards/claims-agent.json` on the API entity
produced, in the running pod's own logs:
```
Processor PlaceholderProcessor threw an error while preprocessing;
caused by Error: Placeholder $text could not form a URL out of
/app/catalog/agents/claims-agent/catalog-info.yaml and
../../contracts/agent-cards/claims-agent.json, TypeError: Invalid URL
```
— exactly step 3 above, reached before step 4 ever became relevant.

**Why it matters, beyond this project:** `$text`/`$json`/`$yaml` are
documented as general-purpose placeholders, and Backstage's own scaffold
ships example locations that are `type: url` against a real SCM host, which
is the one case this code path was built for. A deployment with committed
catalog data and genuinely no git remote to point at — arguably the simplest
possible case, and the one this repo's "no git remote, nothing is backed up"
posture (an open finding of its own, tracked in this project's memory rather
than here) puts it squarely in — cannot use inlining placeholders at all,
with a failure mode (an unhandled `TypeError` surfacing through a generic
"processor threw" wrapper) that names neither the missing scheme support nor
the two-step reason plainly.

**What a fix would need to do:** two independent changes, either sufficient
alone to unblock a well-formed absolute `file://` value but both needed for
the case actually hit here (a location's own `target`, which Backstage itself
populates from disk, being a bare path): (1) `defaultScmResolveUrl` treating a
`base` with no scheme as already-absolute-enough to resolve a relative `value`
against, rather than requiring `new URL(base)` to succeed first; (2) a
`file:`-scheme reader added to `UrlReaders.default()`'s factory list,
presumably gated behind an explicit opt-in given the local-filesystem-access
implications of trusting a `file://` value's path unconditionally.

**How we worked around it, and whether it should be adopted upstream:** this
project did not patch either package. `agents/claims-agent/catalog-info.yaml`
and `agents/settlement-agent/catalog-info.yaml` link the agent card via a
`Component --providesApis--> API` graph edge instead, with the API entity's
`definition` field carrying a written pointer ("see
`contracts/agent-cards/claims-agent.json` in this repo") rather than the
file's actual JSON contents. This keeps the real relationship the catalog
graph is for — an API a component provides, discoverable from the component
page — without pretending the file is inlined when it is not; a reader who
wants the actual contract still has to open the linked file, which is the
honest state of affairs for a placeholder mechanism that cannot reach it. A
custom `coreServices.urlReader` backend module registering a `file:` reader
would be real new backend code, out of this milestone's scope, not a config
change.

**Existing upstream activity (checked read-only via `gh search issues`/
`gh search prs` against `backstage/backstage`, 2026-09-15 — nothing was
opened or commented on as part of writing this):** no open issue or PR names
this specific pairing (a `type: file` location's bare-path `target` failing
`$text` resolution, or the absent `file:` reader). The closest related history
predates the current architecture: `FileReaderProcessor` (issues #7706,
#14042, PR #4534, all 2021–2022) was an entirely different, since-removed
mechanism for reading local files directly, not routed through
`UrlReaders`/`ScmIntegrations` at all — its removal is itself consistent with
this finding rather than contradicting it. Issue #9446 ("Could not read
location from Gitlab repo (`$text` placeholder)") and #12251 ("`$yaml` and
`$text` placeholders behave differently for YAML OpenAPI spec") are the same
placeholder family failing against a *configured* SCM host, a different
failure shape than a `type: file` location with no host to configure.

**Status: not filed by this project.** This is arguably not a defect so much
as a deployment shape (a git-less, committed-file catalog) the placeholder
mechanism was never built for; filing it would be a feature request for
local-file support, a decision reserved for this project's maintainers.
Nothing was posted to any tracker.

---

## Finding 16: the guest auth provider's outside-development gate is correct and by design — and its absence turned a complete, correctly-collated catalog into one nobody could open

**The problem, plainly:** this is not a bug in the sense every other finding
in this document is — `@backstage/plugin-auth-backend-module-guest-provider`
does exactly what it says it does, on purpose, and says so in its own error
message. It is recorded here because that correct, documented default cost a
full task's worth of "everything is green" before the actual blocker
(ruling M6-R7 in this project's own SDD ledger) was found, and because the
gap it leaves — no login at all for a single-operator demo cluster reachable
only through an explicit `kubectl port-forward` — is a real, stated posture
of this deployment, not an accident.

**Project and version:**
`@backstage/plugin-auth-backend-module-guest-provider`, declared and
installed at the identical `0.2.22` (`packages/backend/package.json` and
`node_modules/@backstage/plugin-auth-backend-module-guest-provider/
package.json` agree — no drift to note here, unlike Finding 15).

**Where the behaviour lives, read in full** — both places the same check is
made, once at initialization and once again at sign-in:

```js
// dist/authenticator.cjs.js
initialize({ config }) {
  const allowOutsideDev = config.getOptionalBoolean(
    "dangerouslyAllowOutsideDevelopment"
  );
  return process.env.NODE_ENV !== "development" && allowOutsideDev !== true;
}
...
async authenticate(_, disabled) {
  if (disabled) {
    throw new errors.NotAllowedError(
      "The guest provider cannot be used outside of a development " +
      "environment unless 'auth.providers.guest.dangerouslyAllowOutsideDevelopment' is enabled"
    );
  }
  return { result: {} };
}
```
```js
// dist/resolvers.cjs.js — the same gate, independently, on the sign-in resolver
if (process.env.NODE_ENV !== "development" &&
    config.getOptionalBoolean("dangerouslyAllowOutsideDevelopment") !== true) {
  throw new errors.NotImplementedError(
    "The guest provider is NOT recommended for use outside of a " +
    "development environment. If you want to enable this, set " +
    "`auth.providers.guest.dangerouslyAllowOutsideDevelopment: true` " +
    "in your app config."
  );
}
```

**What we observed:** the in-cluster image is this project's own `yarn build`
output, so it runs under `NODE_ENV=production`. With
`auth.providers.guest.dangerouslyAllowOutsideDevelopment` unset (M6 Task 2's
first attempt), `POST /api/auth/guest/refresh` answered `403` with the exact
first message above. Every other backend signal was green throughout — zero
`PlaceholderProcessor` errors once Finding 15's workaround was in place,
`"Collating documents for software-catalog succeeded"` in the logs, RBAC and
`backstage.io/kubernetes-id` labels verified live against the cluster — and
the catalog was, in the plain sense that matters to an audience, undeliverable:
nothing reachable from a browser could authenticate to see it.

**Why it matters, beyond this project:** the message is correct and the
default is the right one for the overwhelming majority of Backstage
deployments, where "outside development" means "reachable by more than one
person, possibly over a network Backstage does not control." It is exactly
wrong-shaped for this project's specific deployment: a single-operator, one
namespace, `kind` cluster with no Ingress, no LoadBalancer, no node port —
reachable at all only through a `kubectl port-forward` the operator
themselves runs — where "development" is a fair description of the actual
risk profile even though the build is a production build. The gate does not
know that; it only knows `NODE_ENV`. The generalizable lesson (recorded
already in ruling M6-R7, restated here for the finding this project keeps
locally): a backend health signal — build succeeded, plugin registered,
collation succeeded — is not the same claim as "the deliverable is reachable
by the audience it is for," and this gate is a clean example of the two
diverging without either side lying.

**What a fix would need to do:** nothing, on the upstream side — the gate
already does what it should. The fix, such as it is, is entirely this
project's own configuration and its own documented posture, not a patch.

**How we worked around it, and whether it should be adopted upstream:**
`platform/backstage/app-config.production.yaml` sets
`auth.providers.guest.dangerouslyAllowOutsideDevelopment: true` explicitly,
with a comment beside it (not quietly) stating the trade this makes: anyone
who can reach port 7007 can authenticate as the guest user and, through the
autonomy contract card, flip the one write this deployment's Backstage
instance can make. The narrowness of that write — a ServiceAccount scoped to
`get/list/patch` on `resourceNames: [northstar-autonomy]` only, no `delete`,
separate from the read-only Kubernetes-plugin ServiceAccount — is what keeps
this posture defensible for a laptop demo cluster with no network path in
except an operator's own port-forward, and design §10 states the trade-off
in the same terms for anyone reading the architecture docs rather than the
config comment. There is nothing to propose upstream: the flag exists
precisely so a deployment in this project's position can make this call
explicitly, which is what happened.

**Existing upstream activity:** not applicable in the usual sense — there is
no gap to find on the tracker, because the behaviour matches its own
documentation and its own error message exactly. A read-only
`gh search issues`/`gh search prs` sweep (2026-09-15) against
`dangerouslyAllowOutsideDevelopment` and "guest provider production" surfaced
no discussion of this default being wrong; the closest hits are unrelated
Kubernetes-deployment auth reports (401s from other causes, plugin startup
failures) that do not touch the guest provider's own gate.

**Status: not filed by this project, and nothing to file.** This entry
exists to record a real cost this project paid against a working-as-designed
default, not to propose upstream change.

---

## Finding 17: v1.6.0 returns an ExtMCP deny on `tools/call` as an `isError` tool result, and `error.data` — where the PDS's deny marker lived — is gone

**Status: local, not filed.** This is a behaviour change between two releases
of a component this project does not own, recorded because it overturned a
claim Finding 9 documents and a property the deck states ("HTTP 200 means no,
carrying JSON-RPC error `-32001`").

**Project and version:** `agentgateway` `cr.agentgateway.dev/agentgateway`
v1.5.0 → v1.6.0, measured on this cluster on 2026-10-13 (M8 Task 0). Upstream
change: PR [`#3526`](https://github.com/agentgateway/agentgateway/pull/3526),
`tool_error_body` in `crates/agentgateway/src/mcp/mod.rs`.

**What changed, plainly.** A `PERMISSION_DENIED` `AuthorizationError` from the
ExtMCP `mcpGuardrails` hook, on a `tools/call`, used to come back as HTTP 200
with a JSON-RPC **error** (`error.code = -32001`, `error.message` the PDS's
reason, `error.data` the PDS's `mcp_error` payload whole; Finding 9). On v1.6.0
it comes back as HTTP 200 with a JSON-RPC **result** carrying an MCP tool
error, and nothing of `mcp_error` survives. Only
`AuthorizationError.Code == INTERNAL_ERROR` keeps the JSON-RPC error form, and a
policy refusal is not an internal error, so this project does not use it to get
the old shape back.

**The wire bytes, measured** (direct `tools/call`, EUR 6,200 `execute_payment`
at autonomy level `assisted`, `Content-Type: application/json`):

```
HTTP/1.1 200 OK
{"jsonrpc":"2.0","id":"2","result":{"content":[{"type":"text","text":"requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level assisted [policy-denial rule=settlement-agent.assisted.execute_limit limit=6000.00 requested=6200.00]"}],"isError":true}}
```

The text is the PDS's `AuthorizationError.reason`, byte for byte. On v1.5.0 the
gateway's own prefix `mcp: mcpGuardrails rejected: ` came with it into
`error.message`; on v1.6.0 the **response** text carries no prefix (the SERVER
span's `otel.status_description` still does). The agent's parser is anchored on
the tag at the end of the text, so it does not depend on the prefix either way.

**What this project did about it.** The facts a client needs (rule, limit,
requested amount) now ride in the message text, in one fixed format written by
`platform/policy-decision-service/pds/deny_text.py::format_deny` and read by
`agents/settlement-agent/policy_denial_text.py::parse_deny`:
`<reason> [policy-denial rule=<rule> limit=<amount|none> requested=<amount|none>]`.
A contract test feeds the PDS's real output into the agent's real parser, and
an `isError` result with no tag stays a malfunction, never a policy decision.

**Two neighbours of the same upgrade**, measured in the same pass:

- The gateway's SERVER spans moved to stable HTTP semantic conventions (PR
  `#3182`): `http.request.method`, `http.response.status_code`, `url.path`,
  `client.address`, `server.address`, `server.port` replace `http.method`,
  `http.status`, `http.path`, `src.addr`, `http.host`, `http.version`.
  `docs/telemetry-contract.md` carries the re-measured tables.
- Refused SERVER spans gained `error.type` (`"MCP"` for a policy refusal,
  `"JwtAuth"` for an authentication one) beside the existing `error = true`.
  A scalar and its dotted extension on one key cannot be mapped by OpenSearch,
  so the collector's `attributes/opensearch-compat` processor now renames
  `error.type` to `error_type` on the OpenSearch branch.

The ExtMCP proto is byte-identical between the two releases and
`deploy/platform/agentgateway/config.yaml` validates unchanged.

## Open questions — not findings, because nobody has demonstrated them

An entry here is a question this project could not answer, written down so the
next person does not have to rediscover that it is open. It is deliberately not
numbered into the findings above: a finding names a behaviour and shows the
evidence, and this one did not when it was written.

**There is exactly one entry in this section, and M3 Task 5B corrected the
sentence above to say so.** It read *"neither of these does"* from M2.2 until
then — plural, with one entry beneath it. Whether a second question was
planned and never written, or written and dropped, is not recoverable from
this document's history; what is recoverable is that nobody re-read the
sentence after the section stopped having two members. Recorded rather than
silently fixed, because this repository's most-repeated defect is exactly
this: a sentence true when written, falsified by a later edit, and nobody
re-read it.

**And that one entry has since been answered.** OQ-U1 was measured live in M3
Task 5B and is now closed. It stays in this section rather than moving up into
the findings, for the reason this section's own rule gives: the measurement
found no defect to name. An answered question is kept, with its answer, rather
than deleted — the distance between what was feared and what was measured is
the part worth keeping.

### OQ-U1 (closed, M3 Task 5B — measured, not a bypass): does `mcpGuardrails` evaluate every message inside a batched JSON-RPC array? (agentgateway v1.5.0)

**The answer, first, because it is the reason to read the rest. [measured]**
agentgateway v1.5.0 **rejects a JSON-RPC array outright, before the
`mcpGuardrails` hook runs at all.** It answers `HTTP 400` with a `text/plain`
body, `mcp: fail to deserialize request body: data did not match any variant
of untagged enum JsonRpcMessage`. Zero `northstar.policy.decision` spans are emitted,
`payments-mcp` records nothing, and the ledger is untouched. That is the third
of the three outcomes this entry predicted ("A rejected batch closes it a
third way"), and it means **there is no batch bypass on this transport in this
version.** The M2.2 review's concern was correct to raise and turns out not to
be reachable.

**The probe, exactly as run** (M3 Task 5B, against the deployed
`cr.agentgateway.dev/agentgateway:v1.5.0` on the live `kind-northstar`
cluster, costing no model tokens — it speaks MCP to the gateway directly,
reusing `tests/e2e/test_caller_identity.py`'s port-forward and
`spire-server jwt mint` machinery):

1. Open a session: `initialize` with a real SPIRE-minted JWT-SVID for the
   gateway's audience → `HTTP 200`, `mcp-session-id` returned.
2. Create something worth stealing: a single `propose_payment` for €6,200 →
   `HTTP 200`, `PAY-000024`, `status: proposed`. This matters: it means a
   successful bypass would have *executed a real over-limit payment*, so the
   probe could not have produced a false negative by asking the gateway to
   execute a payment that did not exist.
3. POST the batch: a JSON-RPC **array** of two `tools/call` messages on that
   same session — a benign `propose_payment` first, then
   `execute_payment(PAY-000024, 6200)`, which is €200 over
   `settlement-agent`'s assisted allowance and is the exact call M2.2 exists
   to refuse — with a hand-minted `traceparent` so the resulting trace could
   be fetched by id rather than searched for.

**What came back, and what the trace says. [measured]**

| Observation | Value |
|---|---|
| HTTP status | `400` |
| `content-type` | `text/plain` |
| body | `mcp: fail to deserialize request body: data did not match any variant of untagged enum JsonRpcMessage` |
| spans in the trace | **1** — `agentgateway`, operation `POST /*`, SpanKind SERVER |
| `northstar.policy.decision` spans | **0** |
| `payments-mcp` spans | **0** (no `tools/call execute_payment`, and no `initialize` either) |
| ledger, read back afterwards | `PAY-000024` still `status: proposed` |

**The single span is itself the evidence, and it is worth more than the status
code.** Its operation name is `POST /*` — the gateway's own HTTP route span —
not `tools/call`. Every guarded call in this system produces a `tools/call`
SERVER span with `gen_ai.tool.name` on it and a `Guardrail` CLIENT child; this
produces neither, because the request never reached MCP method dispatch. The
rejection is at deserialization, one layer *above* the point where a method
name exists to be matched against `methods: {"tools/call": request}`. The
gateway carries the parser's own message into
`otel.status_description` on that span, so the cause is legible from the trace
alone.

**The control, because a refusal that proves nothing is worse than no probe.**
A `400` could equally mean "this probe built a malformed request". So the same
two messages were sent **singly**, on the same session, with the same token:

| Sent | Result | `northstar.policy.decision` spans |
|---|---|---|
| message 1 alone (`propose_payment`, €6,200) | `HTTP 200`, `PAY-000025` recorded `proposed`; `payments-mcp` and `payments-api` both in the trace | **1** — `permit`, rule `unlimited_tool` |
| message 2 alone (`execute_payment`, €6,200) | `HTTP 200` carrying JSON-RPC error `-32001`, the PDS's own refusal text and `data.decision: "deny"`; no `payments-mcp` span | **1** — `deny`, rule `settlement-agent.assisted.execute_limit`, `northstar.policy.limit.amount` 6000, `northstar.policy.request.amount` 6200 |
| a **one-element** array holding message 1's exact shape | `HTTP 400`, byte-identical `text/plain` body, one `POST /*` span | **0** |

So both messages are well-formed and individually decidable — one is permitted
and one is denied, each with its own decision span — and wrapping a single
*known-good* message in an array is enough to get the same rejection. The
rejection is attributable to the array container and to nothing else about the
request.

**What this does NOT establish, stated so the closure is not overread.** It
does not establish *why* the rejection happens — the `untagged enum
JsonRpcMessage` text points at the MCP layer's own deserializer (rmcp's
`JsonRpcMessage`, which the gateway wraps) rather than at anything
configurable in `config.yaml`, but the source was not read at the image's
revision to confirm which variant set is missing an array case. It does not
establish that a *future* version behaves this way: the day agentgateway
accepts a batch on this transport, the original question reopens in full. And
it says nothing about other MCP transports — this project is streamable-HTTP
throughout, and stdio was never probed.

> **Corrected in the M3 final-review fix round (2026-09-10):** the paragraph
> above used to end its second sentence with *"…the original question reopens
> in full, **and nothing in this repository's test suite would notice**"*. That
> was true when written, and the whole-branch final review filed it as a
> finding — a measurement this careful, left without a guard, decays into
> prose. It now has one. `tests/e2e/test_caller_identity.py::test_a_jsonrpc_batch_is_rejected_before_the_policy_hook_and_changes_nothing`
> re-runs the probe above on every gate run and asserts exactly the three
> things that were measured, and nothing more: `HTTP 400`, **zero**
> `northstar.policy.decision` spans in a trace fetched by an id the test mints itself,
> and the bait payment still `proposed` in the ledger afterwards. It carries
> the same control the probe did — the single `propose_payment` that creates
> the bait must first succeed on that same session with that same token — so a
> `400` from a gateway that is merely unhealthy cannot be misread as this
> finding holding. It is ungated and spends no model tokens, which is why it
> lives in `test_caller_identity.py`: that file already owns the port-forward,
> the `spire-server jwt mint` machinery and the streamable-HTTP session dance
> this probe needs. The rest of this paragraph stands unchanged — the *why* is
> still unread at the image's revision, and stdio is still unprobed.

> **Masked since M8 (v1.6.0, the gateway's own ceiling).** The route's CEL
> `authorization` ceiling runs *before* the MCP body is parsed, and a JSON-RPC
> array carries no `mcp.methodName`, so a batch is now answered
> `HTTP 403 authorization failed` and never reaches the MCP layer's own
> deserializer. The `400 mcp: fail to deserialize request body` measured above is
> therefore **masked, not gone**: it is what a batch would get if the ceiling were
> removed, and it has not been re-measured on v1.6.0. The test pins the 403 and its
> body exactly (`BATCH_REJECTED_BY_CEILING`), so the day either layer changes, the
> test goes red and this question is asked again on purpose. Still no bypass: the
> bait payment stays `proposed` and there are zero `northstar.policy.decision` spans.

**Status: nothing filed, nothing asked upstream, and nothing to file.** No
issue, comment, pull request or discussion post exists anywhere for this, and
the question was not put to the maintainers. There is also no defect here to
put to them: refusing a payload shape the configuration does not opt into
handling is the gateway failing closed, which is the right direction.

**The original question and the reasoning that produced it are kept below,
unedited, because the distance between the two is the record.**

---

**The question.** JSON-RPC 2.0 allows a client to send an array of requests in
one payload. If agentgateway v1.5.0 unpacks such an array but hands the
`mcpGuardrails` processor only the first message — or the array as a single
undecidable unit — then a batch containing a benign `tools/call` followed by a
governed one could reach the upstream MCP server with no decision made about
the second. That is the shape of a policy bypass, and in this project it would
mean an `execute_payment` reaching `payments-mcp` unrefused.

**Why it is recorded at all.** It is the one bypass shape the M2.2 final
whole-branch review could not rule out from the source and configuration it
read, in a milestone whose entire claim is that a specific call is refused. An
unanswered question about the choke point is worth more written down than
assumed away.

**What was actually checked. [measured / read]**

- **The wire contract can only carry one message per decision.**
  `platform/policy-decision-service/proto/ext_mcp.proto` (vendored at v1.5.0,
  digest pinned in its own header and now guarded by a test) defines
  `McpRequest` with a **singular** `string method = 2` and a **singular**
  `optional bytes mcp_request = 4` ("JSON-RPC `params` as raw JSON bytes").
  There is no repeated-message field anywhere in `McpRequest`. So whatever the
  gateway does with a batch, it cannot describe a batch to the policy server in
  one call: it must either issue one `CheckRequest` per message, or describe
  only one of them.
- **The policy server refuses anything it cannot read as a single decidable
  call.** `platform/policy-decision-service/pds/server.py:259` denies any
  `method` other than `tools/call` under `undecidable_method`, and
  `parse_call` refuses to build a `PolicyInput` from params it cannot read. So
  if a batch arrived described as one non-`tools/call` unit, the PDS denies it.
  This is the half of the question that *is* settled, and it is the half inside
  this project's own code.
- **The gateway config sends exactly one method for a decision**
  (`deploy/platform/agentgateway/config.yaml`, `methods: {"tools/call":
  request}`), so a batch is not something this configuration opts into
  handling.

**What was NOT established, precisely.** Whether the gateway's MCP layer
accepts a JSON-RPC array on this transport at all; and if it does, whether it
loops the guardrail over each element, evaluates only the first, or rejects the
batch before the guardrail runs. None of that was read at the revision the
running image reports, and none of it was probed live.

**What would settle it, cheaply.** A live probe against the deployed gateway,
which costs no model tokens: POST a JSON-RPC **array** of two `tools/call`
messages — a `propose_payment` first, then an over-limit `execute_payment` —
with a minted JWT-SVID, using the same direct-at-the-gateway machinery
`tests/e2e/test_caller_identity.py` already has, and then count two things in
the resulting trace: how many `northstar.policy.decision` spans the PDS emitted, and
whether `payments-mcp` recorded a `tools/call execute_payment`. Two decisions
and no tool span means per-message evaluation and no bypass. One decision plus
a tool span is the bypass, and at that point it is a finding with evidence
rather than a question. A rejected batch closes it a third way. Reading
`crates/agentgateway/src/mcp/` at the image's own revision for the array case
would narrow it further before any probe is run.

**Status (as written, M2.2): nothing filed, nothing asked upstream.** No
issue, comment, pull request or discussion post exists anywhere for this, and
the question was not put to the maintainers. It is carried as this project's
own open item, to be measured — and only then written up as a finding, if it
turns out to be one.

*(End of the M2.2 text. The probe above was run exactly as this section
specifies it, the third outcome is what it produced, and it did not turn out
to be a finding. The one thing it suggests and that was **not** done is
reading `crates/agentgateway/src/mcp/` at the image's own revision — the live
measurement was decisive enough on its own that reading the source would have
narrowed only the `why`, which the closure above records as still open.)*
