# Upstream findings — held locally, not filed

Status: M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic, and through **M2.1 (Interposed)** for the two findings the
interposed proxy produced — Findings 6 and 7, both against `agentgateway`
v1.5.0, added by M2.1 Task 5 and checked against commit `27a32df`.
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
seven items, 1 through 7 (M1.2 added Gap 7). Two of those are not findings
about a third-party library's behaviour at all — they are open design
questions this project has not yet answered itself:

- **Gap 2** ("human-in-the-loop approval spans") — no convention exists yet
  for representing an approval/escalation step in a trace; deferred to M3,
  when this project has a working implementation to argue from.
- **Gap 3** ("policy and autonomy decision spans") — no convention exists for
  a policy/autonomy decision span; `policy.decision` is this project's own
  attribute to define in M2, not a gap in someone else's library.

Neither is "filing-ready" in the sense the findings below are: there is no
specific library behaviour to point at, no version to cite, no issue number
to check. They are recorded in the contract as open questions this project
will need to resolve itself, not as upstream defects. This document covers
the remaining five contract items — Gaps 1, 4, 5, 6, and 7 — renumbered below
as Findings 1–5 in the order a reader would hit them walking the chain from
the A2A hop down to the model's own outbound call.

**Findings 6 and 7 do not come from that list at all**, and the numbering
would mislead a reader who assumed they did. They are M2.1 findings, both
against `agentgateway` v1.5.0, about a component that did not exist in this
system when Gaps 1–7 were enumerated: there was no proxy in the path to have
a finding about. They continue the numbering because that is what a reader
scanning this document expects, not because they map onto a contract gap.
Findings 6 and 7 are also the only two here whose subject is a **Linux
Foundation** project rather than an independent Python package — worth
stating because "agentgateway is CNCF" is an easy and wrong assumption, and
this document is the wrong place to launder it.

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
this project's own settlement-agent, pinned to `mcp==1.9.4`
(`agents/settlement-agent/requirements.txt:13`). Two different, and
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
`otelhttp v0.66.0` emits it by default. So a single trace in this system
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
