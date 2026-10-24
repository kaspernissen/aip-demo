# claims-agent: SDK behaviour and emitted telemetry

What this service relies on in `a2a-sdk` 0.3.7, what it emits, and where
upstream libraries fall short of the OpenTelemetry semantic conventions. Gaps
are recorded as found; no attribute or span has been invented to hide one.
Section numbers are referenced from other files. See
`agents/settlement-agent/NOTES.md` for the findings that apply to both agents.

## 1. Version pins

Same pins as settlement-agent: `a2a-sdk[telemetry]==0.3.7` forces
`opentelemetry-sdk` and the OTLP exporter to 1.44.0 and the httpx and starlette
instrumentations to 0.65b0.

## 2. `a2a-sdk[telemetry]` does not pull in starlette

`A2AStarletteApplication` needs `starlette` and `sse-starlette`, but a2a-sdk
declares them only under its `http-server` extra. Nothing else in this
service's dependency tree brings them in, so `requirements.txt` pins them
explicitly (not the whole extra, which would also install `fastapi` and
`annotated-doc`).
`tests/test_main.py::test_build_app_constructs_a_starlette_app_without_import_error`
covers it; if the pins are dropped, `import main` fails at collection with
`No module named 'starlette'` (raised from
`opentelemetry.instrumentation.starlette`).

## 3. One `Message` per blocking `message/send`

a2a-sdk's blocking `message/send` returns the first `Message` event and drops
anything enqueued after it. The executor therefore enqueues a single final
`Message`, and progress narration goes to the log. `message/stream` loses the
narration for the same reason; the agent card still advertises `streaming`.

## 4. Inbound trace context: StarletteInstrumentor

a2a-sdk does not extract inbound trace context (see settlement-agent section
4). `main.py` applies `StarletteInstrumentor().instrument_app(app)` to the built
app and `bootstrap.py` sets the global propagator to trace-context plus baggage.
The `POST /` entry span is then a child of the caller's span and carries the
usual HTTP server attributes.

claims-agent and settlement-agent both name their entry span `POST /`, so a
trace lookup that expects one such span must also filter by service.

## 5. Outbound A2A propagates trace context

`delegation.py` sends the JSON-RPC `message/send` over a plain
`httpx.AsyncClient` (not a2a-sdk's own client). `HTTPXClientInstrumentor`
injects `traceparent` and `baggage`, so settlement-agent's spans join the same
trace.

## 6. a2a-sdk client telemetry

a2a-sdk's own client transports are decorated `@trace_class(kind=CLIENT)` and
have the same gap as the server side: span names follow the Python call graph
and no span carries any attribute (no `gen_ai.*`, no agent, task or context
identity, not even the destination URL).

## 7. HTTP semconv spelling

`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on the deployment. Without it the
httpx and starlette instrumentations emit the legacy attribute names.

## 8. Settlement failure surfacing

If delegation to settlement-agent fails, the task already exists, so letting
the exception escape `execute()` would abort the HTTP response mid-body. The
executor instead marks the task `failed` and puts the real reason in the reply's
`error`.

## 9. Known noise

- a2a-sdk emits a Starlette deprecation warning at import.
- An e2e trace fetched right after the response can be missing spans from other
  services, because each process exports independently; tests poll the trace
  until the expected services appear.

## 10. Baggage inside the executor

a2a-sdk runs `execute()` in a separate asyncio task (`asyncio.create_task`
copies the context before the executor runs). `northstar.claim.id` is attached
to baggage inside `execute()`, so it reaches only spans created in that task:
the outbound A2A CLIENT span and `EventQueue.enqueue_event`. The ASGI entry span
and most `a2a.server.*` spans lack it, as does `EventQueue.close` (it runs after
`execute()` has detached the context). settlement-agent, which receives the id
as inbound baggage, has full coverage. Nothing here patches a2a-sdk's task
spawning to widen coverage.

## 11. How a waiting claim works

- When a claim needs a person, `execute()` marks the task `input-required` and
  returns; nothing in the process waits. The hand-off span's context is stored
  in task metadata (`northstar.a2a.parked_span_context`) and the reply to report
  in `northstar.a2a.parked_reply`. These are task metadata, not telemetry.
- A resume is a `message/send` on the same task id. a2a-sdk starts a fresh
  `execute()` with the task and its state; the resume span
  (`northstar.a2a.resume`) lives in the resumer's trace and links back to the
  hand-off span.
- The reply is read only from task metadata, never from message history,
  because the SDK appends every incoming message without checking its role. A
  resume that is not user-role is refused. The outcome the customer sees comes
  from what payments-api says, not from the resume message.
- The input-required update is `final=False`; the completion uses the default.
- After a stream ends the SDK removes the task queue in a background task, so a
  resume sent within microseconds of a close can tap a closed queue. Tests wait
  for the SDK's background tasks.
- `push.py` wraps the request handler and refuses a push-notification config
  unless its URL matches the allowlist exactly (JSON-RPC `-32602`; the executor
  never runs).
- The task store and push config store are in memory, so a restart loses
  waiting claims.
- sse-starlette ends SSE lines with CRLF; the customer-ui frame reader
  normalises that.

## 12. Free-text intake

A claim that is not a JSON object is read by a model (`extraction.py`,
`pydantic-ai-slim[anthropic]`, model from `NORTHSTAR_CLAIMS_MODEL`). The model
returns a draft; `intake.check_draft` applies the same `validate_claim` as the
JSON path and decides whether it is a claim and what is missing. A JSON claim
never reaches the model.

If something is missing, the task parks at `input-required` with
`northstar.a2a.parked_reason = needs_information` and the intake state
(`northstar.a2a.intake_state`: claim id, the customer's messages, the kept
draft, number of questions asked) in task metadata. The customer's answer is a
user message on the same task. Which wait a resume ends is read only from
`parked_reason`; no key means the approval wait.

The status message for a question has two text parts, the question and then the
reply JSON; clients read the last part. Every reply carries `claim` (policy
number, claimed amount, incident date, description as read by claims-agent);
`amount` is still settlement's.

Instrumentation mirrors settlement-agent's: `Agent.instrument_all` with the
same settings, plus the httpx2 instrumentor for the model's HTTP hop
(`test_instrumentation_settings.py` keeps the two equal). The claim id is in
baggage before the model runs, so `invoke_agent claims-agent` and `chat` carry
`northstar.claim.id`. With `NORTHSTAR_CAPTURE_CONTENT` on (the deploy default)
the `chat` span carries the customer's text.

`ANTHROPIC_API_KEY` comes from the optional `northstar-model` Secret, with an
egress rule to Anthropic's published range (TCP 443). Without the key the pod
starts, JSON claims work, and a free-text claim fails on the task with the
credential error in `error`.

Not yet measured live: the `chat` span's httpx2 child, the resume link on an
answered question, and how the model handles a claim that tries to instruct it.
