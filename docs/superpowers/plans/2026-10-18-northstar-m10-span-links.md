# Northstar M10 — span links for the human wait — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A claim routed to a human ends trace A at the hand-off; the human's release, the resumed A2A task and an A2A push to the customer all run in trace B, linked back to trace A; no polling spans during the wait.

**Architecture:** The claims-agent's `execute()` returns at `input-required` (no parked future) after saving trace A's span context in the task's metadata. The reviewer-ui's resume (`message/send` on the same task id, now blocking) runs a fresh `execute()` in trace B that links to the saved context. a2a-sdk's own push sender POSTs the completed task to a new Python server in the customer-ui image, which relays it to the browser over one SSE response.

**Tech Stack:** a2a-sdk 0.3.7 (`DefaultRequestHandler`, `InMemoryPushNotificationConfigStore`, `BasePushNotificationSender`), Starlette + uvicorn, OpenTelemetry Python 1.44.0 / instrumentation 0.65b0, Next.js 15.5.21 static export, vitest 2.1.8, Cilium `CiliumNetworkPolicy`, OTel Weaver v0.28.0 registry.

**Spec:** `docs/superpowers/specs/2026-10-18-northstar-m10-span-links-design.md`

## Global Constraints

- Never sample, never drop a span (memory: never-sample-this-demo). No SpanProcessor filters.
- Pin the latest released versions, verified against upstream; reuse the repo's existing pins for shared packages (`httpx==0.28.1`, `uvicorn==0.34.0`, OTel `1.44.0` / `0.65b0`).
- Every telemetry name we own is `northstar.*` and is in `telemetry/registry/` (`stability: development`).
- The money path is unchanged: agentgateway → ExtMCP → PDS failClosed, `human-approved.execute`, €50,000 CEL ceiling, the ledger check on resume.
- TaskStore stays `InMemoryTaskStore`; a claims-agent restart loses waiting claims (documented, not fixed).
- The push token never appears in a URL, a span attribute or a log line.
- Commit with explicit paths. No `Co-Authored-By` or any generated-by footer.
- Any change to a gate path (`agents apps contracts deploy platform scripts tests systems tools`) demotes `docs/telemetry-contract.md`'s gate row: within 200 chars after `Gate row:` it must say `PREDICTED, NOT MEASURED` (enforced by `tests/unit/test_telemetry_contract_counts.py`). Task 1's commit does this; the gate run after the final review re-measures it.
- Never run `kind delete`, teardown, or touch `kind-registry`. Never edit scripts while `setup-kind.sh` runs. Never commit during a gate run.

## Rulings made while planning (from reading the code, 2026-10-18)

1. **No span in trace A after the hand-off**, not "zero `dequeue_event` spans": a2a-sdk's `@trace_class` wraps every `EventQueue` call, so the live stream has some. The spec was corrected to match.
2. **Clients read the claim stream to its end.** Today `streamClaim` (customer-ui) and `live.submit_claim` (harness) close the stream at `input-required`, which is exactly what starts `background_consume`. With `execute()` returning, the stream ends by itself within milliseconds.
3. **The reviewer-ui's resume becomes blocking** (`configuration.blocking` removed). Non-blocking existed only to avoid racing the parked producer's queue cleanup. Blocking returns `completed`, or the JSON-RPC error of a refused resume.
4. **The waiting claim's reply is read back from the stored task**, `current_task.status.message` (the JSON text part the `input-required` update carried), not from an in-process dict. The parked span context rides in the same update's `metadata` (`TaskManager` merges status-update metadata into `task.metadata`, `task_manager.py:148-151`).
5. **Push token flow.** The customer-ui server issues `{url, token}` from `POST /claims/push-config`; the browser puts both in `configuration.pushNotificationConfig`; the SDK sends the token as `X-A2A-Notification-Token`; the browser later opens `GET /claims/events` with the token in an `x-claim-token` **header** (never a URL: URLs are span attributes). The push URL is one fixed in-cluster URL, so the claims-agent's allowlist is exact-match.

## Review Focus

1. **A push that arrives before the browser subscribes** — the browser must still get the outcome at once (stored snapshot). Test in Task 3 (`test_a_push_before_the_subscriber_is_delivered_at_once`).
2. **Two resumes on the same task at once** (a reviewer double-click, a retry) — both read the ledger; the second must not raise a 500 or produce a second, contradicting completion. Test in Task 1 (`test_a_second_resume_after_completion_is_refused`).
3. **A malformed or missing parked span context** (an old task, a hand-edited one) — the resume still completes, with no link and a WARNING. Test in Task 1.
4. **A push config with a URL that differs only by case, trailing slash or query** — refused (exact match). Test in Task 2 (parametrised).
5. **The events stream drops mid-wait** (port-forward blip) — the browser reads the task once with `tasks/get` and reopens; it does not spin. Test in Task 3 (vitest).

---

### Task 1: claims-agent returns at input-required; the resume runs in trace B with a link

**Files:**
- Modify: `agents/claims-agent/executor.py` (whole `_run_claim` wait branch, `_resume`, `_Waiting`, module docstring)
- Modify: `agents/claims-agent/tests/test_task_lifecycle.py` (replace the resubscribe tests)
- Modify: `apps/reviewer-ui/claims.py` (blocking resume, docstring)
- Modify: `apps/reviewer-ui/tests/` — the test that asserts `"blocking": False` (find with `grep -rn blocking apps/reviewer-ui/tests`)
- Modify: `docs/telemetry-contract.md` (gate row → `PREDICTED, NOT MEASURED`)

**Interfaces:**
- Produces: task metadata key `northstar.a2a.parked_span_context` = `{"trace_id": "<32 hex>", "span_id": "<16 hex>", "trace_flags": <int>}`; link attribute `northstar.a2a.link.reason` = `"resume"` on the resume span's link. Task 4 registers both names (the metadata key is documented, not registered).
- Produces: `execute()` returns after the `input-required` update. A resume is a `message/send` with `taskId` of an `input-required` task.

- [ ] **Step 1: Measure the SDK first (no product code yet).** Add this test to `test_task_lifecycle.py`, using the file's existing `handler` fixture / helpers (`_until_input_required`, `_state`). Its point is to prove rulings 2 and 4 against a2a-sdk 0.3.7 before anything depends on them:

```python
async def test_after_execute_returns_the_stream_ends_and_a_resume_runs_a_fresh_execute():
    # Measurement for M10 (spec §5). If this fails, STOP: the design rests on it.
    handler, ledger = _handler_with_ledger(status="executed")
    task, seen = await _until_input_required(handler)
    # The stream ended on its own: _until_input_required drained it to the end.
    assert seen[-1] == TaskState.input_required
    stored = await handler.task_store.get(task.id)
    assert stored.status.state == TaskState.input_required
    assert "northstar.a2a.parked_span_context" in (stored.metadata or {})
    result = await handler.on_message_send(_resume_params(task))
    assert result.status.state == TaskState.completed
```

`_handler_with_ledger` and `_resume_params` are small helpers you add next to the existing ones: the first builds `DefaultRequestHandler(agent_executor=ClaimsAgentExecutor(delegates={"settlement-agent": FakeDelegate(AWAITING)}, ledger=FakeLedger(status)), task_store=InMemoryTaskStore())` using the fakes the file already has (rename to match what is there); the second builds `MessageSendParams(message=Message(role=Role.user, message_id=uuid4().hex, task_id=task.id, context_id=task.context_id, parts=[Part(root=TextPart(text="{}"))]))` with no `configuration` (blocking). Make `_until_input_required` drain the stream to its end and return every state seen.

- [ ] **Step 2: Run it, expect FAIL** (metadata key missing; the stream does not end because `execute()` is parked).

Run: `cd agents/claims-agent && .venv/bin/python -m pytest tests/test_task_lifecycle.py -k fresh_execute -v`

- [ ] **Step 3: Implement return-and-resume in `executor.py`.** Delete `_Waiting`, `self._waiting`, the future and `published` event. Replace the `AWAITING_APPROVAL` branch of `_run_claim` with:

```python
        span.set_attribute("northstar.a2a.task.state", TaskState.input_required.value)
        await updater.update_status(
            TaskState.input_required,
            message=updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]),
            final=False,
            metadata={PARKED_CONTEXT_KEY: _span_context_dict(span.get_span_context())})
        # execute() returns here. Trace A ends at the hand-off; the resume is a
        # new execute() in the reviewer's trace (B), linked back to this span.
```

(Check `TaskUpdater.update_status` in 0.3.7 accepts `metadata=`; if not, enqueue the `TaskStatusUpdateEvent` directly with `metadata=` as the old completion code did.) Add the helpers and the new `_resume`:

```python
PARKED_CONTEXT_KEY = "northstar.a2a.parked_span_context"


def _span_context_dict(ctx: trace.SpanContext) -> dict[str, Any]:
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x"),
            "trace_flags": int(ctx.trace_flags)}


def _parked_link(task: Task) -> trace.Link | None:
    raw = (task.metadata or {}).get(PARKED_CONTEXT_KEY)
    try:
        ctx = trace.SpanContext(trace_id=int(raw["trace_id"], 16), span_id=int(raw["span_id"], 16),
                                is_remote=True, trace_flags=trace.TraceFlags(int(raw["trace_flags"])))
    except (TypeError, KeyError, ValueError):
        logger.warning("task %s: no usable parked span context (%r); resuming without a link", task.id, raw)
        return None
    if not ctx.is_valid:
        logger.warning("task %s: parked span context is not valid; resuming without a link", task.id)
        return None
    return trace.Link(ctx, {"northstar.a2a.link.reason": "resume"})


def _waiting_reply(task: Task) -> dict[str, Any]:
    for part in reversed((task.status.message.parts if task.status.message else []) or []):
        if isinstance(part.root, TextPart):
            return json.loads(part.root.text)
    raise RuntimeError(f"task {task.id} is input-required but carries no reply to resume")
```

```python
    async def _resume(self, task: Task, event_queue: EventQueue) -> None:
        reply = _waiting_reply(task)
        link = _parked_link(task)
        token = otel_context.attach(set_claim_id(reply["claimId"]))
        try:
            with _tracer.start_as_current_span(
                    "northstar.a2a.resume", links=[link] if link else []) as span:
                span.set_attribute("northstar.a2a.task.id", task.id)
                record = await self._ledger.get(reply["paymentId"])
                decision = OUTCOMES.get(record.get("status"))
                if decision is None:
                    raise RuntimeError(
                        f"task {task.id}: the ledger shows {record.get('status')!r} for "
                        f"{reply['paymentId']}, so there is no human decision to report")
                span.set_attribute("northstar.a2a.task.state", TaskState.completed.value)
                outcome = {**reply, "status": record["status"], "decision": decision,
                           "narrative": DECIDED_NARRATIVE[decision]}
                updater = TaskUpdater(event_queue, task.id, task.context_id)
                await updater.complete(updater.new_agent_message([Part(root=TextPart(text=json.dumps(outcome)))]))
        finally:
            otel_context.detach(token)
```

`execute()` calls `await self._resume(task, event_queue)`. `_tracer = trace.get_tracer("claims-agent")` at module level. **Span name check:** if a span name `northstar.a2a.resume` needs registering, Task 4 does it; do not invent other names. Rewrite the module docstring: execute() returns at input-required; why (trace A ends at the hand-off, no polling); the resume reads the reply from the stored task and links to the parked context; a refused resume raises, the task stays input-required. Remove the `final=False` rationale only if a test shows `final=True` now works for the completion; otherwise keep it, shortened.

- [ ] **Step 4: Replace the resubscribe tests.** Delete the tests that exercise `on_resubscribe_to_task` (they pin the old design). Add, each a separate test:

```python
async def test_a_resume_span_links_to_the_parked_context(span_exporter): ...
    # the finished spans contain one "northstar.a2a.resume" whose single link's
    # span_id/trace_id equal the stored metadata's, with attribute
    # northstar.a2a.link.reason == "resume"

async def test_a_ledger_unbacked_resume_is_refused_and_the_task_keeps_waiting(): ...
    # FakeLedger(status="awaiting_approval"); on_message_send raises (or returns a
    # JSON-RPC error); task_store still says input_required; a later resume with
    # FakeLedger(status="executed") completes it

async def test_a_second_resume_after_completion_is_refused(): ...
    # first resume completes; second on_message_send raises the SDK's
    # "is in terminal state" error; the stored task is still completed with the
    # first outcome

async def test_a_malformed_parked_context_resumes_without_a_link(caplog, span_exporter): ...
    # overwrite stored.metadata[PARKED_CONTEXT_KEY] = {"trace_id": "nope"};
    # resume completes; resume span has no links; a WARNING names the task id

async def test_a_rejected_payment_resumes_as_declined(): ...
```

Use the file's existing span-exporter fixture if there is one; otherwise add an `InMemorySpanExporter` fixture with a `TracerProvider` set for the test module only (follow how other claims-agent tests capture spans, `grep -rn InMemorySpanExporter agents/`).

- [ ] **Step 5: Run the claims-agent suite, expect PASS.**

Run: `cd agents/claims-agent && .venv/bin/python -m pytest -q`

- [ ] **Step 6: Blocking resume in the reviewer-ui.** In `apps/reviewer-ui/claims.py` remove `"configuration": {"blocking": False}` and rewrite the docstring's last paragraph: "The message is a blocking send: it starts a fresh `execute()` on the claims-agent, which reads the ledger and completes the task, or refuses (a JSON-RPC error, raised here as `ResumeFailed`)." Update the reviewer-ui test that asserted non-blocking to assert the request carries no `configuration`. Run `cd apps/reviewer-ui && python -m pytest -q`.

- [ ] **Step 7: Demote the gate row.** In `docs/telemetry-contract.md` make the gate row read `Gate row: **PREDICTED, NOT MEASURED** — M10 changes the claims-agent's task lifecycle after the 7041c36 measurement; the next gate run re-measures it.` keeping the 7041c36 row text after it as history. Run `python -m pytest tests/unit/test_telemetry_contract_counts.py -q` from the repo root.

- [ ] **Step 8: Commit.**

```bash
git add agents/claims-agent/executor.py agents/claims-agent/tests/test_task_lifecycle.py apps/reviewer-ui/claims.py apps/reviewer-ui/tests docs/telemetry-contract.md
git commit -m "feat(m10): claims-agent returns at input-required; the resume is a fresh execute() in trace B linked to the parked span; reviewer-ui resumes blocking"
```

---

### Task 2: A2A push from the claims-agent, behind an exact-match URL allowlist

**Files:**
- Create: `agents/claims-agent/push.py`
- Modify: `agents/claims-agent/main.py`
- Modify: `agents/claims-agent/tests/test_main.py` (or create `tests/test_push.py`)
- Modify: `contracts/agent-cards/claims-agent.json` (`"pushNotifications": true`)
- Modify: `deploy/k8s/claims-agent.yaml` (env `CLAIMS_AGENT_PUSH_URL_ALLOWLIST`)

**Interfaces:**
- Consumes: Task 1's resume (pushes on completion come for free: the SDK pushes after every event of a request that has a stored config).
- Produces: `PUSH_URL = "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"` as the default allowlist entry; Task 3's server listens there.

- [ ] **Step 1: Failing tests** in `tests/test_push.py`:

```python
import pytest
from push import AllowlistedRequestHandler, PushUrlRefused

ALLOWED = "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"

@pytest.mark.parametrize("url", [
    ALLOWED + "/", ALLOWED.upper(), ALLOWED + "?x=1", "http://example.com/a2a/push",
    "http://169.254.169.254/latest/meta-data", ""])
async def test_a_push_url_off_the_allowlist_is_refused_before_the_task_starts(url):
    handler = _handler(allowlist=[ALLOWED])
    with pytest.raises(PushUrlRefused):
        await handler.on_message_send_stream(_params(push_url=url)).__anext__()
    assert handler.started == 0          # the executor never ran

async def test_the_allowlisted_url_is_accepted_and_stored():
    handler = _handler(allowlist=[ALLOWED])
    [e async for e in handler.on_message_send_stream(_params(push_url=ALLOWED, token="t"))]
    configs = await handler.push_config_store.get_info(handler.last_task_id)
    assert [c.url for c in configs] == [ALLOWED]

async def test_tasks_push_notification_config_set_is_checked_too():
    handler = _handler(allowlist=[ALLOWED])
    with pytest.raises(PushUrlRefused):
        await handler.on_set_task_push_notification_config(_set_params("http://example.com/"))

def test_the_allowlist_env_is_parsed_exactly():
    from push import allowlist_from_env
    assert allowlist_from_env({"CLAIMS_AGENT_PUSH_URL_ALLOWLIST": f" {ALLOWED} , "}) == [ALLOWED]
    assert allowlist_from_env({}) == [ALLOWED]
```

`_handler` wraps a stub executor that counts `execute()` calls; adapt helpers to the file's style.

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: push`).

- [ ] **Step 3: Implement `push.py`.**

```python
"""A2A push notifications for claims-agent, behind an exact-match URL allowlist.

A push URL is supplied by the client. An agent that POSTs wherever it is told
is a server-side request forgery hole, so the URL is checked here, before the
task starts, against an exact list from the Deployment's env. Cilium's Reach
rule (deploy/platform/cilium-policies.yaml) enforces the same thing on the
wire; this is the application's half.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import InvalidParamsError
from a2a.utils.errors import ServerError

DEFAULT_PUSH_URL = "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"


class PushUrlRefused(ServerError):
    def __init__(self, url: str):
        super().__init__(error=InvalidParamsError(message=f"push URL not allowed: {url!r}"))


def allowlist_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    raw = env.get("CLAIMS_AGENT_PUSH_URL_ALLOWLIST", DEFAULT_PUSH_URL)
    return [u.strip() for u in raw.split(",") if u.strip()]


class AllowlistedRequestHandler(DefaultRequestHandler):
    def __init__(self, *args, push_url_allowlist: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._allowed = frozenset(push_url_allowlist)

    def _check(self, config) -> None:
        if config is not None and config.url not in self._allowed:
            raise PushUrlRefused(config.url)

    async def on_message_send(self, params, context=None):
        self._check(params.configuration and params.configuration.push_notification_config)
        return await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context=None):
        self._check(params.configuration and params.configuration.push_notification_config)
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def on_set_task_push_notification_config(self, params, context=None):
        self._check(params.push_notification_config)
        return await super().on_set_task_push_notification_config(params, context)
```

Verify the exact error classes and method names against the installed `a2a/server/request_handlers/default_request_handler.py` and `a2a/utils/errors.py`; keep the behaviour (JSON-RPC InvalidParams, raised before `_setup_message_execution`).

- [ ] **Step 4: Wire it in `main.py`.**

```python
    push_client = httpx.AsyncClient(timeout=10.0)   # instrumented by bootstrap's HTTPXClientInstrumentor
    push_store = InMemoryPushNotificationConfigStore()
    handler = AllowlistedRequestHandler(
        agent_executor=ClaimsAgentExecutor(),
        task_store=InMemoryTaskStore(),
        push_config_store=push_store,
        push_sender=BasePushNotificationSender(push_client, push_store),
        push_url_allowlist=allowlist_from_env(),
    )
```

Confirm `bootstrap.py` instruments httpx globally (`HTTPXClientInstrumentor().instrument()`); if it instruments per client, instrument `push_client` too. Add `x-a2a-notification-token` nowhere in CORS (the browser never sends it to the claims-agent).

- [ ] **Step 5: Agent card and Deployment.** `contracts/agent-cards/claims-agent.json`: `"pushNotifications": true`. `deploy/k8s/claims-agent.yaml`: add env `CLAIMS_AGENT_PUSH_URL_ALLOWLIST` = the default URL. Run `grep -rn pushNotifications tests` and update any test pinning `false`.

- [ ] **Step 6: Run the claims-agent suite and the contract unit tests, expect PASS.**

Run: `cd agents/claims-agent && .venv/bin/python -m pytest -q && cd ../.. && python -m pytest tests/unit -q -k "card or contract"`

- [ ] **Step 7: Commit.**

```bash
git add agents/claims-agent/push.py agents/claims-agent/main.py agents/claims-agent/tests/test_push.py contracts/agent-cards/claims-agent.json deploy/k8s/claims-agent.yaml
git commit -m "feat(m10): claims-agent sends A2A push notifications, to an exact-match URL allowlist only"
```

---

### Task 3: the customer-ui server receives the push and relays it to the browser

**Files:**
- Create: `apps/customer-ui/server/main.py`, `apps/customer-ui/server/bootstrap.py`, `apps/customer-ui/server/relay.py`, `apps/customer-ui/server/requirements.txt`, `apps/customer-ui/server/tests/test_relay.py`, `apps/customer-ui/server/pytest.ini`
- Modify: `apps/customer-ui/Dockerfile` (second stage), delete `apps/customer-ui/nginx.conf`
- Modify: `apps/customer-ui/src/a2a.ts`, `apps/customer-ui/src/a2a.test.ts`, `apps/customer-ui/src/app/page.tsx`
- Modify: `deploy/k8s/customer-ui.yaml` (command, env, probes if any)

**Interfaces:**
- Consumes: Task 2's push: `POST /a2a/push`, header `X-A2A-Notification-Token`, body = A2A `Task` JSON (`id`, `contextId`, `status.state`, `status.message.parts`).
- Produces (browser-facing, same origin): `POST /claims/push-config` → `{"url": PUSH_URL, "token": "<43 urlsafe chars>"}`; `GET /claims/events` with header `x-claim-token` → SSE, one `data:` frame per snapshot (the Task JSON as pushed), ends after a terminal one. Static export at `/` as nginx served it (`try_files $uri $uri.html $uri/index.html /index.html`).

- [ ] **Step 1: Failing relay tests** (`server/tests/test_relay.py`, Starlette `TestClient` / httpx `ASGITransport`):

```python
def test_push_config_issues_a_fresh_token_and_the_fixed_url(client):
    a, b = client.post("/claims/push-config").json(), client.post("/claims/push-config").json()
    assert a["url"] == PUSH_URL and a["token"] != b["token"] and len(a["token"]) >= 32

def test_a_push_without_a_token_or_with_a_wrong_one_is_rejected(client):
    assert client.post("/a2a/push", json=TASK).status_code == 401
    assert client.post("/a2a/push", json=TASK, headers={"x-a2a-notification-token": "nope"}).status_code == 401

def test_a_push_for_a_second_task_on_the_same_token_is_rejected(client):
    tok = client.post("/claims/push-config").json()["token"]
    assert _push(client, tok, TASK).status_code == 204
    assert _push(client, tok, {**TASK, "id": "other"}).status_code == 409

async def test_a_push_before_the_subscriber_is_delivered_at_once(app):
    # terminal push lands first; the events stream returns it and ends
    ...

async def test_the_subscriber_waits_and_ends_on_the_terminal_push(app):
    # open events, push input-required (frame), push completed (frame), stream ends
    ...

def test_events_without_a_known_token_is_rejected(client):
    assert client.get("/claims/events", headers={"x-claim-token": "nope"}).status_code == 401

def test_the_token_never_appears_in_a_span(app, span_exporter):
    # after push-config, push and events: no span attribute value contains the token
    ...

def test_the_static_export_is_served_like_nginx_did(client, tmp_static):
    # "/" -> index.html; "/foo" -> foo.html if present; unknown path -> index.html
    ...
```

- [ ] **Step 2: Run, expect FAIL** (`cd apps/customer-ui/server && python -m pytest -q`).

- [ ] **Step 3: Implement `relay.py`** (pure state, no I/O):

```python
"""Claim outcomes pushed by the claims-agent, held for the browser that asked.

One token per claim: issued to the browser, handed by the browser to the
claims-agent in its push config, sent back by the SDK as
X-A2A-Notification-Token. The first push binds the token to its task id. A
waiter blocks on an asyncio.Event, never on a timer: the relay has no loop.
Bounded: the oldest token is forgotten past MAX_CLAIMS.
"""
from __future__ import annotations

import asyncio
import hmac
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field

MAX_CLAIMS = 1000
TERMINAL = {"completed", "failed", "canceled", "rejected"}


@dataclass
class _Claim:
    task_id: str | None = None
    snapshots: list[dict] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class Relay:
    def __init__(self, max_claims: int = MAX_CLAIMS):
        self._claims: OrderedDict[str, _Claim] = OrderedDict()
        self._max = max_claims

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        self._claims[token] = _Claim()
        while len(self._claims) > self._max:
            self._claims.popitem(last=False)
        return token

    def _find(self, token: str | None) -> _Claim | None:
        if not token:
            return None
        for known, claim in self._claims.items():
            if hmac.compare_digest(known, token):
                return claim
        return None

    def push(self, token: str | None, task: dict) -> int:
        claim = self._find(token)
        if claim is None:
            return 401
        if claim.task_id is None:
            claim.task_id = task.get("id")
        elif claim.task_id != task.get("id"):
            return 409
        claim.snapshots.append(task)
        claim.changed.set()
        return 204

    def known(self, token: str | None) -> bool:
        return self._find(token) is not None

    async def follow(self, token: str):
        """Yield every snapshot for this token, in order, until a terminal one."""
        claim = self._find(token)
        sent = 0
        while True:
            while sent < len(claim.snapshots):
                snap = claim.snapshots[sent]
                sent += 1
                yield snap
                if snap.get("status", {}).get("state") in TERMINAL:
                    return
            claim.changed.clear()
            if sent < len(claim.snapshots):
                continue
            await claim.changed.wait()
```

(`follow` waits on an event; there is no timeout poll. The linear `_find` is fine at `MAX_CLAIMS` and keeps the comparison constant-time.)

- [ ] **Step 4: Implement `main.py` and `bootstrap.py`.** `bootstrap.py`: copy `apps/reviewer-ui/bootstrap.py`'s OTel setup with `service.name=customer-ui-server` (check the registry/collector for how service names are listed; add the name where M9 lists services). `main.py`: a Starlette app with routes `POST /claims/push-config`, `POST /a2a/push` (reads `x-a2a-notification-token`, returns `Relay.push`'s status), `GET /claims/events` (401 unless `relay.known(token)`; `StreamingResponse` of `data: <json>\n\n` frames from `relay.follow`, media type `text/event-stream`, header `cache-control: no-store`), `GET /healthz`, and a static handler reproducing nginx's `try_files $uri $uri.html $uri/index.html /index.html` from `STATIC_DIR` (default `/app/static`). `StarletteInstrumentor().instrument_app(app)`. `PUSH_URL` from env `CUSTOMER_UI_PUSH_URL`, default `http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push`. Run on port 8080. `requirements.txt`: `starlette` (latest release, verify on PyPI), `uvicorn==0.34.0`, the OTel pins reviewer-ui uses, `opentelemetry-instrumentation-starlette==0.65b0`; comment each like reviewer-ui's file.

- [ ] **Step 5: Run the server tests, expect PASS.**

- [ ] **Step 6: Browser client, failing vitest first.** In `a2a.test.ts` add: `streamClaim` sends `configuration.pushNotificationConfig` `{url, token}` from `POST /claims/push-config`; `streamClaim` reads the stream to its end (does not cancel at input-required); `awaitOutcome` opens `GET /claims/events` with `x-claim-token` and resolves on a terminal frame; a dropped events stream calls `tasks/get` once and reopens; five empty drops in a row throw (existing `maxEmptyDrops`); no URL ever contains the token. Delete the `resubscribe` tests.

- [ ] **Step 7: Implement in `a2a.ts` and `page.tsx`.** `buildStreamRequest(claim, push?)` adds `params.configuration = {pushNotificationConfig: push}` when given. New `getPushConfig(uiBase)` → `{url, token}` (same-origin `POST /claims/push-config`). `streamClaim` keeps reading frames until the body ends, returning the last snapshot. `awaitOutcome(baseUrl, uiBase, taskId, token, onEvent)` follows `GET ${uiBase}/claims/events` with `x-claim-token` (fetch + the existing `frames()` reader); on a drop: `getTask` once, return if terminal, else reopen; keep `maxEmptyDrops`. Delete `resubscribe`. Build headers before the first `await`, as the file's Zone.js comment requires. In `page.tsx` fetch the push config before `streamClaim` and pass the token to `awaitOutcome`; `uiBase` is `window.location.origin`. The `northstar.claim.wait` span and its link stay as they are.

- [ ] **Step 8: Run vitest, expect PASS** (`cd apps/customer-ui && npm test`).

- [ ] **Step 9: Image and Deployment.** Dockerfile second stage:

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/bootstrap.py server/relay.py server/main.py ./
COPY --from=build /app/out /app/static
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["python", "main.py"]
```

Delete `nginx.conf`. In `deploy/k8s/customer-ui.yaml` add env `CUSTOMER_UI_PUSH_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT` (copy reviewer-ui's value), and point any probe at `/healthz`. `grep -rn "nginx" apps/customer-ui deploy platform/backstage tests docs` and update every reference that describes the customer-ui image.

- [ ] **Step 10: Commit.**

```bash
git add apps/customer-ui/server apps/customer-ui/Dockerfile apps/customer-ui/src/a2a.ts apps/customer-ui/src/a2a.test.ts apps/customer-ui/src/app/page.tsx deploy/k8s/customer-ui.yaml
git rm apps/customer-ui/nginx.conf
git commit -m "feat(m10): customer-ui serves its static export from a small server that receives the A2A push and relays it to the browser"
```

---

### Task 4: Reach, the registry, the harness and the live tests

**Files:**
- Modify: `deploy/platform/cilium-policies.yaml` (claims-agent egress rule 5; customer-ui ingress if it has an ingress policy — check `grep -n "customer-ui" deploy/platform/cilium-policies.yaml`)
- Modify: `deploy/observability/collector-config.yaml` only if the customer-ui server needs a new OTLP route (it should not: reviewer-ui's path applies)
- Modify: `telemetry/registry/` (`northstar.a2a.link.reason`, `northstar.a2a.resume` span if registered spans are listed, the `customer-ui-server` service where services are listed)
- Modify: `tests/harness/live.py` (`submit_claim` push + read-to-end; new `await_outcome`; delete `resubscribe`; `release_wait` docstring)
- Modify: `tests/harness/jaeger.py`, `tests/unit/test_jaeger_assertions.py` (delete `WAIT_POLLING_SPANS` and `steady_ignoring`)
- Modify: `tests/e2e/test_human_in_the_loop.py`, `tests/e2e/test_egress_policy.py`, `scripts/demo.py`
- Modify: `scripts/start-demo.sh` only if the harness needs the customer-ui forward it does not already have (check its forward list)

**Interfaces:**
- Consumes: Tasks 1-3 (link attribute name, push URL, `/claims/push-config`, `/claims/events`).
- Produces: `live.submit_claim(...)` now returns after the stream ends and its dict gains `"push_token"`; `live.await_outcome(push_token, *, timeout, drop_after=None) -> {"state", "reply", "dropped"?}`.

- [ ] **Step 1: Reach rule.** Append to `claims-agent-egress-allowlist`:

```yaml
    # 5. The customer's push receiver, and nothing else on it.
    #
    # M10: a claim decided by a human is pushed to the customer-ui server's
    # /a2a/push (a2a-sdk BasePushNotificationSender). The URL comes from the
    # client, so claims-agent checks it against an exact allowlist (push.py);
    # this rule is the same fact on the wire. One port, POST to one path.
    - toEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: northstar-agents
            app: customer-ui
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
          rules:
            http:
              - method: POST
                path: "/a2a/push"
```

- [ ] **Step 2: Dataplane test** in `tests/e2e/test_egress_policy.py`, in that file's existing style (it already execs probes from pods): from the claims-agent pod, `POST customer-ui:8080/a2a/push` is answered by the server (401 without a token = reached), `GET customer-ui:8080/` is denied (403 from Cilium's L7 proxy), and any other customer-ui port is unreachable.

- [ ] **Step 3: Registry.** Add `northstar.a2a.link.reason` (enum `resume`, `stability: development`, brief: "why this span links to another trace") to the a2a group in `telemetry/registry/` (find the file that defines `northstar.a2a.task.id`). Register `northstar.a2a.resume` the way M9 registers other `northstar.*` span names, if it does; if span names are not registered, add it to the brief of the attribute group and leave it. Run `weaver registry check` as M9's tests do (`grep -rn "registry check" tests scripts`).

- [ ] **Step 4: Harness.** In `live.py`, `submit_claim`: get `{url, token}` from the customer-ui's `POST /claims/push-config` (customer-ui forward URL constant, add next to `CLAIMS_AGENT_URL_DEFAULT`), send it as `configuration.pushNotificationConfig`, read the stream **to its end**, return the last snapshot plus `"push_token"`. Add `await_outcome(push_token, timeout, drop_after=None)` reading `GET /claims/events` with `x-claim-token` (same SSE parsing as `_sse_results`). Delete `resubscribe`. Rewrite `release_wait`'s docstring: trace A no longer stays open while a claim waits. In `jaeger.py` delete `WAIT_POLLING_SPANS` and the `steady_ignoring` parameter; in `test_jaeger_assertions.py` delete the test of it; in `scripts/demo.py:553` drop the argument.

- [ ] **Step 5: e2e.** In `test_human_in_the_loop.py`:
  - `test_a_resubscribe_held_open_ten_minutes_delivers_completed` → `test_a_push_waited_on_for_ten_minutes_delivers_completed`, using `live.await_outcome(run["push_token"], timeout=900)` in the thread.
  - `test_a_dropped_stream_reconnects_and_still_sees_the_outcome` → uses `await_outcome(..., drop_after=3)`, then `get_task` once, then `await_outcome` again.
  - `test_trace_a_holds_the_deny_and_no_execute`: drop `steady_ignoring`.
  - New `test_trace_a_is_finished_at_the_hand_off`: file a claim, read trace A once its PDS span is in, wait 12 s **without deciding**, read again: same span count, and no span's start time is later than the `input-required` hand-off span's end + 2 s. Then `live.release_wait` it.
  - New `test_the_resume_runs_in_trace_b_and_links_to_trace_a(approved)`: in trace B, a claims-agent span `northstar.a2a.resume` whose link's trace id is `approved["trace_id"]` with `northstar.a2a.link.reason == "resume"`, and a claims-agent push CLIENT span to `/a2a/push` followed by a `customer-ui-server` SERVER span.

- [ ] **Step 6: Run the unit suite** (`python -m pytest tests/unit -q`). The live suites run in the gate, not here.

- [ ] **Step 7: Commit.**

```bash
git add deploy/platform/cilium-policies.yaml telemetry/registry tests/harness/live.py tests/harness/jaeger.py tests/unit/test_jaeger_assertions.py tests/e2e/test_human_in_the_loop.py tests/e2e/test_egress_policy.py scripts/demo.py
git commit -m "feat(m10): Reach allows the push and nothing else; the registry names the resume link; the harness waits on the push, and trace A is asserted finished at the hand-off"
```

---

### Task 5: docs and the deck

**Files:**
- Modify: `agents/claims-agent/NOTES.md` (an M10 section: what was measured in Task 1; the polling paragraph marked superseded, not deleted), `agents/claims-agent/docs/index.md` ("The waiting claim" rewritten), `apps/customer-ui/docs/` and `NOTES.md` (the server, the token flow), `docs/demo-runbook.md` (delete "open trace A after the decision"; add where the push shows in trace B), `docs/telemetry-contract.md` (trace A ends at the hand-off; the resume link), `README.md` (the flow paragraph about the wait)
- Modify: the deck's HITL trace slide in `talk/slides/` and its note in `talk/notes/` (find with `grep -rln "trace B\|span link" talk/slides talk/notes`), then `python talk/build.py` and commit `talk/index.html` and the generated `talk/talk.css` together
- Modify: `talk/README.md` slide count/timing only from the build's own output

- [ ] **Step 1:** Edit the docs above. Each statement about the wait says: trace A ends at the hand-off; the person's trace B carries the release, the resume and the push; the link joins them; a restart still loses waiting claims.
- [ ] **Step 2:** Deck: the slide shows two traces and one link (A: "agent proposes → platform: a person decides"; B: "person approves → release → resume → push", with the link arrow from B's resume back to A). The note: "Work separated by human time is two traces and a link. Nothing polls while the claim waits." Keep the notes JSON's existing indent per file (10-open-act1 and 50-close: 1 space; 40-act4: 2 spaces). Build, run `python -m pytest tests/unit/test_talk_deck_is_not_m7.py -q`, open the slide with `talk/shoot.py` and look at it.
- [ ] **Step 3: Commit** with explicit paths, `docs(m10): ...`.

---

## After the tasks

1. Review Tasks 1, 3 and 4 (the risky ones), then one whole-branch review on the most capable model; fix wave.
2. Then, and only then, one gate run from a clean HEAD: `./scripts/setup-kind.sh --stage 7`, `./scripts/start-demo.sh`, `./scripts/smoke-test.sh` (the scratchpad `gate-run2.sh` pattern). Never commit while it runs.
3. Record the measured row in `docs/telemetry-contract.md` and stop for Kasper before merging.
