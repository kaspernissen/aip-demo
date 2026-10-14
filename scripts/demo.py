#!/usr/bin/env python3
"""The five-minute demo driver. Run it as `scripts/demo <command>`.

    guard                 no identity -> 401, around the gateway -> dropped,
                          with settlement-agent's SPIRE identity -> its tools
    claim <amount>        a real claim through claims-agent and the model, the
                          outcome, the span tree from Jaeger, a Jaeger link
    autonomy <level>      flip the one live value: assisted | autonomous
    keystone              the agent tries to edit what constrains it
    evidence              who decided, rule, limit, identity, outcome, human
    reset                 flag assisted, over-grant absent, state cleared
    ceiling               (off stage) the gateway's own CEL ceiling: a 403 before the PDS

Design: docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md, section 7.

**No new mechanism.** Everything here is code the tests already prove, imported
from `tests/harness/` (`live.py`, `contract.py`, `jaeger.py`) -- claim submission
over A2A, server-side SVID minting, direct MCP calls through the gateway, the
flag patch with propagation verified, the OpenSearch evidence join. The reach
probe is `scripts/reach-probe.py`, the very file `scripts/beat-reach.sh` runs.
So the stage path and the tested path are the same code, and
`tests/e2e/test_demo_flow.py` runs these commands, not a re-implementation.

**Written for a room.** Short lines, at most ~100 columns, the decisive word
highlighted, each step says what it is about to do before it does it. A
refusal is shown as a success of the platform, in the colour of one.

**Exit status is the point.** Each command exits non-zero when the expected
outcome does not happen -- a refusal that executes, a drop that connects, a
keystone that does not fire -- so a rehearsal that "worked" cannot hide a ring
that is open. Every network call and subprocess carries a timeout.

**No secrets on screen.** JWT-SVIDs are minted at runtime and used; only their
`sub` and `aud` are ever printed.

State: `.demo/state.json` (git-ignored; `DEMO_STATE_DIR` moves it) remembers this run's claim ids and when
the run started, so `evidence` reports this run's claims and nothing older.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The harness is imported as `tests.harness`, exactly as the e2e tests import it.
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from tests.harness import contract, live  # noqa: E402
from tests.harness.jaeger import JaegerClient, Trace  # noqa: E402

# --- what the demo knows about the platform -----------------------------------
# Each of these is also written in a file the driver does not own; a unit test
# (tests/unit/test_demo_driver.py) compares them to that file, so a drift is a
# red test and not a wrong thing said on stage.

# Overridable so the e2e test keeps its run's claims out of an operator's own
# rehearsal state.
STATE_DIR = Path(os.environ.get("DEMO_STATE_DIR") or ROOT / ".demo")
STATE_FILE = STATE_DIR / "state.json"
OVERGRANT = ROOT / "deploy" / "demo" / "keystone-overgrant.yaml"
REACH_PROBE = ROOT / "scripts" / "reach-probe.py"

JAEGER_API = os.environ.get("JAEGER_URL", "http://localhost:16686")
# The host URL setup-kind.sh and start-demo.sh print for Jaeger: a kind host
# port, not a forward. Overridable for a laptop that maps it elsewhere.
JAEGER_UI = os.environ.get("JAEGER_UI_URL", "http://localhost:16686")

AGENTS_NS = "northstar-agents"
TOOLS_NS = "northstar-tools"
PLATFORM_NS = "northstar-platform"
CONTROL_URL = (
    "http://settlement-agent.northstar-agents.svc.cluster.local:9002"
    "/.well-known/agent-card.json"
)
DIRECT_URL = "http://payments-mcp.northstar-tools.svc.cluster.local:8000/mcp"
# A Cilium drop presents as a connect timeout. Long enough that a slow-but-
# allowed connection is not mistaken for a drop, short enough for a stage.
CONNECT_TIMEOUT_S = 8

AGENT = "system:serviceaccount:northstar-agents:settlement-agent"
POLICY = "agents-cannot-edit-their-own-authority"
FLAG = "featureflag/northstar-autonomy"
OVERGRANT_OBJECTS = ("role", "rolebinding")
OVERGRANT_NAME = "keystone-demo-overgrant"

GATEWAY_SPAN = "tools/call"
GUARDRAIL_SPAN = "Guardrail"
EXECUTE_HOP = "POST /payments/{paymentId}/execute"
PROPOSE_HOP = "POST /payments/propose"
REFUSE_HOP = "POST /payments/{paymentId}/refuse"

# OpenSearch is near-real-time and the collector batches on a 1s timeout;
# bounded, and exceeding it is a failure (that is what a dropped span looks like).
EVIDENCE_WAIT_S = 90.0
TRACE_WAIT_S = 90.0
WIDTH = 100


class Fail(Exception):
    """The expected outcome did not happen. Printed, then exit status 1."""


# --- output, written for a room -----------------------------------------------

_COLOR = (
    os.environ.get("DEMO_COLOR") == "1"
    or (sys.stdout.isatty() and "NO_COLOR" not in os.environ)
)

_STYLES = {
    # the decisive word: inverse video, so it reads from the back of a room
    "stop": "1;97;41",   # white on red   -- something was stopped
    "go": "1;30;42",     # black on green -- something went through
    "info": "1;97;44",   # white on blue  -- a fact
    "dim": "2",
    "bold": "1",
    "head": "1;96",
    "red": "1;31",
}


def paint(text: str, style: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{_STYLES[style]}m{text}\033[0m"


def word(text: str, kind: str) -> str:
    """The one word on a line that matters, highlighted."""
    return paint(f" {text} ", kind) if _COLOR else f"[{text}]"


def out(line: str = "") -> None:
    print(line, flush=True)


def title(text: str) -> None:
    out()
    out(paint(text, "head"))
    out(paint("-" * min(len(text), WIDTH), "dim"))


def step(text: str) -> None:
    out()
    out(paint(text, "bold"))


def doing(text: str) -> None:
    """What is about to run, before it runs."""
    out(paint(f"  $ {text}", "dim"))


def say(text: str, indent: int = 2) -> None:
    pad = " " * indent
    for line in textwrap.wrap(text, WIDTH - indent) or [""]:
        out(pad + line)


def kv(key: str, value: str, indent: int = 4, key_width: int = 17) -> None:
    out(f"{' ' * indent}{paint(key.ljust(key_width), 'dim')}{value}")


def short_id(spiffe: str | None) -> str:
    """`.../ns/northstar-agents/sa/settlement-agent` -> `settlement-agent`, for
    lines that would not otherwise fit; the full ID is printed where it is the
    point."""
    return (spiffe or "-").rsplit("/", 1)[-1]


# --- state ----------------------------------------------------------------------


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# --- helpers over the cluster ---------------------------------------------------


def flag_level() -> str:
    """The contract's value for the targeted caller, resolved by flagd itself."""
    return contract.resolved_level()


def pod_fingerprint() -> dict[str, tuple[str, str, int]]:
    """Every pod in the two namespaces the flip could possibly disturb, as
    (uid, start time, restart count). Start times are what a restart changes
    and what the audience would see in `kubectl get pods`."""
    pods: dict[str, tuple[str, str, int]] = {}
    for ns in (AGENTS_NS, PLATFORM_NS):
        result = live.kubectl("-n", ns, "get", "pods", "-o", "json", timeout=30)
        if result.returncode != 0:
            raise Fail(f"could not list pods in {ns}: {result.stderr.strip()}")
        for pod in json.loads(result.stdout)["items"]:
            statuses = pod.get("status", {}).get("containerStatuses", [])
            pods[f"{ns}/{pod['metadata']['name']}"] = (
                pod["metadata"]["uid"],
                pod.get("status", {}).get("startTime", ""),
                sum(cs.get("restartCount", 0) for cs in statuses),
            )
    if not pods:
        raise Fail("no pods found -- is the kind-northstar cluster up?")
    return pods


def overgrant_present() -> bool:
    result = live.kubectl(
        "-n", PLATFORM_NS, "get", ",".join(OVERGRANT_OBJECTS), OVERGRANT_NAME,
        "--ignore-not-found", "-o", "name", timeout=30,
    )
    # Judged on OUTPUT, not exit status: `get` exits 0 on some "found nothing"
    # paths, and output is the one thing an absent object cannot fake.
    if result.returncode != 0:
        raise Fail(f"could not look for the over-grant: {result.stderr.strip()}")
    return bool(result.stdout.strip())


def delete_overgrant() -> None:
    live.kubectl("delete", "-f", str(OVERGRANT), "--ignore-not-found", timeout=60)


def gateway_headers(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


# --- guard ----------------------------------------------------------------------


def probe_from_claims_agent(pod: str, url: str, method: str) -> str:
    """The shared prober, run inside claims-agent. One line of output."""
    result = live.kubectl(
        "-n", AGENTS_NS, "exec", pod, "-c", "claims-agent", "--",
        "python3", "-c", REACH_PROBE.read_text(), url, method, str(CONNECT_TIMEOUT_S),
        timeout=60,
    )
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        raise Fail(f"the probe did not run inside {pod}: {result.stderr.strip()[:300]}")
    return lines[-1]


def cmd_guard(_: argparse.Namespace) -> None:
    title("GUARD  who may call the payment tools, and by which road")

    with live.gateway_port_forward() as gateway:
        step("1  No identity")
        say("A caller with no SPIRE identity asks the gateway what tools exist.")
        doing("POST gateway/mcp  tools/list   (no token)")
        outcome, names = live.list_tools_directly(gateway, {})
        if outcome.http_status != live.UNAUTHORIZED or names:
            raise Fail(
                f"the gateway did not answer 401 to a caller with no identity: {outcome!r}"
            )
        out(f"  -> HTTP {word('401', 'stop')}  {outcome.body.strip()}")
        say("And a direct call to the payment tool:")
        doing("POST gateway/mcp  tools/call execute_payment   (no token)")
        called = live.call_tool_directly(
            gateway, live.EXECUTE_TOOL, {"payment_id": "PAY-0", "amount": 1}, headers={}
        )
        if called.http_status != live.UNAUTHORIZED or called.tool_result is not None:
            raise Fail(f"the gateway let an unauthenticated call through: {called!r}")
        out(f"  -> HTTP {word('401', 'stop')}  nothing forwarded")

        step("2  Around the gateway")
        pods = live.kubectl(
            "-n", AGENTS_NS, "get", "pod", "-l", "app=claims-agent",
            "-o", "jsonpath={.items[0].metadata.name}", timeout=30,
        )
        pod = pods.stdout.strip()
        if not pod:
            raise Fail("no claims-agent pod found")
        say("From inside claims-agent: no SVID, no business at the tool.")
        doing("GET   settlement-agent:9002   (the one peer it may call: the control)")
        control = probe_from_claims_agent(pod, CONTROL_URL, "GET")
        if not control.startswith("REACHED"):
            raise Fail(
                f"the control did not answer ({control}), so a drop below would prove "
                "an outage and not a policy"
            )
        out(f"  -> {word('REACHED', 'go')}  {control.split(' ', 1)[1]}  (the network works)")
        doing(f"POST  payments-mcp:8000/mcp  initialize, straight to the tool, {CONNECT_TIMEOUT_S}s timeout")
        direct = probe_from_claims_agent(pod, DIRECT_URL, "POST")
        if not direct.startswith("DROPPED"):
            raise Fail(
                f"claims-agent was not dropped on the way to payments-mcp ({direct}): "
                "either it got through, or something refused it instead of the "
                "dataplane dropping it. The gateway is not provably the only way in."
            )
        out(f"  -> {word('DROPPED', 'stop')}  no answer, no 401. The packet never arrived.")

        step("3  With settlement-agent's identity")
        say("SPIRE mints the same JWT-SVID settlement-agent holds (not shown).")
        doing("spire-server jwt mint   sub=settlement-agent  aud=agentgateway")
        token = live.mint_svid(live.GATEWAY_AUDIENCE)
        shown = live.claims(token)
        kv("sub", shown.get("sub", "?"))
        kv("aud", ", ".join(shown.get("aud", [])) if isinstance(shown.get("aud"), list) else str(shown.get("aud")))
        doing("POST gateway/mcp  tools/list   (bearer SVID)")
        listed, names = live.list_tools_directly(gateway, gateway_headers(token))
        if listed.http_status != 200 or not names:
            raise Fail(f"the gateway did not list tools for a genuine SVID: {listed!r}")
        out(f"  -> HTTP {word('200', 'go')}  tools settlement-agent may call:")
        descriptions = {t["name"]: t.get("description", "") for t in listed.tool_result["tools"]}
        for name in names:
            first = descriptions[name].strip().splitlines()[0] if descriptions[name] else ""
            out(f"       {paint(name.ljust(16), 'bold')}{first[: WIDTH - 24]}")
        say("Listed is not permitted: every call is decided, one by one.")
    out()
    out(paint("guard holds: no identity, no road, one way in.", "bold"))


# --- claim ----------------------------------------------------------------------


def _children(trace: Trace) -> dict:
    kids: dict = {}
    for s in trace.spans:
        kids.setdefault(s.parent_id, []).append(s)
    return kids


def _gateway_execute_spans(trace: Trace):
    return [
        s for s in trace.spans
        if s.service == "agentgateway"
        and s.operation == GATEWAY_SPAN
        and s.attrs.get("gen_ai.tool.name") == live.EXECUTE_TOOL
    ]


def _decision_of(trace: Trace):
    spans = [
        s for s in trace.spans
        if s.service == live.PDS_SERVICE
        and s.operation == live.DECISION_SPAN
        and s.attrs.get(live.ATTR_POLICY_TOOL) == live.EXECUTE_TOOL
    ]
    return spans


def render_span_tree(trace: Trace, executed_expected: bool) -> dict:
    """Print the part of the trace that matters on stage, return the facts.

    Not the whole trace -- 130 spans of A2A plumbing would bury the point. The
    tree shows the road the money would take: the agent's tool call, the
    gateway, the guardrail hop to the policy decision service, and the three
    payments-api operations, with the one that moves money named and, when it
    never happened, shown as absent.
    """
    gateway_spans = _gateway_execute_spans(trace)
    if len(gateway_spans) != 1:
        raise Fail(
            f"expected exactly one gateway tools/call for {live.EXECUTE_TOOL}, "
            f"found {len(gateway_spans)}"
        )
    gateway = gateway_spans[0]
    kids = _children(trace)
    # MEASURED: `Guardrail` (the gateway's own span for its hook call) and the
    # PDS's `policy.decision` are SIBLINGS under the gateway's tools/call span,
    # not parent and child -- the PDS parents to the gateway span it was called
    # from, via the traceparent the gateway forwards on the hook.
    children = kids.get(gateway.span_id, [])
    has_guardrail = any(c.operation == GUARDRAIL_SPAN for c in children)
    decisions = [
        c for c in children
        if c.service == live.PDS_SERVICE and c.operation == live.DECISION_SPAN
    ]
    if len(decisions) != 1 or not has_guardrail:
        raise Fail(
            "expected the gateway's tools/call to hold one Guardrail span and one "
            f"policy.decision span, found guardrail={has_guardrail}, decisions={len(decisions)}"
        )
    decision = decisions[0]
    a = decision.attrs
    verdict = a.get(live.ATTR_DECISION)
    denied = verdict == "deny"

    out(f"  trace {trace.trace_id}")
    settlement = [
        s for s in trace.spans
        if s.service == "settlement-agent" and s.operation == f"execute_tool {live.EXECUTE_TOOL}"
    ]
    if settlement:
        out(f"  settlement-agent  {paint('execute_tool execute_payment', 'bold')}"
            "   the model asks")
    status = gateway.attrs.get("http.response.status_code")
    out(f"  +- agentgateway  {paint('tools/call execute_payment', 'bold')}"
        f"   HTTP {status}  {gateway.attrs.get('otel.status_code', 'OK')}")
    out(f"      jwt.sub  {short_id(gateway.attrs.get('jwt.sub'))}")
    if denied and str(status) == "200":
        out(paint("      HTTP 200: the answer is a tool result marked isError. A no is not an HTTP failure.", "dim"))
    out(f"    +- {GUARDRAIL_SPAN}   the gateway asks the decision service")
    mark = word("DENY", "stop") if denied else word("PERMIT", "go")
    out(f"    +- {live.PDS_SERVICE}  {paint('policy.decision', 'bold')}  {mark}")
    kv("rule", str(a.get(live.ATTR_POLICY_RULE)), indent=10, key_width=11)
    if a.get(live.ATTR_POLICY_LIMIT) is not None:
        kv("limit", f"{a.get(live.ATTR_POLICY_LIMIT)}  (requested {a.get(live.ATTR_POLICY_AMOUNT)})", indent=10, key_width=11)
    kv("caller", short_id(a.get(live.ATTR_POLICY_CALLER)), indent=10, key_width=11)
    kv("level", str(a.get(live.ATTR_AUTONOMY)), indent=10, key_width=11)
    description = gateway.attrs.get("otel.status_description")
    if description:
        out()
        say("gateway: " + str(description), indent=4)

    out()
    out("  payments-api spans in this trace:")
    api = [s for s in trace.spans if s.service == live.PAYMENTS_API]
    names = sorted({s.operation for s in api})
    for op, label in ((PROPOSE_HOP, "records the proposal; moves no money"),
                      (REFUSE_HOP, "records the refusal; moves no money")):
        if op in names:
            out(f"    {paint('present', 'dim')}  {op}   {label}")
    executed = EXECUTE_HOP in names
    if executed:
        out(f"    {word('PRESENT', 'go')}  {EXECUTE_HOP}   money moved")
    else:
        out(f"    {word('ABSENT', 'stop')}   {EXECUTE_HOP}   money did not move")
    return {
        "verdict": verdict,
        "rule": a.get(live.ATTR_POLICY_RULE),
        "limit": a.get(live.ATTR_POLICY_LIMIT),
        "requested": a.get(live.ATTR_POLICY_AMOUNT),
        "level": a.get(live.ATTR_AUTONOMY),
        "gateway_http_status": status,
        "gateway_otel_status": gateway.attrs.get("otel.status_code"),
        "executed_span_present": executed,
        "payments_api_ops": names,
    }


def cmd_claim(args: argparse.Namespace) -> None:
    amount = float(args.amount)
    claim_id = f"CLM-DEMO-{uuid.uuid4().hex[:6].upper()}"
    title(f"CLAIM  {claim_id}   EUR {amount:,.0f}")

    level_before = flag_level()
    state = load_state()
    if not state.get("claims"):
        # The run starts with its first claim; evidence is bounded to here.
        state = {
            "run_id": uuid.uuid4().hex[:8],
            "window_start_ms": live.window_start_ms_now(),
            "claims": [],
        }
    kv("autonomy level", paint(level_before, "bold"), indent=2)
    out()
    say("A claimant files the claim. claims-agent hands it to settlement-agent.")
    say("The model decides what to propose. The platform decides what happens.")
    doing(f"A2A message/stream  claims-agent  claim {claim_id}  EUR {amount:,.0f}")
    started = time.monotonic()
    with live.claims_agent_route() as url:
        run = live.submit_claim(claim_id, amount, claims_agent_url=url, timeout=170)
    reply = run["reply"]
    took = time.monotonic() - started
    status = reply.get("status")
    out(f"  -> {took:0.0f}s")
    out()
    if status == "awaiting_approval":
        out(f"  Outcome  {word('WAITING', 'stop')}  for a human. Nothing was paid.")
    elif status == "executed":
        out(f"  Outcome  {word('EXECUTED', 'go')}  by the platform. The payment was made.")
    else:
        raise Fail(f"the claim ended {status!r}, neither waiting for a human nor executed: {reply}")
    out()
    out(paint("  The model's explanation to the claimant:", "dim"))
    for para in str(reply.get("narrative", "")).split("\n"):
        for line in textwrap.wrap(para, WIDTH - 6) or [""]:
            out("    " + line)

    state["claims"].append(
        {"id": claim_id, "amount": amount, "status": status, "trace_id": run["trace_id"],
         "payment_id": reply.get("paymentId"), "level": level_before}
    )
    save_state(state)

    step("The same event, as the trace saw it")
    doing(f"Jaeger  trace {run['trace_id'][:8]}...  (waiting for every service to land)")
    trace = JaegerClient(JAEGER_API).wait_for_trace_id(
        run["trace_id"],
        services={"agentgateway", live.PDS_SERVICE, live.PAYMENTS_API},
        timeout=TRACE_WAIT_S,
        # A claim waiting for a human keeps adding SDK polling spans to its trace.
        steady_ignoring=JaegerClient.WAIT_POLLING_SPANS,
    )
    facts = render_span_tree(trace, executed_expected=(status == "executed"))
    out()
    out(f"  Jaeger  {JAEGER_UI}/trace/{run['trace_id']}")

    # --- the outcome has to be the expected one, and the three records agree --
    problems = []
    if args.expect and status != args.expect:
        problems.append(f"expected the claim to end {args.expect!r}, it ended {status!r}")
    expected_verdict = {"awaiting_approval": "deny", "executed": "permit"}[status]
    if facts["verdict"] != expected_verdict:
        problems.append(
            f"the reply says {status!r} but the policy decision says {facts['verdict']!r}"
        )
    if facts["executed_span_present"] != (status == "executed"):
        problems.append(
            f"the reply says {status!r} but the payments-api execute span is "
            f"{'present' if facts['executed_span_present'] else 'absent'}"
        )
    if facts["level"] != level_before:
        problems.append(
            f"the flag read {level_before!r} before the claim but the decision ran under {facts['level']!r}"
        )
    if status == "awaiting_approval" and not (
        facts["limit"] is not None and facts["requested"] is not None
        and facts["requested"] > facts["limit"]
    ):
        problems.append(f"a refusal must name a limit below the request: {facts}")
    if problems:
        raise Fail("; ".join(problems))
    out()
    if status == "awaiting_approval":
        out(paint("A refusal is a success: the model asked, the platform said no, and the record shows both.", "bold"))
    else:
        out(paint("Same amount, same agent, same code. Only the value changed.", "bold"))


# --- autonomy -------------------------------------------------------------------


def cmd_autonomy(args: argparse.Namespace) -> None:
    level = args.level
    title(f"AUTONOMY  set the live value to {level}")
    before_level = flag_level()
    before_pods = pod_fingerprint()
    kv("flagd resolves", f"{before_level}  (before)", indent=2)
    out()
    say("One field on one FeatureFlag object. Nothing is redeployed.")
    doing(f"kubectl patch {FLAG} --type=json  targeting.if[1] = {level}")
    started = time.monotonic()
    contract.set_targeted_branch(level)
    resolved = flag_level()
    out(f"  -> flagd resolves for settlement-agent: {word(resolved, 'go' if resolved == 'autonomous' else 'info')}"
        f"   ({time.monotonic() - started:0.1f}s to take effect)")
    if resolved != level:
        raise Fail(f"asked for {level!r} but flagd resolves {resolved!r}")
    after_pods = pod_fingerprint()
    if before_pods != after_pods:
        changed = sorted(k for k in set(before_pods) | set(after_pods) if before_pods.get(k) != after_pods.get(k))
        raise Fail(f"something restarted when only the value changed: {changed}")
    out(f"  Pods restarted: {word('NONE', 'info')}  ({len(after_pods)} pods, same UIDs, same start times, same restart counts)")


# --- keystone -------------------------------------------------------------------


def _as_agent(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return live.kubectl("--as", AGENT, *args, timeout=timeout)


def _patch_args(level: str) -> list[str]:
    patch = json.dumps(
        [{"op": "replace", "path": contract.TARGETING_THEN_PATH, "value": level}]
    )
    return ["-n", PLATFORM_NS, "patch", FLAG, "--type=json", "-p", patch]


def _show_kubectl_error(text: str) -> None:
    """The API server's own words, wrapped, with the decisive word marked."""
    text = text.strip()
    for raw in text.splitlines():
        for line in textwrap.wrap(raw, WIDTH - 4, break_on_hyphens=False) or [""]:
            line = re.sub(r"(Forbidden|DENIED)", lambda m: word(m.group(1), "stop"), line, count=1)
            out("    " + line)


def cmd_keystone(_: argparse.Namespace) -> None:
    title("KEYSTONE  the agent can never edit what constrains it")

    ready = live.kubectl(
        "get", "validatingpolicy", POLICY,
        "-o", "jsonpath={.status.conditionStatus.ready}", timeout=30,
    ).stdout.strip()
    if ready != "true":
        raise Fail(
            f"validatingpolicy/{POLICY} is not Ready (got {ready!r}). "
            "Stage 7 not up? ./scripts/setup-kind.sh --stage 7"
        )
    if overgrant_present():
        raise Fail(
            "the over-grant is already applied, so step 1 would show Kyverno and not "
            "RBAC. Run `scripts/demo reset` first."
        )
    kv("acting as", AGENT, indent=2)

    try:
        step("1  The agent tries to flip its own autonomy flag")
        doing("kubectl --as=settlement-agent patch featureflag/northstar-autonomy  -> autonomous")
        first = _as_agent(*_patch_args("autonomous"))
        if first.returncode == 0:
            contract.set_targeted_branch(contract.ASSISTED)
            raise Fail("the agent patched the flag with no over-grant. RBAC is wider than it should be. Flag restored to assisted.")
        _show_kubectl_error(first.stderr or first.stdout)
        if "Forbidden" not in (first.stderr + first.stdout):
            raise Fail("expected an RBAC Forbidden, got something else")
        out(f"  -> {word('RBAC says no', 'stop')}  That is the first line.")

        step("2  Someone makes a mistake: the agent is over-granted patch on that one flag")
        doing("kubectl apply -f deploy/demo/keystone-overgrant.yaml")
        applied = live.kubectl("apply", "-f", str(OVERGRANT), timeout=60)
        if applied.returncode != 0:
            raise Fail(f"could not apply the over-grant: {applied.stderr.strip()}")
        for line in applied.stdout.strip().splitlines():
            out("    " + line)
        # RBAC changes are not instantaneous for an impersonated request; wait
        # until the authorizer agrees, so step 3 tests Kyverno and not a race.
        deadline = time.time() + 20
        while time.time() < deadline:
            can = _as_agent("-n", PLATFORM_NS, "auth", "can-i", "patch", FLAG, timeout=30)
            if can.stdout.strip() == "yes":
                break
            time.sleep(1)
        doing("kubectl auth can-i patch featureflag/northstar-autonomy --as=settlement-agent")
        out(f"  -> {can.stdout.strip() or '?'}   RBAC would now allow it")
        if can.stdout.strip() != "yes":
            raise Fail("the over-grant never took effect, so step 3 would prove nothing about Kyverno")

        step("3  The same patch again. RBAC allows it. Kyverno does not.")
        doing("kubectl --as=settlement-agent patch featureflag/northstar-autonomy  -> autonomous")
        third = _as_agent(*_patch_args("autonomous"))
        if third.returncode == 0:
            contract.set_targeted_branch(contract.ASSISTED)
            raise Fail("the agent changed its own authority. The keystone did not fire. Flag restored to assisted.")
        text = third.stderr + third.stdout
        _show_kubectl_error(text)
        if POLICY not in text or "DENIED" not in text:
            raise Fail(
                f"the patch was refused, but not by the keystone (no {POLICY!r} / DENIED in "
                "the message). An RBAC denial must not pass for it."
            )
        out(f"  -> {word('DENIED', 'stop')} by Kyverno, naming the policy. The second line.")
    finally:
        # Idempotent, and on every exit path including Ctrl-C: the over-grant
        # models a mistake and must never be left standing.
        step("4  Take the mistake back")
        doing("kubectl delete -f deploy/demo/keystone-overgrant.yaml")
        deleted = live.kubectl("delete", "-f", str(OVERGRANT), "--ignore-not-found", timeout=60)
        for line in deleted.stdout.strip().splitlines():
            out("    " + line)
        if deleted.returncode != 0:
            out(paint(f"  could not delete the over-grant: {deleted.stderr.strip()}", "red"))

    step("5  What does the flag resolve to now?")
    doing("flagd, asked for settlement-agent")
    level = flag_level()
    out(f"  -> settlement-agent resolves: {word(level, 'info')}")
    if level != contract.ASSISTED:
        raise Fail(f"the flag is {level!r}, not 'assisted'")
    if overgrant_present():
        raise Fail("the over-grant is still present after the delete")
    out()
    out(paint("The agent could not edit what constrains it, even with the grant.", "bold"))


# --- evidence -------------------------------------------------------------------


def _settled(claim: dict, window_start_ms: int) -> dict:
    """`evidence_for` until the row is complete for what this claim did; never a
    partial row. A timeout is a failure naming the fields that never arrived."""
    required = ["policy_decision", "rule", "caller_identity", "money_hops"]
    if claim["status"] == "executed":
        required.append("executing_identity")
    deadline = time.time() + EVIDENCE_WAIT_S
    row: dict = {}
    while True:
        row = live.evidence_for(claim["id"], window_start_ms)
        missing = [f for f in required if not row.get(f)]
        if not missing:
            return row
        if time.time() > deadline:
            raise Fail(
                f"the evidence for {claim['id']} never became complete in OpenSearch within "
                f"{EVIDENCE_WAIT_S:.0f}s: {missing} are still empty. Row as it stood: "
                f"{json.dumps(row)}"
            )
        out(paint(f"  ... waiting for OpenSearch ({', '.join(missing)})", "dim"))
        time.sleep(4)


def cmd_evidence(_: argparse.Namespace) -> None:
    title("EVIDENCE  can we prove why each claim ended the way it did?")
    state = load_state()
    claims = state.get("claims") or []
    if not claims:
        raise Fail("no claims in this run yet: run `scripts/demo claim <amount>` first")
    say(f"From OpenSearch, for this run's {len(claims)} claim(s). Nothing older.")
    window = state["window_start_ms"]
    problems = []
    for claim in claims:
        out()
        out(paint(f"{claim['id']}   EUR {claim['amount']:,.0f}", "bold"))
        doing(f"OpenSearch  claim id -> trace ids -> every span in them  ({claim['id']})")
        row = _settled(claim, window)
        denied = row["policy_decision"] == "deny"
        outcome_executed = EXECUTE_HOP in row["money_hops"]
        kv("who decided", f"{live.PDS_SERVICE}   {word('DENY' if denied else 'PERMIT', 'stop' if denied else 'go')}")
        kv("rule", str(row["rule"]))
        kv("limit", f"{row['limit']}   (requested {row['requested']})")
        kv("autonomy level", str(row["autonomy_level"]))
        kv("caller identity", str(row["caller_identity"]))
        if outcome_executed:
            kv("outcome", f"{word('EXECUTED', 'go')} by {short_id(row['executing_identity'])}")
        else:
            kv("outcome", f"{word('REFUSED', 'stop')} no payments-api execute span: nothing moved")
        if row.get("approved_by"):
            kv("human approval", f"{row['approved_by']}, after {row['human_wait_s']:.0f} s")
        else:
            kv("human approval", str(row["reviewer"]) if row["reviewer"] else "none (no human was involved)")
        # The record must agree with what the run saw at the time.
        if (claim["status"] == "executed") != outcome_executed:
            problems.append(f"{claim['id']}: the run saw {claim['status']!r} but OpenSearch says executed={outcome_executed}")
        if (claim["status"] == "executed" and row["executing_identity"] != row["caller_identity"]
                and not row.get("approved_by")):
            problems.append(f"{claim['id']}: executed by someone other than the caller and no human approved it")
        if claim["level"] != row["autonomy_level"]:
            problems.append(f"{claim['id']}: the flag read {claim['level']!r} but the decision recorded {row['autonomy_level']!r}")
    if problems:
        raise Fail("; ".join(problems))
    out()
    out(paint("Every line above is a recorded span, read back from OpenSearch. Nothing is reconstructed.", "bold"))


# --- reset ----------------------------------------------------------------------


def cmd_reset(_: argparse.Namespace) -> None:
    title("RESET  back to the resting state")
    doing("over-grant: delete if present")
    delete_overgrant()
    if overgrant_present():
        raise Fail("the over-grant is still present after the delete")
    out(f"  over-grant   {word('ABSENT', 'info')}")
    doing("flag: set to assisted, wait for flagd")
    contract.set_targeted_branch(contract.ASSISTED)
    level = flag_level()
    if level != contract.ASSISTED:
        raise Fail(f"flagd resolves {level!r}, not 'assisted'")
    out(f"  flag         {word('assisted', 'info')}")
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    out(f"  run state    {word('CLEARED', 'info')}")


# --- ceiling (off stage: not part of the five-minute flow) ---------------------


def cmd_ceiling(_: argparse.Namespace) -> None:
    title("CEILING  the gateway's own limit, before the policy service is asked")
    over = live.ceiling_eur() + 10_000
    say(f"No caller may execute a payment above EUR {live.ceiling_eur():,}. The gateway says so itself.")
    with live.gateway_port_forward() as gateway:
        token = live.mint_svid(live.GATEWAY_AUDIENCE)
        doing(f"POST gateway/mcp  tools/call execute_payment   EUR {over:,}  (settlement-agent's SVID)")
        called = live.call_tool_directly(
            gateway, live.EXECUTE_TOOL,
            {"payment_id": "PAY-999999", "amount": float(over), "currency": "EUR"},
            gateway_headers(token),
        )
    if called.http_status != 403:
        raise Fail(f"the gateway did not stop EUR {over:,}: {called!r}")
    out(f"  -> HTTP {word('403', 'stop')}  {called.body.strip()}  -- the gateway said no, the PDS was never asked")


# --- main -----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("guard", help="identity, reach, and the tools it may call").set_defaults(fn=cmd_guard)
    claim = sub.add_parser("claim", help="a real claim through the agents and the model")
    claim.add_argument("amount", type=float)
    claim.add_argument(
        "--expect", choices=["awaiting_approval", "executed"],
        help="exit non-zero unless the claim ends this way",
    )
    claim.set_defaults(fn=cmd_claim)
    autonomy = sub.add_parser("autonomy", help="flip the live autonomy value")
    autonomy.add_argument("level", choices=[contract.ASSISTED, contract.AUTONOMOUS])
    autonomy.set_defaults(fn=cmd_autonomy)
    sub.add_parser("keystone", help="the agent tries to edit what constrains it").set_defaults(fn=cmd_keystone)
    sub.add_parser("evidence", help="who decided, and why, from OpenSearch").set_defaults(fn=cmd_evidence)
    sub.add_parser("reset", help="flag assisted, over-grant absent, state cleared").set_defaults(fn=cmd_reset)
    sub.add_parser("ceiling", help="the gateway's own CEL ceiling (not in the five-minute flow)").set_defaults(fn=cmd_ceiling)
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except Fail as exc:
        print(f"\n{paint('FAIL', 'stop') if _COLOR else 'FAIL'}  {exc}", file=sys.stderr)
        return 1
    except (AssertionError, subprocess.TimeoutExpired, requests.RequestException) as exc:
        print(f"\n{paint('FAIL', 'stop') if _COLOR else 'FAIL'}  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
