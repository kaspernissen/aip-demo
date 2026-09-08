# Upstream findings — held locally, not filed

Status: M1.1. No upstream repository exists for this project yet, and filing
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
the A2A hop down to the model's own outbound call. The evidence for Findings
1–4 was originally gathered as A8 upstream-contribution material and lives,
in full, in `docs/telemetry-contract.md` and the per-component `NOTES.md`
files (`agents/claims-agent/NOTES.md`, `agents/settlement-agent/NOTES.md`,
`tools/payments-mcp/NOTES.md`); Finding 5's evidence was gathered in M1.2
Task 4 and re-verified, including a fresh tracker search, for this document —
this document consolidates and re-verifies it into one filing-ready list; it
does not re-derive it.

**Finding 5 is not filing-ready the way Findings 1–4 are, and that is itself
the finding.** It was first suspected as a genuine upstream gap (a library
not supporting a newer HTTP client). A tracker search shows the opposite: the
fix is already merged and already shipped in the exact library version this
project has installed. What remains is a one-line change in this project's
own code, not an upstream ask. It is included here anyway, in the same
format as the other four, because "we searched and there is nothing to file
because it is already fixed" is exactly the kind of claim this document
exists to state precisely rather than assume.

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
(`agents/settlement-agent/requirements.txt:13`). The capture was taken via
`tests/e2e/test_claim_correlation.py` and
`agents/settlement-agent/tests/test_mcp_tools.py` against the real deployed
payments-mcp service (`task-7-report.md`, "Where `northstar.claim.id`
lands"; see also `docs/telemetry-contract.md`'s Gap 4, which cites the same
live capture: "`northstar.claim.id` reaches payments-mcp on **0** of its
tool-call spans... in a live capture of the full chain"). This predates
Task 6 — it was Task 7's own finding — but it is a genuine live observation
at this project's real, installed versions, not a prediction from reading
source alone.

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

---

## Finding 5: the model's own outbound HTTP hop has no CLIENT span — and, unlike Findings 1–4, the fix is already shipped

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

**What we observed instead:** capturing a real settlement end to end and
reading every httpx-instrumented CLIENT span back from Jaeger: 12 spans, all
of them settlement-agent's or payments-mcp's calls to payments-mcp or
payments-api (`POST`/`GET`/`DELETE` against
`payments-mcp...svc.cluster.local:8000` and
`payments-api...svc.cluster.local:8080`). **Zero** spans target
`api.anthropic.com`. The only trace of the Anthropic call anywhere in the
captured spans is `server.address=api.anthropic.com`, appearing as an
attribute on pydantic-ai's own `chat claude-sonnet-5` span — a real, useful
span, but not an HTTP CLIENT span, and carrying none of the HTTP-layer
attributes an httpx span would.

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

**What a fix needs:** for this project specifically, one line —
`HTTPX2ClientInstrumentor().instrument()` alongside the existing
`HTTPXClientInstrumentor().instrument()` call in `bootstrap.py`. No
dependency bump, no waiting on anyone: the class needed is already installed.

**How we worked around it, and whether it should be adopted upstream:** we
did not work around it, and there is no upstream action to recommend —
unlike Findings 1–4, upstream already did the work. This project's own
`bootstrap.py` is deliberately unchanged this milestone (out of scope per
the plan), and the gap is pinned rather than silently left to rot:
`tests/e2e/test_settlement_llm_trace.py::test_the_models_own_http_hop_has_no_client_span`
asserts the absence directly and is written to fail loudly the day someone
adds the missing instrumentor call — the intended signal that the fix
landed, not a regression to chase.

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

**Status: not filed by this project — and, unlike every other finding in
this document, there is no upstream action left to file.** The fix exists,
is merged, and is already installed in this repository's own dependency
tree. The only remaining step — applying `HTTPX2ClientInstrumentor()` in
`agents/settlement-agent/bootstrap.py` — is this project's own deliberately
deferred work (`bootstrap.py` is out of scope for this milestone), not an
upstream gap. Recording this here in full, in the same format as Findings
1–4, rather than silently dropping it once the search came back negative for
"a gap worth filing," because the accurate statement — "we suspected an
upstream defect, searched, and found it already fixed" — is itself a
correction to how this milestone's own working notes had framed the finding
before this document was written, and the project's governing rule is to
correct a telemetry claim the moment it is found to be wrong, not to quietly
let the more interesting-sounding version stand.
