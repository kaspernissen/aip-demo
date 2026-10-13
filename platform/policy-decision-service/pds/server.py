"""The ExtMCP servicer: wire format in, policy decision out.

Everything about the wire shape below is measured against agentgateway
v1.5.0 on this cluster, not assumed. The three facts this file is built on:

1. The caller's SPIFFE ID is in `McpRequest.metadata_context` under `caller`
   (a `google.protobuf.Struct`), put there by the gateway config
   `metadata: {caller: 'jwt.sub'}`. It is never in a forwarded `authorization`
   header.
2. `McpRequest.mcp_request` is the JSON-RPC `params` object as raw UTF-8 JSON
   bytes -- not the whole envelope. The tool name is at `$.name`, the amount at
   `$.arguments.amount` (a JSON *float*: `6200.0`, not `6200`), the currency at
   `$.arguments.currency`. The method is the sibling `McpRequest.method` field.
3. `traceparent` is forwarded in `McpRequest.headers`, so the decision span is
   a real child of the gateway's `tools/call` SERVER span.

And one fact about the way out, measured on agentgateway v1.6.0: a
`PERMISSION_DENIED` rejection on `tools/call` reaches the client as HTTP 200
with an MCP tool result, `{"isError": true, "content": [{"type": "text",
"text": <rejection message>}]}`. `error.data` no longer reaches it (upstream PR
#3526); only `INTERNAL_ERROR` keeps the JSON-RPC error form, and a policy
refusal is not an internal error. So everything the agent needs -- the reason,
the rule, the limit, the requested amount -- rides in the message text, in the
one format `pds/deny_text.py::format_deny` writes.
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
from concurrent import futures
from dataclasses import dataclass

import grpc
from openfeature import api
from openfeature.contrib.hook.opentelemetry import TracingHook
from openfeature.contrib.provider.flagd import FlagdProvider
from opentelemetry.trace import SpanKind, Status, StatusCode

import ext_mcp_pb2 as pb
import ext_mcp_pb2_grpc as pb_grpc

from pds import telemetry
from pds.autonomy import (
    FLAGD_HOST,
    FLAGD_PORT,
    emit_evaluation_event,
    resolve_autonomy,
)
from pds.deny_text import format_deny
from pds.identity import caller_spiffe_id
from pds.rules import Decision, PolicyInput, decide

log = logging.getLogger("pds")

# The autonomy level is resolved per decision through a flag rather than
# compiled in as a constant: the rule table in `rules.py` is already keyed by
# the level, so this is a value change at lookup time, not a shape change --
# `decide()` itself never learns a flag exists.
#
# One client, built once at import time rather than per request: the
# provider owns a gRPC channel and a background connection-monitor thread
# (`GrpcResolver.connect`, spawned by `api.set_provider`'s own init), and
# building one per `CheckRequest` would open one of each per decision for no
# gain. `api.set_provider` is non-blocking (it spawns a daemon thread to run
# `provider.initialize`, measured in
# `openfeature.provider._registry.ProviderRegistry._initialize_provider` --
# `wait_for_init` defaults `False`) so this import never blocks server
# startup on the sidecar being reachable; a request that arrives before the
# provider reports ready resolves through the SDK's own not-ready path, which
# `resolve_autonomy` reads exactly like any other unavailability (measured
# against the live sidecar: `error_code=PROVIDER_NOT_READY`,
# `value=default_value`, i.e. `RESTRICTIVE_LEVEL`).
#
# `TracingHook()` puts a `feature_flag.evaluation` event on whatever span is
# active when the flag is resolved -- `_decide` below resolves inside the
# `policy.decision` span, so that event lands there. `emit_evaluation_event`
# (see `pds/autonomy.py`) sends a separate log record with the fields the
# hook does not.
api.set_provider(FlagdProvider(host=FLAGD_HOST, port=FLAGD_PORT))
_flag_client = api.get_client()
_flag_client.add_hooks([TracingHook()])

# The JSON-RPC application-error range. The exact code is ours because this is
# our error -- and the client never sees it: on agentgateway v1.6.0 a deny is
# returned as an `isError` tool result whose text is the message below, and
# `error.data` (where this code and the decision body used to land) is dropped.
# The body is still sent in `mcp_error` in case a gateway version reads it.
DENY_JSONRPC_CODE = -32003

# Only `tools/call` is configured to reach us (the gateway's `methods` block),
# and it is the only method whose `params` this file knows how to read. Anything
# else is a config change nobody told the policy about, so it is denied rather
# than waved through.
DECIDABLE_METHOD = "tools/call"

DEFAULT_PORT = 9100


@dataclass(frozen=True)
class ToolCall:
    """What we could recover from `mcp_request`. Any field may be missing --
    that is a policy input, handed to `decide`, not an error.

    `currency` is a plain `str` and defaults to empty rather than to "EUR":
    the policy limit does not depend on it (it is display only, in the reason
    string), and inventing a currency we were not told would put a number in
    front of a customer that nobody sent.
    """
    tool: str | None
    amount: float | None
    currency: str


def _amount(value) -> float | None:
    """The amount as a finite float, or None if it is not a number at all.

    `bool` is excluded deliberately -- `isinstance(True, int)` is true in
    Python, and `"amount": true` is not an amount.

    Non-finite values are refused here so they never become a `PolicyInput`.
    `rules.decide` refuses them too, and independently; this is the outer of
    the two layers, and it earns its place twice over. Python's `json` module
    accepts the non-standard literals `NaN`, `Infinity` and `-Infinity` by
    default, and `1e309` overflows to `inf`, so a caller really can put one on
    the wire. And `json.dumps` would happily write `NaN` back out into
    `mcp_error.data.requested` -- which is not valid JSON, so the gateway would
    drop the whole deny body with a `warn!` and the structured refusal a
    caller keys off would silently vanish.

    A JSON integer literal too large for a float raises `OverflowError` on
    `float()` rather than returning `inf`; caught, because `parse_call` must
    never raise.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def parse_call(params: bytes | None) -> ToolCall:
    """Read the tool, amount and currency out of the JSON-RPC `params` bytes.

    Never raises. A parse failure must become a denial (via a `ToolCall` with
    holes in it, which `decide` refuses) and not a gRPC error: the gateway
    routes a gRPC error through `failureMode` and reports it as a PDS *outage*,
    which is a different sentence from a policy refusal both on stage and in a
    trace.
    """
    try:
        payload = json.loads(params) if params else None
    except (ValueError, TypeError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return ToolCall(tool=None, amount=None, currency="")

    tool = payload.get("name")
    if not isinstance(tool, str):
        tool = None

    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    amount = _amount(arguments.get("amount"))

    currency = arguments.get("currency")
    if not isinstance(currency, str):
        currency = ""

    return ToolCall(tool=tool, amount=amount, currency=currency)


def build_result(
    *,
    permit: bool,
    reason: str,
    rule: str,
    limit: float | None,
    requested: float | None = None,
) -> pb.McpRequestResult:
    """The `McpRequestResult` for a decision.

    A permit is `Pass` and nothing else: no mutation, no `header_mutation`, no
    `metadata`. The response side is unused and touching a successful call
    would put the PDS in its data path for no gain.

    A deny carries the same formatted text (`format_deny`) twice, because which
    of `AuthorizationError.reason` and `mcp_error.message` the gateway reads
    into its rejection message is not ours to rely on. On v1.6.0 that text is
    the tool result's `content[0].text`, and the agent parses the tag at its end.
    """
    if permit:
        return pb.McpRequestResult(**{"pass": pb.Pass()})

    text = format_deny(reason, rule, limit, requested)
    body = {
        "code": DENY_JSONRPC_CODE,
        "message": text,
        "data": {
            "rule": rule,
            "limit": limit,
            "requested": requested,
            # Always false. A denial the agent may retry is a denial it can sit
            # on stage retrying.
            "retryable": False,
            "decision": "deny",
        },
    }
    return pb.McpRequestResult(
        error=pb.AuthorizationError(
            code=pb.AuthorizationError.PERMISSION_DENIED,
            reason=text,
            mcp_error=json.dumps(body).encode("utf-8"),
        )
    )


class PolicyServicer(pb_grpc.ExtMcpServicer):
    """`agentgateway.dev.ext_mcp.ExtMcp`, implemented as a policy decision point."""

    def CheckRequest(self, request, context):
        """Decide, and never raise.

        The two `except`s here are not defensive decoration: a gRPC error is
        routed by the gateway's `failureMode` and surfaces as
        `mcpGuardrails checkRequest failed: <status>` -- indistinguishable from
        the PDS being down, and reported as an outage rather than as a
        decision. So every internal failure becomes an explicit deny with the
        rule `internal_error`, which says what actually happened.

        The inner one owns the case where a span exists, so the cause is
        recorded on it. The outer one covers the two steps that happen before
        there is a span to record on: extracting the trace context and starting
        the span itself.
        """
        try:
            parent = telemetry.context_from_headers(request.headers)
            # SpanKind.SERVER: the gateway emits a CLIENT `Guardrail` span for
            # this very RPC, and the server span for an inbound RPC is the
            # matching half of that pairing, which is what makes the decision
            # hop show up as one hop rather than two halves.
            #
            # Note this span is a SIBLING of `Guardrail`, not its child --
            # `Guardrail` (CLIENT) and `policy.decision` (SERVER) carry the
            # *same* parent span id, the gateway's `tools/call`. The gateway
            # propagates the context it received rather than the context of
            # its own client span, so the parent here is `tools/call`.
            #
            # record_exception/set_status_on_exception are off because we do
            # both ourselves below, and because nothing propagates out of this
            # method for the context manager to see anyway -- left on, they
            # would double the exception event on the span.
            with telemetry.tracer().start_as_current_span(
                telemetry.SPAN_NAME,
                context=parent,
                kind=SpanKind.SERVER,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    return self._decide(request, span)
                except Exception as exc:  # noqa: BLE001 -- see the docstring
                    log.exception("policy evaluation failed; denying")
                    span.record_exception(exc)
                    span.set_status(
                        Status(StatusCode.ERROR, "policy evaluation failed"))
                    span.set_attribute(telemetry.ATTR_DECISION, "deny")
                    span.set_attribute(telemetry.ATTR_RULE, "internal_error")
                    span.set_attribute(telemetry.ATTR_RETRYABLE, False)
                    return _internal_error_result()
        except Exception:  # noqa: BLE001 -- see the docstring
            log.exception("CheckRequest failed before a decision span existed; "
                          "denying")
            return _internal_error_result()

    def _decide(self, request, span) -> pb.McpRequestResult:
        metadata = dict(request.metadata_context)
        caller = caller_spiffe_id(metadata, list(request.headers))
        call = parse_call(request.mcp_request if request.HasField("mcp_request")
                          else None)

        # Resolved unconditionally: every `policy.decision` span carries the
        # level in effect, even on the `_undecidable_method` path below.
        resolution = resolve_autonomy(caller, _flag_client)
        # The second of "two records per evaluation" -- a log record
        # carrying the full seven-field set, alongside (not instead of) the
        # span event `TracingHook` already put on the active span inside
        # `resolve_autonomy` above. See `pds/autonomy.py`'s comment for why
        # this is a separate call rather than folded into `resolve_autonomy`
        # itself.
        emit_evaluation_event(resolution, caller)

        if request.method != DECIDABLE_METHOD:
            # Not reachable under the committed gateway config, which sends us
            # `tools/call` only. Fail closed rather than assume a method whose
            # params we cannot read is harmless.
            decision = _undecidable_method(request.method)
        else:
            decision = decide(PolicyInput(
                caller=caller,
                tool=call.tool or "",
                amount=call.amount,
                currency=call.currency,
                autonomy_level=resolution.level,
            ))

        span.set_attribute(telemetry.ATTR_DECISION,
                           "permit" if decision.permit else "deny")
        span.set_attribute(telemetry.ATTR_RULE, decision.rule)
        # The level actually used, plus why -- so a reader can tell
        # "autonomous because the contract says so" (reason TARGETING_MATCH
        # or STATIC) from "assisted because we could not ask" (reason ERROR).
        span.set_attribute(telemetry.ATTR_AUTONOMY, resolution.level)
        span.set_attribute(telemetry.ATTR_AUTONOMY_REASON, resolution.reason)
        # The ruleset version goes on this span too, not only on the log
        # record `emit_evaluation_event` writes -- the audit trail reads this
        # span, and must not have its correctness depend on the logs
        # pipeline as well. Absent like every other optional field below:
        # the live sidecar's `autonomy-level` flag carries no
        # `flagSetId`/`version` metadata today (measured, `flag_metadata`
        # comes back `{}`), so this is routinely missing rather than always
        # present, and a missing version must read as missing.
        if resolution.version is not None:
            span.set_attribute(telemetry.ATTR_FLAG_VERSION, resolution.version)
        span.set_attribute(telemetry.ATTR_RETRYABLE, decision.retryable)
        # Absent values are left off the span rather than written as "" or 0:
        # an attribute that is missing reads as missing, whereas a zero amount
        # reads as a request for nothing.
        if caller is not None:
            span.set_attribute(telemetry.ATTR_CALLER, caller)
        if call.tool is not None:
            span.set_attribute(telemetry.ATTR_TOOL, call.tool)
        if call.amount is not None:
            span.set_attribute(telemetry.ATTR_AMOUNT, call.amount)
        if call.currency:
            span.set_attribute(telemetry.ATTR_CURRENCY, call.currency)
        if decision.limit is not None:
            span.set_attribute(telemetry.ATTR_LIMIT, decision.limit)

        log.info("decision=%s rule=%s method=%s tool=%s caller=%s",
                 "permit" if decision.permit else "deny", decision.rule,
                 request.method, call.tool, caller)

        # The span status stays unset on a deny. A refusal is this service
        # working correctly, and marking it ERROR would put a red span in the
        # trace for the one thing this service exists to do.
        return build_result(
            permit=decision.permit,
            reason=decision.reason,
            rule=decision.rule,
            limit=decision.limit,
            requested=call.amount,
        )

    def CheckResponse(self, request, context):
        """`Pass`, unmodified.

        The response side is not used. Mutating it would put the PDS in
        the data path of every successful call with nothing to gain, and the
        gateway treats an unparseable mutation as a protocol violation.
        """
        return pb.McpResponseResult(**{"pass": pb.Pass()})


def _internal_error_result() -> pb.McpRequestResult:
    """The deny an internal failure becomes. Deliberately says the service
    failed, rather than implying the policy refused: the two are different
    facts and the person reading the refusal needs the true one."""
    return build_result(
        permit=False,
        reason=("the policy decision service could not evaluate this request "
                "and refused it"),
        rule="internal_error",
        limit=None,
    )


def _undecidable_method(method: str) -> Decision:
    return Decision(
        permit=False,
        reason=f"the policy decision service cannot evaluate {method!r}",
        rule="undecidable_method",
    )


def serve() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    telemetry.configure()

    port = int(os.environ.get("PDS_PORT", DEFAULT_PORT))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_ExtMcpServicer_to_server(PolicyServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("policy-decision-service listening on %s, autonomy level "
             "resolved per decision from flagd at %s:%s",
             port, FLAGD_HOST, FLAGD_PORT)

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    stopping.wait()
    log.info("shutting down")
    server.stop(grace=5).wait()


if __name__ == "__main__":
    serve()
