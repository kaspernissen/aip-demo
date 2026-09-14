"""Guards the M2.1 Task 4 workload-identity configuration.

Identity in this system is one string repeated in four files. The gateway's
SPIFFE ID is what SPIRE's template renders, what settlement-agent asks SPIRE to
mint a token *for*, and what the gateway checks the `aud` claim against. Change
it in three places out of four and nothing fails to start: SPIRE mints happily,
the gateway verifies happily, and every MCP call gets a 401 whose cause is
invisible in the token itself. So the cross-file agreements are asserted here,
not left to a comment in each file promising the others say the same thing.

The same goes for the trust domain. `global.spire.trustDomain` defaults to
`example.org` in the pinned chart, and every SPIFFE ID in this system is built
from it, so an install that lost the override would still come up, still mint
SVIDs, and still verify them -- against `spiffe://example.org/...`. That is a
system that passes its own tests while proving nothing about this platform.

**Written against the failure modes the M2.1 Task 1 guard round documented**
(see `tests/unit/test_stage2_cilium_config.py`'s module docstring for the full
account of five guards that shipped inert):

1. **No assertion here can be satisfied by a comment.** Every check either
   parses structure -- `yaml.safe_load` and `ast.parse`, both of which discard
   comments before anything is asserted -- or runs against
   `_effective_script()`, which is `setup-kind.sh` with the shebang and every
   whole-line comment removed. The version-pin checks additionally require
   *exactly one* assignment each and match a whole stripped line, so neither a
   stale second assignment nor a trailing comment can satisfy them.
2. **No assertion reads only the first element of a list the schema allows to
   be longer.** Fix round 1's whole subject. Every gateway check walks all
   binds, all listeners and all routes; every pod check walks
   `initContainers + containers`; every Service check walks all ports; the
   behavioural checks assert on *every* matching `helm` invocation, not the
   first. The inversion that makes this hold as the config grows: the rule is
   not "there is one route and it is authenticated" but "every route that
   exists is authenticated".
3. **The load-bearing orderings are proven behaviourally**, by running the real
   script against fake `helm`/`kubectl`/`kind`/`docker` and asserting on what
   it actually invoked. Nothing written in the file -- comment, string literal
   or echo -- can satisfy those.
4. **Enumerations normalise whitespace before matching.** `deploy/` YAML and
   this repo's docs both hard-wrap, and a hard-wrapped claim has already
   defeated two independent enumerations in this project.
5. **Published measurements are guarded too.** The SPIFFE IDs
   `docs/telemetry-contract.md` and `docs/architecture.md` publish are checked
   against the two this repo registers. A document that has been quietly
   rewritten to agree with a regression is worse than a stale one, and the
   specific stale value available here -- `.../sa/default` -- is exactly the
   failure the dedicated ServiceAccounts exist to prevent.

Every check below was proven RED by breaking its input, twice: 78 mutations in
the first round and 25 more in fix round 1, each applied to the real file and
reverted, enumerated over the *checkers' decision paths* rather than over
plausible document edits (the distinction that let a previous guard survive
twelve mutations without covering the branch it existed for). Some of those
mutations are expected to leave the suite GREEN -- a hard-wrapped header,
doubled spaces inside a Go template, a commented-out second version
assignment, a comment merely mentioning cert-manager, a value moved
consistently across every file that names it, and a *correctly* authenticated
second listener -- because a guard that fires on those would be a guard nobody
can edit around.

Four real weaknesses surfaced that way and are fixed here rather than noted.
Two from round one: a bare `"1.15.3" in header` was satisfiable by an unrelated
sentence elsewhere in the same file, and a blanket ban on the string
`cert-manager` in `setup-kind.sh` would have forbidden `--help` from honestly
saying it is not installed. Two from fix round 1, both found by review: the
`mcp_tools.py` audience check read text and took `literals[0]`, so a comment
quoting the old value kept it green; and every gateway check read
`binds[0].listeners[0]`, so a second listener with no `jwtAuth` at all left the
suite green. A fifth was found while enumerating for those two and had not been
reported: with the pod check widened to all containers, a mount and env var
present only on an *init* container would have satisfied "at least one
container is wired" while being unreachable at request time.

Two deliberate non-goals. This module does not talk to a cluster, mint a token,
or read a trace: the live evidence for those is the task report's captures and
`tests/e2e/test_gateway_trace.py`. And it does not assert that SPIRE *works* --
it asserts that the configuration says what the rest of the repo believes it
says.
"""
import ast
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"
SPIRE_VALUES = REPO_ROOT / "deploy" / "platform" / "spire" / "values.yaml"
SPIRE_REGISTRATION = REPO_ROOT / "deploy" / "platform" / "spire" / "registration.yaml"
SPIRE_METRICS = REPO_ROOT / "deploy" / "platform" / "spire" / "metrics.yaml"
GATEWAY_CONFIG = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "config.yaml"
GATEWAY_DEPLOYMENT = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "deployment.yaml"
SETTLEMENT_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "settlement-agent.yaml"
SVID_MODULE = REPO_ROOT / "agents" / "settlement-agent" / "svid.py"
MCP_TOOLS_MODULE = REPO_ROOT / "agents" / "settlement-agent" / "mcp_tools.py"
COLLECTOR_CONFIGS = (
    REPO_ROOT / "deploy" / "observability" / "collector-config.yaml",
    REPO_ROOT / "deploy" / "observability" / "collector-config-dash0.yaml",
)
# Documents that publish a SPIFFE ID as a measured value. Guarded because a
# published measurement nothing checks is a published measurement that can go
# stale -- and for this particular value the stale form is `.../sa/default`,
# which is precisely the silent failure the dedicated ServiceAccounts exist to
# prevent. A regression back to it would otherwise ship with a green suite.
DOCS_PUBLISHING_SPIFFE_IDS = (
    REPO_ROOT / "docs" / "telemetry-contract.md",
    REPO_ROOT / "docs" / "architecture.md",
)

# Resolved from `helm search repo spiffe/spire --versions` against the HARDENED
# charts repo, and cross-checked against that repo's upstream releases API
# (which tags per chart, so these two version series are independent):
# spire 0.30.2 (appVersion SPIRE 1.15.3) and spire-crds 0.6.1 are each the
# newest.
PINNED_SPIRE_CHART_VERSION = "0.30.2"
PINNED_SPIRE_CRDS_CHART_VERSION = "0.6.1"
PINNED_SPIRE_APP_VERSION = "1.15.3"
HARDENED_CHARTS_REPO = "https://spiffe.github.io/helm-charts-hardened/"

TRUST_DOMAIN = "northstar.local"
CLUSTER_NAME = "northstar"
JWT_ISSUER = (
    "http://spire-spiffe-oidc-discovery-provider.northstar-platform.svc.cluster.local"
)
SETTLEMENT_SPIFFE_ID = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/settlement-agent"
GATEWAY_SPIFFE_ID = f"spiffe://{TRUST_DOMAIN}/ns/northstar-platform/sa/agentgateway"
# M2.2 Task 4: the third registered workload. Registered, but NOT fetched --
# the PDS's pod carries no Workload API mount, because the identity it decides
# on is the caller's, handed to it by the gateway as an already-verified
# `jwt.sub`. See deploy/platform/spire/registration.yaml's closing note.
PDS_SPIFFE_ID = (
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-platform/sa/policy-decision-service"
)
# M3 Tasks 3 and 4: the fourth and fifth registered workloads. Both are real
# ServiceAccounts in `deploy/k8s/`, so SPIRE's `spiffeIDTemplate` derives these
# without any new registration.
#
# These two were added late, and the reason is worth keeping. The allow-list
# below sat at three entries while Tasks 3 and 4 created two more identities,
# so a document publishing either *correct* new ID was rejected as an offender
# -- and both tasks did what this file's own comment predicts anyone would do:
# they edited the document to match the test, substituting a placeholder for
# the real SPIFFE ID. The guard was passing while both documents had been made
# less true. That is the failure mode this comment already warned about, caught
# happening twice, so: adding an identity to the cluster means adding it here,
# in the same task.
SENIOR_SETTLEMENT_SPIFFE_ID = (
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/senior-settlement-agent"
)
REVIEWER_UI_SPIFFE_ID = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/reviewer-ui"
# M4 Task 4: the sixth registered workload, and the third in
# `northstar-platform`.
#
# **This comment said something false for most of the task that wrote it**, and
# the correction is the useful part. It read: "`deploy/k8s/eval-scorer.yaml`
# gives it a dedicated ServiceAccount, so SPIRE's `spiffeIDTemplate` derives
# this ID from the chart's catch-all `default` CR without any new registration
# -- exactly as it does for the two M3 identities above". The two M3 identities
# are in `northstar-agents`; eval-scorer is in `northstar-platform`, and the
# chart's catch-all CR *excludes* that namespace by name (`NotIn
# [northstar-platform, spire-server, spire-system]`, read off the live CR). So
# no entry was ever created: `spire-server entry show` listed an entry for
# every other workload and none for it. The workload had a ServiceAccount, this constant, an
# allow-list entry -- and no identity. It was inferred from the two nearest
# precedents instead of being measured, which is exactly the defect class this
# milestone keeps finding, committed inside the guard written against it.
# `deploy/platform/spire/registration.yaml` now carries an explicit
# non-fallback ClusterSPIFFEID for it, like agentgateway's and the PDS's.
#
# Added in the SAME task as the workload, which is the whole point of this
# block's own account of ruling R22: two M3 tasks created an identity, found
# this allow-list rejecting the correct new ID, and edited their *documents*
# to substitute a placeholder rather than widen the list. The guard was
# passing while both documents had been made less true.
#
# Registered, but NOT fetched, the same relationship
# `policy-decision-service` has with its own: eval-scorer's pod carries no
# Workload API mount, because it presents no credential to anything -- it
# reads OpenSearch (security plugin off on this cluster) and writes OTLP to
# the collector (unauthenticated). The identity exists so that the day
# something does check one, the answer is this workload's own name rather than
# `.../sa/default`.
EVAL_SCORER_SPIFFE_ID = (
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-platform/sa/eval-scorer"
)

# Every ClusterSPIFFEID `deploy/platform/spire/registration.yaml` must contain,
# by CR name -> (namespace, service account, the ID its template must render).
# Two tests read this: one checks the set and the rendering, the other checks
# the count and the className. Kept in one place because they were two places
# and the second was a hand-written `== 3`, which is the shape that drifts.
#
# `senior-settlement-agent` and `reviewer-ui` are deliberately NOT here: they
# are in `northstar-agents`, which the chart's catch-all fallback CR does
# cover, so they genuinely need no registration of their own. Namespace is what
# decides -- see this file's EVAL_SCORER_SPIFFE_ID comment for the measurement
# that established it, and what believing otherwise cost.
REGISTERED_CLUSTER_SPIFFEIDS = {
    "northstar-settlement-agent": (
        "northstar-agents", "settlement-agent", SETTLEMENT_SPIFFE_ID,
    ),
    "northstar-agentgateway": (
        "northstar-platform", "agentgateway", GATEWAY_SPIFFE_ID,
    ),
    "northstar-policy-decision-service": (
        "northstar-platform", "policy-decision-service", PDS_SPIFFE_ID,
    ),
    "northstar-eval-scorer": (
        "northstar-platform", "eval-scorer", EVAL_SCORER_SPIFFE_ID,
    ),
}
# The workload-facing Workload API socket, as READ from the pinned chart's
# rendered output rather than assumed: `helm template spire spiffe/spire
# --version 0.30.2` renders the spiffe-oidc-discovery-provider (the chart's own
# Workload API consumer) with `workload_api.socket_path:
# /spiffe-workload-api/spire-agent.sock`, a `mountPath: /spiffe-workload-api`,
# and a `csi.spiffe.io` volume.
WORKLOAD_API_DIR = "/spiffe-workload-api"
WORKLOAD_API_SOCKET = f"{WORKLOAD_API_DIR}/spire-agent.sock"
CSI_DRIVER_NAME = "csi.spiffe.io"


def _yaml_docs(path: Path) -> list:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _effective_script() -> str:
    """`setup-kind.sh` with the shebang and every whole-line comment removed.

    Trailing (end-of-line) comments are deliberately left in place -- stripping
    those safely needs a shell parser, since `#` is an ordinary character
    inside strings -- so every assertion below is additionally written
    specifically enough that a trailing comment cannot supply it.
    """
    return "\n".join(
        line
        for line in SETUP_KIND.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _squash(text: str) -> str:
    """Collapse all whitespace runs to single spaces.

    Every enumeration over this repo's YAML and docs goes through this. Two
    independent parties have already under-counted the same document because a
    claim was hard-wrapped across a newline; matching on squashed text removes
    that whole failure mode rather than hoping the next claim fits on one line.
    """
    return re.sub(r"\s+", " ", text)


def _prose(path: Path) -> str:
    """A YAML file's comment prose as one squashed line.

    Leading `#` markers are stripped *before* squashing, because a
    hard-wrapped comment puts a `#` in the middle of the sentence -- so
    squashing alone would turn "spiffe/spire-crds 0.6.1" wrapped across two
    comment lines into "spiffe/spire-crds # 0.6.1" and miss it. This is the
    same under-count that has already defeated two enumerations of
    docs/telemetry-contract.md, one level meaner.
    """
    return _squash(
        " ".join(
            re.sub(r"^\s*#\s?", "", line)
            for line in path.read_text().splitlines()
            if line.lstrip().startswith("#")
        )
    )


def _python_spiffe_literals(path: Path) -> list[str]:
    """Every `spiffe://…` string *constant* in a Python module, via `ast`.

    Regex over the file text was the first version of this, and review's
    mutation R1 defeated it: changing the real literal while leaving a comment
    that quoted the old one kept `literals[0]` correct and all 24 tests green.
    That is this project's sixth instance of a guard satisfiable by a comment,
    so this one does not read the file as text at all -- `ast.parse` has
    discarded every comment before anything here can look at one.

    Docstrings are excluded as well. They are not comments, so the parser keeps
    them, and a module that *documents* the audience in prose would otherwise
    supply the literal the code is supposed to contain.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.startswith("spiffe://")
    ]


def _all_routes(config: dict) -> list[tuple[str, dict]]:
    """Every route in the gateway config, with a readable path to each.

    Enumerates all binds, all listeners and all routes rather than
    `binds[0].listeners[0].routes[0]`. Review found that narrowness: a second
    listener carrying no `jwtAuth` at all left every test in this file green --
    an unauthenticated way into the same gateway, invisible to the guard for
    authentication. The schema permits a list at all three levels, so the guard
    walks all three.
    """
    routes = []
    for bind_index, bind in enumerate(config.get("binds") or []):
        for listener_index, listener in enumerate(bind.get("listeners") or []):
            for route_index, route in enumerate(listener.get("routes") or []):
                routes.append(
                    (
                        f"binds[{bind_index}].listeners[{listener_index}]"
                        f".routes[{route_index}]",
                        route,
                    )
                )
    assert routes, "the gateway config declares no routes at all"
    return routes


def _all_listeners(config: dict) -> list[tuple[str, dict]]:
    listeners = []
    for bind_index, bind in enumerate(config.get("binds") or []):
        for listener_index, listener in enumerate(bind.get("listeners") or []):
            listeners.append(
                (f"binds[{bind_index}].listeners[{listener_index}]", listener)
            )
    assert listeners, "the gateway config declares no listeners at all"
    return listeners


def _pod_containers(pod: dict) -> list[tuple[str, dict]]:
    """Every container in a pod spec, init containers included.

    The unit enumerated over is `initContainers + containers`, because that is
    the complete set of places an image, an env var or a volume mount can
    appear in a pod spec. It is the same correction review applied to this
    task's image enumeration, which read `containerStatuses` and so missed two
    init containers: the unit has to be the class, not the convenient subset.
    """
    return [
        (f"initContainers[{i}]", c)
        for i, c in enumerate(pod.get("initContainers") or [])
    ] + [(f"containers[{i}]", c) for i, c in enumerate(pod.get("containers") or [])]


def _find_by_kind(docs: list, kind: str, name: str) -> dict:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(
        f"no {kind}/{name} in the manifest; found "
        f"{[(d.get('kind'), d.get('metadata', {}).get('name')) for d in docs]}"
    )


# --- The Helm values -------------------------------------------------------


def test_the_trust_domain_cluster_name_and_issuer_are_nested_not_flat():
    """The chart is an umbrella: values must nest under per-sub-chart keys.

    A flat `trustDomain:` at the document root is not a key any sub-chart
    reads, Helm does not warn about unknown top-level values, and the install
    then silently uses the chart default `example.org`. That failure mode looks
    exactly like success, which is why both halves are asserted: the nested
    keys are present *and* the flat spellings are absent.
    """
    values = _yaml(SPIRE_VALUES)
    spire = values.get("global", {}).get("spire", {})
    assert spire.get("trustDomain") == TRUST_DOMAIN, (
        f"global.spire.trustDomain must be {TRUST_DOMAIN!r} (the chart default "
        f"is 'example.org'); got {spire.get('trustDomain')!r}"
    )
    assert spire.get("clusterName") == CLUSTER_NAME, (
        f"global.spire.clusterName must be {CLUSTER_NAME!r} (the chart default "
        f"is 'example-cluster'); got {spire.get('clusterName')!r}"
    )
    assert spire.get("jwtIssuer") == JWT_ISSUER, (
        "global.spire.jwtIssuer must be set explicitly, not left to derive to "
        f"oidc-discovery.$trustDomain; expected {JWT_ISSUER!r}, got "
        f"{spire.get('jwtIssuer')!r}"
    )
    for flat in ("trustDomain", "clusterName", "jwtIssuer"):
        assert flat not in values, (
            f"a top-level {flat!r} in this umbrella chart's values is silently "
            "ignored -- Helm does not warn, and the chart default applies "
            "instead. It must live under global.spire."
        )


def test_the_values_header_records_both_pinned_chart_versions():
    """Nothing floats: the versions setup-kind.sh installs must be stated in
    the values file too, so the two cannot drift apart silently.

    Read through `_prose`, because this file hard-wraps its header and the
    version and the chart name it belongs to can land on different lines --
    with a comment marker between them.
    """
    text = _prose(SPIRE_VALUES)
    assert f"spiffe/spire-crds {PINNED_SPIRE_CRDS_CHART_VERSION}" in text, (
        "the values header must state the pinned spire-crds chart version as "
        f"'spiffe/spire-crds {PINNED_SPIRE_CRDS_CHART_VERSION}'"
    )
    assert f"spiffe/spire {PINNED_SPIRE_CHART_VERSION}" in text, (
        "the values header must state the pinned spire chart version as "
        f"'spiffe/spire {PINNED_SPIRE_CHART_VERSION}'"
    )
    # Deliberately not a bare `"1.15.3" in text`: this file mentions the SPIRE
    # release in prose elsewhere, so a bare containment check was satisfiable
    # by that unrelated sentence -- a mutation that changed the header's
    # appVersion escaped exactly that way. The appVersion has to be stated
    # next to the chart version it belongs to, which is the only place the
    # pairing is load-bearing.
    assert (
        f"spiffe/spire {PINNED_SPIRE_CHART_VERSION} "
        f"(appVersion {PINNED_SPIRE_APP_VERSION}" in text
    ), (
        f"the values header must state the SPIRE appVersion "
        f"({PINNED_SPIRE_APP_VERSION}) alongside the chart version "
        f"({PINNED_SPIRE_CHART_VERSION}), so a chart bump that changes SPIRE "
        "itself is visible"
    )
    assert HARDENED_CHARTS_REPO in text, (
        "the values header must name the hardened charts repo explicitly -- "
        "the older spiffe/helm-charts location does not carry these charts, "
        "and that is the wrong turn this pin exists to prevent"
    )


def test_spires_own_prometheus_telemetry_is_enabled_on_both_server_and_agent():
    """SPIRE 1.15.3 emits no OTLP and no spans; Prometheus is its only real
    telemetry surface. Left at the chart default (`enabled: false`) SPIRE is
    invisible -- the same hole M2.1 Task 3 found in agentgateway, which is why
    this is asserted rather than assumed."""
    values = _yaml(SPIRE_VALUES)
    for chart in ("spire-server", "spire-agent"):
        prom = values.get(chart, {}).get("telemetry", {}).get("prometheus", {})
        assert prom.get("enabled") is True, (
            f"{chart}.telemetry.prometheus.enabled must be true; got "
            f"{prom.get('enabled')!r}"
        )
        assert prom.get("podMonitor", {}).get("enabled") is False, (
            f"{chart}.telemetry.prometheus.podMonitor must stay disabled -- it "
            "needs the Prometheus Operator CRD, which this cluster does not "
            "have (Prometheus is stage 4). The collector scrapes directly."
        )


def test_cert_manager_is_not_installed_below_stage_5():
    """The M2 spec includes cert-manager "only to the extent SPIRE actually
    needs it". The pinned SPIRE chart needs it for nothing this install
    enables -- the OIDC provider's ingress TLS, spire-server federation TLS,
    and the `certManager` upstreamAuthority, all off -- so SPIRE's own values
    must never configure it.

    **M5 Task 2 gave this guard a second half.** Until then the whole
    assertion was an absence -- cert-manager was not installed anywhere in
    this script, full stop, and this test's own docstring told the next task
    that genuinely needed it to "install it pinned and record why, then
    update this guard" rather than delete it. M5 Task 2 is that task: the
    Open Feature Operator's webhooks need cert-manager for TLS, so
    `scripts/setup-kind.sh` now installs it, pinned (`CERT_MANAGER_VERSION`),
    inside the `"${STAGE}" -ge 5` block. What must stay true is narrower than
    before but not gone: SPIRE (stage 2+) still needs no certManager-backed
    feature, and nothing below stage 5 may invoke cert-manager tooling --
    installing it a stage early would be the same "component installed
    because a document named it" defect this guard has always existed to
    catch, just moved one stage later.

    Scoped to *invocations* rather than to any mention of the string, on
    purpose. `setup-kind.sh --help` says out loud when cert-manager arrives
    and why, and that sentence is the kind of honest operator text this repo
    wants to keep; a guard that forbade the word would forbid saying so. The
    counterpart behavioural check (`test_stage_two_installs_no_cert_manager`)
    is the one nothing textual can satisfy.
    """
    # Walked over the parsed values rather than searched for in the file text.
    # As a text search this was defeatable in the harmless direction and
    # trippable in the annoying one: a comment explaining why cert-manager is
    # not needed would have failed it, which is the same mistake as banning the
    # word in the script (below).
    def _cert_manager_keys(node, path="values"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "certManager":
                    yield f"{path}.{key}"
                yield from _cert_manager_keys(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from _cert_manager_keys(value, f"{path}[{index}]")

    enabled = sorted(_cert_manager_keys(_yaml(SPIRE_VALUES)))
    assert not enabled, (
        "the SPIRE values must not configure any certManager-backed feature; "
        f"found {enabled}"
    )

    text = _effective_script()
    installs = [
        line
        for line in text.splitlines()
        if re.search(r"cert-?[Mm]anager", line)
        and re.search(r"\b(helm|kubectl|jetstack)\b", line)
    ]
    assert installs, (
        "setup-kind.sh no longer invokes cert-manager tooling at all -- if M5 "
        "Task 2's install was removed, this guard's other half (SPIRE must "
        "configure no certManager feature) still applies, but this half "
        "should be deleted or rewritten rather than trivially satisfied"
    )

    # Every such line must sit inside the `-ge 5` gate, not merely somewhere
    # in the file: a stage-5-only feature installed unconditionally would
    # bring cert-manager to a stage-2/3/4 cluster too, silently.
    gate = re.search(
        r'if \[\[ "\$\{STAGE\}" -ge 5 \]\]; then\n(.*?)\nfi\n', text, re.DOTALL
    )
    assert gate, (
        "expected an effective `if [[ \"${STAGE}\" -ge 5 ]]; then ... fi` "
        "block -- cert-manager's install must be gated behind it"
    )
    gated_lines = set(gate.group(1).splitlines())
    ungated = [line for line in installs if line not in gated_lines]
    assert not ungated, (
        "these cert-manager invocations sit outside the `-ge 5` gate, so a "
        "cluster below stage 5 would install cert-manager too:\n  "
        + "\n  ".join(line.strip() for line in ungated)
    )

    assert 'CERT_MANAGER_VERSION="' in text, (
        "cert-manager's version must be a pinned variable, not a literal "
        "inlined into the install line -- the same convention every other "
        "chart/release version in this script follows"
    )


# --- The registration entries ---------------------------------------------


def test_every_cluster_spiffeid_sets_the_controller_managers_class_name():
    """The single silent-failure mode in this file.

    The chart renders its controller-manager with `className:
    "northstar-platform-spire"` and `watchClassless: false`, so a
    ClusterSPIFFEID carrying no `className` is ignored outright: `kubectl
    apply` succeeds, the CR's status stays empty, no entry is created, and the
    only symptom is a workload that cannot fetch an SVID. The chart's own
    install NOTES say so in as many words.

    **M2.2 Task 4 widened the count below from two to three**: the Policy
    Decision Service is the third registered workload
    (deploy/platform/spire/registration.yaml). The check's intent is unchanged
    -- every ClusterSPIFFEID in this file must carry the controller-manager's
    className -- and the count is what makes a silently *absent* CR fail here
    as well as a classless one. It is still an exact count, deliberately: an
    identity appearing in this trust domain without review still fails.

    **M4 Task 4 widened it to four**, adding eval-scorer, and the count is now
    read from `test_the_registered_spiffe_ids_are_built_from_this_projects_
    trust_domain`'s `expected` map rather than restated -- that map already
    names every CR this file must contain, and keeping a second hand-written
    number beside it is how the two drift. It went red on the fourth CR before
    that change, which is the guard working; deriving it means the *next* one
    is reviewed in one place instead of two.
    """
    docs = _yaml_docs(SPIRE_REGISTRATION)
    crs = [d for d in docs if d.get("kind") == "ClusterSPIFFEID"]
    assert len(crs) == len(REGISTERED_CLUSTER_SPIFFEIDS), (
        "expected exactly "
        f"{len(REGISTERED_CLUSTER_SPIFFEIDS)} ClusterSPIFFEID resources "
        f"({', '.join(sorted(REGISTERED_CLUSTER_SPIFFEIDS))}); found "
        f"{len(crs)}: {sorted(d['metadata']['name'] for d in crs)}"
    )
    for cr in crs:
        name = cr["metadata"]["name"]
        assert cr["spec"].get("className") == "northstar-platform-spire", (
            f"ClusterSPIFFEID/{name} must set spec.className to "
            f"'northstar-platform-spire'; got "
            f"{cr['spec'].get('className')!r}. With the chart's "
            "watchClassless: false, a classless CR is silently never "
            "reconciled into a registration entry."
        )


def test_both_workloads_are_attested_on_namespace_and_service_account():
    """The service account must be an attestation *constraint*, not decoration
    in the ID string. Without the `k8s:sa:` workload selector the entry's only
    selectors are whatever the controller-manager derives, and the identity
    would name a service account SPIRE never checked."""
    crs = [d for d in _yaml_docs(SPIRE_REGISTRATION) if d.get("kind") == "ClusterSPIFFEID"]
    for cr in crs:
        name = cr["metadata"]["name"]
        selectors = cr["spec"].get("workloadSelectorTemplates") or []
        squashed = [_squash(s) for s in selectors]
        assert "k8s:ns:{{ .PodMeta.Namespace }}" in squashed, (
            f"ClusterSPIFFEID/{name} must constrain on namespace; got "
            f"{selectors!r}"
        )
        assert "k8s:sa:{{ .PodSpec.ServiceAccountName }}" in squashed, (
            f"ClusterSPIFFEID/{name} must constrain on service account; got "
            f"{selectors!r}"
        )
        assert cr["spec"].get("fallback") is False, (
            f"ClusterSPIFFEID/{name} must not be a fallback -- the chart ships "
            "a catch-all `default` CR with fallback: true that matches the "
            "same pods, and a fallback CR applies only when no non-fallback "
            "one matches"
        )


def test_the_registered_spiffe_ids_are_built_from_this_projects_trust_domain():
    """The ID template must render the IDs the rest of the repo names.

    Rendered by substitution here rather than asserted as a literal, so the
    guard fails if the *template* changes shape (say to `/sa/{{...}}/ns/{{...}}`)
    as well as if the trust domain drifts.

    **M2.2 Task 4 widened `expected` from two entries to three**, adding the
    Policy Decision Service. Nothing about the check's shape changed: it is
    still an exact set comparison, so an unreviewed identity fails, and each
    entry's template is still rendered rather than matched as a string.

    **M4 Task 4 widened it to four**, adding eval-scorer -- and the reason is
    worth keeping, because it is the thing no guard in this file could see. The
    chart's catch-all `default` CR excludes the `northstar-platform` namespace
    (measured on the live CR: `NotIn [northstar-platform, spire-server,
    spire-system]`), so a workload added there without a CR of its own gets
    **no identity at all**, silently. eval-scorer ran in that state:
    `spire-server entry show` on this cluster listed an entry for every other
    workload and none for it. Nothing went red, because every SPIFFE check in this file
    compares strings the documents publish against configuration the repo
    writes, and an identity that was never registered appears in neither.
    Registering it here is the fix; publishing the ID in `docs/architecture.md`
    in the same task is what makes the allow-list further down this file live
    rather than decorative.
    """
    crs = {
        d["metadata"]["name"]: d["spec"]
        for d in _yaml_docs(SPIRE_REGISTRATION)
        if d.get("kind") == "ClusterSPIFFEID"
    }
    expected = REGISTERED_CLUSTER_SPIFFEIDS
    assert set(crs) == set(expected), (
        f"unexpected ClusterSPIFFEID names: {sorted(crs)} vs {sorted(expected)}"
    )
    for cr_name, (namespace, service_account, spiffe_id) in expected.items():
        template = _squash(crs[cr_name]["spiffeIDTemplate"])
        rendered = (
            template.replace("{{ .TrustDomain }}", TRUST_DOMAIN)
            .replace("{{ .PodMeta.Namespace }}", namespace)
            .replace("{{ .PodSpec.ServiceAccountName }}", service_account)
        )
        assert rendered == spiffe_id, (
            f"ClusterSPIFFEID/{cr_name}'s template renders {rendered!r} for a "
            f"pod in {namespace} under service account {service_account}, but "
            f"the rest of the repo names {spiffe_id!r}"
        )
        # The pod selector must actually select that workload, or the CR
        # matches nothing and the ID above is hypothetical.
        assert crs[cr_name]["podSelector"]["matchLabels"]["app"] == service_account
        assert (
            crs[cr_name]["namespaceSelector"]["matchLabels"][
                "kubernetes.io/metadata.name"
            ]
            == namespace
        )


# --- The workloads' own manifests -----------------------------------------


def test_settlement_agent_has_its_own_service_account_and_uses_it():
    """Both halves matter. A ServiceAccount that exists but is not referenced
    leaves the pod on `default`, and the SPIFFE ID then reads
    `.../sa/default` -- measured, not hypothetical: that is the ID SPIRE minted
    for this pod before the ServiceAccount was wired in."""
    docs = _yaml_docs(SETTLEMENT_MANIFEST)
    _find_by_kind(docs, "ServiceAccount", "settlement-agent")
    deployment = _find_by_kind(docs, "Deployment", "settlement-agent")
    pod = deployment["spec"]["template"]["spec"]
    assert pod.get("serviceAccountName") == "settlement-agent", (
        "the pod must reference its own ServiceAccount; got "
        f"{pod.get('serviceAccountName')!r}. Unreferenced, the pod runs as "
        "`default` and attests as .../sa/default."
    )


def test_agentgateway_has_its_own_service_account_and_uses_it():
    docs = _yaml_docs(GATEWAY_DEPLOYMENT)
    _find_by_kind(docs, "ServiceAccount", "agentgateway")
    deployment = _find_by_kind(docs, "Deployment", "agentgateway")
    pod = deployment["spec"]["template"]["spec"]
    assert pod.get("serviceAccountName") == "agentgateway", (
        "the gateway's SPIFFE ID is the JWT audience both ends of this hop "
        "agree on, so it must not attest as .../sa/default; got "
        f"{pod.get('serviceAccountName')!r}"
    )


def test_settlement_agent_mounts_the_spiffe_csi_volume_at_the_socket_path():
    """The mount and the configured socket path must agree.

    The directory half of the Workload API path is chosen by whoever mounts the
    CSI volume and the filename half is the driver's, so these are two
    independent settings that must describe one file. They are compared here
    rather than each being asserted against a literal, so moving the mount
    without moving the env var fails.

    Enumerated over every container in the pod, init containers included,
    rather than `containers[0]`. The rule has two halves. **No** container may
    have the mount without the env var or the env var without the mount --
    checked across init and regular containers alike, since either is a
    half-wired container. And at least one **long-running** container must have
    both: an init container has exited by the time the agent serves a request,
    so a Workload API socket reachable only from an init container is a socket
    nothing can use. Both halves were invisible when this read
    `containers[0]`.
    """
    deployment = _find_by_kind(_yaml_docs(SETTLEMENT_MANIFEST), "Deployment", "settlement-agent")
    pod = deployment["spec"]["template"]["spec"]

    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    csi_volumes = {
        name: v for name, v in volumes.items() if v.get("csi", {}).get("driver")
    }
    assert len(csi_volumes) == 1, (
        f"expected exactly one CSI volume on this pod; found {sorted(csi_volumes)}"
    )
    volume_name, volume = next(iter(csi_volumes.items()))
    assert volume["csi"]["driver"] == CSI_DRIVER_NAME, (
        f"the CSI driver must be {CSI_DRIVER_NAME!r} (the plugin name the "
        "pinned chart registers); the nested-mode variant registers "
        f"'upstream.{CSI_DRIVER_NAME}' and is not installed. Got "
        f"{volume['csi']['driver']!r}"
    )
    assert volume["csi"].get("readOnly") is True, (
        "the Workload API mount must be read-only"
    )

    wired, half_wired = [], []
    for where, container in _pod_containers(pod):
        mounts = {m["name"]: m for m in container.get("volumeMounts") or []}
        env = {e["name"]: e.get("value") for e in container.get("env") or []}
        has_mount = volume_name in mounts
        endpoint = env.get("SPIFFE_ENDPOINT_SOCKET")
        if has_mount and not endpoint:
            half_wired.append(
                f"{where} mounts {volume_name!r} but sets no "
                "SPIFFE_ENDPOINT_SOCKET, so nothing in it can find the socket"
            )
            continue
        if endpoint and not has_mount:
            half_wired.append(
                f"{where} sets SPIFFE_ENDPOINT_SOCKET={endpoint!r} but does "
                f"not mount {volume_name!r}, so that path does not exist in it"
            )
            continue
        if not has_mount:
            continue

        assert endpoint.startswith("unix://"), (
            f"{where}'s SPIFFE_ENDPOINT_SOCKET must carry the unix:// scheme; "
            f"got {endpoint!r}"
        )
        socket_path = endpoint[len("unix://") :]
        assert socket_path == WORKLOAD_API_SOCKET, (
            f"{where}'s SPIFFE_ENDPOINT_SOCKET points at {socket_path!r}; the "
            f"path read from the pinned chart's own rendered output is "
            f"{WORKLOAD_API_SOCKET!r}"
        )
        mount_path = mounts[volume_name]["mountPath"].rstrip("/")
        assert os.path.dirname(socket_path) == mount_path, (
            f"{where} mounts the CSI volume at {mount_path!r} but its "
            f"SPIFFE_ENDPOINT_SOCKET names a socket in "
            f"{os.path.dirname(socket_path)!r} -- the mount and the configured "
            "path must describe one file"
        )
        if not where.startswith("initContainers["):
            wired.append(where)

    assert not half_wired, (
        "the Workload API mount and SPIFFE_ENDPOINT_SOCKET must travel "
        "together; these containers have one without the other:\n  "
        + "\n  ".join(half_wired)
    )
    assert wired, (
        f"no long-running container in this pod both mounts {volume_name!r} "
        "and sets SPIFFE_ENDPOINT_SOCKET. An init container carrying them is "
        "not enough -- it has exited before the agent serves a request, so the "
        "socket is unreachable when svid.py needs it."
    )


# --- The one string, in four places ---------------------------------------


def test_the_gateway_audience_is_the_same_string_everywhere_it_appears():
    """The highest-value check in this file.

    The gateway's SPIFFE ID appears as: the ID the SPIRE template renders, the
    audience settlement-agent's deployment asks for, the fallback baked into
    `mcp_tools.py` for when that variable is unset, and the `aud` value the
    gateway's own jwtAuth accepts. Three out of four agreeing is a silent 401
    on every call.

    **Both narrowness modes were found here by review and are closed.** The
    `mcp_tools.py` side read the file as text and took `literals[0]`, so a
    comment quoting the old value kept it green while the code had moved; it
    now goes through `ast` and asserts on *every* SPIFFE literal in the module,
    not the first. The manifest side read `containers[0]`; it now enumerates
    every container in the pod, init containers included, and requires the ones
    that declare the audience to agree.
    """
    pod = _find_by_kind(
        _yaml_docs(SETTLEMENT_MANIFEST), "Deployment", "settlement-agent"
    )["spec"]["template"]["spec"]
    declared = {}
    for where, container in _pod_containers(pod):
        for entry in container.get("env") or []:
            if entry.get("name") == "NORTHSTAR_GATEWAY_AUDIENCE":
                declared[where] = entry.get("value")
    assert declared, (
        "no container in the settlement-agent pod sets "
        "NORTHSTAR_GATEWAY_AUDIENCE; the audience would then come from "
        "mcp_tools.py's fallback alone, unstated in the manifest"
    )

    # Parsed out of the module with `ast` rather than imported: this test runs
    # in the root .venv, which deliberately does not have settlement-agent's
    # dependencies (importing mcp_tools would pull in mcp, spiffe and
    # pydantic-ai).
    module_literals = _python_spiffe_literals(MCP_TOOLS_MODULE)
    assert module_literals, (
        "no spiffe:// literal found in mcp_tools.py's code -- expected the "
        "fallback audience for NORTHSTAR_GATEWAY_AUDIENCE. A comment or "
        "docstring naming it does not count and is not looked at."
    )

    audience_lists = {
        where: block["audiences"]
        for where, block in _jwt_auth_blocks(_yaml(GATEWAY_CONFIG))
    }

    sites = {
        "deploy/platform/spire/registration.yaml (rendered ID template)": [
            GATEWAY_SPIFFE_ID
        ],
        "agents/settlement-agent/mcp_tools.py (every SPIFFE literal in the code)":
            module_literals,
    }
    for where, value in declared.items():
        sites[f"deploy/k8s/settlement-agent.yaml ({where} env)"] = [value]
    for where, audiences in audience_lists.items():
        sites[f"deploy/platform/agentgateway/config.yaml ({where}.jwtAuth)"] = audiences

    distinct = {value for values in sites.values() for value in values}
    assert distinct == {GATEWAY_SPIFFE_ID}, (
        "the gateway's SPIFFE ID must be one identical string at every site, "
        "and no site may name a second one; found:\n  "
        + "\n  ".join(f"{where}: {what!r}" for where, what in sites.items())
    )
    for where, audiences in audience_lists.items():
        assert audiences == [GATEWAY_SPIFFE_ID], (
            f"{where}.jwtAuth.audiences must contain exactly the gateway's own "
            f"SPIFFE ID; got {audiences!r}. A second audience widens what this "
            "gateway accepts without anything else in the repo saying so."
        )
    # The registration CR must actually be the source of that ID, not a
    # coincidence -- cross-checked against the template test above.
    assert SETTLEMENT_SPIFFE_ID not in distinct, (
        "the audience must be the *gateway's* identity, not the caller's -- a "
        "token minted for its own subject would verify against nothing"
    )


_DOC_SPIFFE_ID_RE = re.compile(r"spiffe://[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+")


@pytest.mark.parametrize(
    "doc", DOCS_PUBLISHING_SPIFFE_IDS, ids=lambda p: p.name
)
def test_every_spiffe_id_published_in_the_docs_is_one_this_repo_configures(doc):
    """The published identity is a measurement, so it gets a guard.

    Review's finding, and the one that matters most out of proportion to its
    label: `docs/telemetry-contract.md` publishes
    `jwt.sub = spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`
    as read off a live span, and nothing checked it. Swapping it to
    `.../sa/default` left the whole suite green -- and `.../sa/default` is not
    an arbitrary wrong value, it is exactly what SPIRE minted for this pod
    before it was given a ServiceAccount of its own. A regression to the one
    failure this task exists to prevent would have shipped with a passing
    suite, having also quietly rewritten the document that would have exposed
    it.

    Two things follow from that about the shape of this check.

    It is an **allow-list over every occurrence**, not an equality check on the
    one line review happened to name. The value appears in several places in
    that file and in `architecture.md`; a check pinned to a line number, or to
    the attribute block alone, would leave the others exactly as unguarded as
    they were. So the enumeration unit is "every SPIFFE ID anywhere in the
    document", and the permitted set is every ID this repo actually configures
    -- which makes a wrong trust domain, a wrong namespace and a wrong service
    account all fail, not only `sa/default`.

    **No count is written here, deliberately.** This paragraph said "the three
    IDs this repo actually registers" while `permitted` already held five, and
    said "three places in that file" without anything checking it. Both were
    the stale-prose class this milestone keeps finding, in the docstring of a
    guard written against exactly that failure. The set below is the single
    place the answer lives; prose that restates its size only goes stale
    again.

    And it enumerates over the **raw text**, deliberately including prose,
    tables and fenced blocks. A published measurement is wrong wherever it
    appears; there is no part of these documents where a stale SPIFFE ID is
    acceptable.
    """
    text = doc.read_text()
    found = _DOC_SPIFFE_ID_RE.findall(text)
    assert found, (
        f"{doc.relative_to(REPO_ROOT)} publishes no SPIFFE ID at all, so this "
        "guard would be checking nothing. If the identity documentation moved, "
        "point this at where it went rather than deleting the check."
    )
    # Every identity this repository configures, whether or not it has a
    # ClusterSPIFFEID of its own. `PDS_SPIFFE_ID` was registered by M2.2 Task 4
    # and checked against the CR's own rendered template by
    # `test_the_registered_spiffe_ids_are_built_from_this_projects_trust_domain`
    # -- but it was missing from *this* allow-list, so the moment a document
    # published an identity this repository genuinely configures, the guard
    # would have called a correct document an offender. A guard that fails on a
    # correct document is as harmful as one that passes a wrong one: both
    # teach a reader to edit the document to match the test. M3 added the two
    # `northstar-agents` identities and M4 Task 4 added eval-scorer's, each in
    # the task that created the workload (ruling R22).
    permitted = {
        SETTLEMENT_SPIFFE_ID,
        GATEWAY_SPIFFE_ID,
        PDS_SPIFFE_ID,
        SENIOR_SETTLEMENT_SPIFFE_ID,
        REVIEWER_UI_SPIFFE_ID,
        EVAL_SCORER_SPIFFE_ID,
    }
    offenders = sorted({one for one in found if one not in permitted})
    assert not offenders, (
        f"{doc.relative_to(REPO_ROOT)} publishes SPIFFE ID(s) this repo does "
        f"not configure: {offenders}. The registered IDs are "
        f"{sorted(permitted)}.\n"
        "A subject ending '/sa/default' means the document has been rewritten "
        "to match a pod running as the default ServiceAccount -- the exact "
        "regression the dedicated ServiceAccounts prevent. A different trust "
        "domain means the chart's `example.org` default came back. Either way "
        "the document and the cluster now disagree, and the document is not "
        "the thing to change first."
    )


def test_the_documented_jwt_sub_value_is_the_callers_attested_identity():
    """The specific site review named, checked structurally rather than by line.

    The allow-list above catches a swap to any unregistered ID. This one
    additionally pins *which* of the two registered IDs belongs in the
    `jwt.sub` row of the gateway's SERVER attribute block -- because swapping
    it for the gateway's own SPIFFE ID would pass an allow-list while
    describing a token minted for its own subject, which verifies against
    nothing and proves nothing about who called.
    """
    text = (REPO_ROOT / "docs" / "telemetry-contract.md").read_text()
    rows = [
        line.split(None, 1)
        for line in text.splitlines()
        if line.startswith("jwt.sub")
    ]
    assert rows, (
        "no `jwt.sub <value>` row found in docs/telemetry-contract.md's "
        "agentgateway attribute block -- that block is where the measured "
        "value is published, and this guard cannot check it without it"
    )
    for row in rows:
        assert len(row) == 2, f"jwt.sub row publishes no value: {row!r}"
        published = row[1].strip().strip('"')
        assert published == SETTLEMENT_SPIFFE_ID, (
            f"the documented jwt.sub value is {published!r}; the gateway "
            f"records the *caller's* attested identity, "
            f"{SETTLEMENT_SPIFFE_ID!r}. "
            f"{GATEWAY_SPIFFE_ID!r} is the audience, not the subject."
        )


# --- The gateway's verification policy ------------------------------------


def _jwt_auth_blocks(config: dict) -> list[tuple[str, dict]]:
    """The `jwtAuth` block of **every** route, and every route must have one.

    Previously this read `binds[0].listeners[0].routes[0]` and additionally
    asserted `len(routes) == 1`. Review showed why that is not the same thing:
    a *second listener* with no `jwtAuth` satisfied every assertion in this
    file, because nothing looked past the first one. The route count assertion
    was load-bearing for exactly one level of a three-level structure.

    So the shape of the check is inverted. Instead of "there is one route and
    it is authenticated", it is "every route that exists is authenticated" --
    which stays true when a route or a listener is legitimately added, and goes
    red the moment one of them is anonymous.
    """
    blocks = []
    unauthenticated = []
    for where, route in _all_routes(config):
        policies = route.get("policies") or {}
        jwt_auth = policies.get("jwtAuth")
        if not jwt_auth:
            unauthenticated.append(
                f"{where}: policies={sorted(policies) or 'none at all'}"
            )
            continue
        blocks.append((where, jwt_auth))
    assert not unauthenticated, (
        "every route on this gateway must carry a jwtAuth policy; these do "
        "not, and are an unauthenticated way in:\n  "
        + "\n  ".join(unauthenticated)
    )
    return blocks


def test_the_gateway_requires_a_valid_jwt_rather_than_merely_validating_one():
    """`mode: strict` is the one setting in that block that changes behaviour.

    agentgateway v1.5.0's `Mode` enum marks `Optional` as `#[default]`,
    documented as "Validate the JWT when present. Warning: this allows requests
    without a JWT." At the default, a caller that sent no credential at all
    would sail through and this task's claim would hold only for callers that
    volunteered one. Both accidental spellings that mean "allow anonymous"
    (`optional`, `permissive`) are named in the failure message so the fix is
    unambiguous.

    Checked on every route, not the first one: a route left at the default is
    an anonymous entrance whether it is the only route or the second.
    """
    for where, jwt_auth in _jwt_auth_blocks(_yaml(GATEWAY_CONFIG)):
        assert jwt_auth.get("mode") == "strict", (
            f"{where}.jwtAuth.mode must be 'strict'; got "
            f"{jwt_auth.get('mode')!r}. 'optional' (the agentgateway default) "
            "and 'permissive' both admit requests with a missing or invalid "
            "JWT, which would make every identity assertion in this milestone "
            "conditional on the caller's goodwill."
        )


def test_the_gateway_trusts_exactly_the_issuer_spire_is_configured_to_mint():
    """One value, `global.spire.jwtIssuer`, feeds both the `iss` claim SPIRE
    mints and the `issuer` the gateway checks. Derived independently they
    become two guesses at the same string, and a mismatch is a 401 with nothing
    visibly wrong in the token. Checked on every route's block, not the
    first."""
    configured_issuer = _yaml(SPIRE_VALUES)["global"]["spire"]["jwtIssuer"]
    for where, jwt_auth in _jwt_auth_blocks(_yaml(GATEWAY_CONFIG)):
        assert jwt_auth.get("issuer") == configured_issuer, (
            f"{where} trusts issuer {jwt_auth.get('issuer')!r} but SPIRE is "
            f"configured to mint {configured_issuer!r}"
        )
        jwks_url = (jwt_auth.get("jwks") or {}).get("url")
        assert jwks_url == f"{configured_issuer}/keys", (
            f"{where}'s JWKS URL must be the issuer's own /keys endpoint (the "
            f"`jwks_uri` the discovery document advertises); got {jwks_url!r}"
        )
        assert "file" not in (jwt_auth.get("jwks") or {}), (
            f"{where} uses a `jwks.file`, which goes stale the first time "
            "SPIRE rotates its JWT signing key; the key set must be fetched"
        )


def test_the_gateway_carries_only_the_reviewed_authorization_mechanism():
    """**SUPERSEDES `test_the_gateway_carries_no_authorization_policy_yet`
    (M2.1 Task 4). Read this before assuming a guard was relaxed.**

    That guard's own docstring read: "M2.1 makes identity real; a later
    milestone decides what to do with it" and, in its failure message, "The
    denial is a later milestone." Its forbidden set was
    `("mcpAuthorization", "authorization", "extAuthz", "mcpGuardrails")`.

    That later milestone is M2.2, and its Task 4 is what added `mcpGuardrails`
    -- the whole point of the milestone. So this is not an M2.1 measurement
    quietly widened to accommodate new code: it is a scope assertion that
    named its own expiry condition, reaching it. The distinction matters
    because the two are indistinguishable from the diff alone, which is why it
    is written out here rather than left to a commit message.

    What is deliberately preserved is the property the original guard actually
    protected: that no *unreviewed* authorization mechanism appears on this
    gateway. Three of the four forbidden keys are still forbidden. A native
    `mcpAuthorization` CEL rule, a generic `authorization` block or an
    `extAuthz` hook would each be a second, independent place a payment could
    be permitted or denied -- with no test asserting its shape, no fail-closed
    guarantee, and nothing in a trace distinguishing its decision from the
    PDS's. `mcpGuardrails` moves from "forbidden" to "required, and asserted in
    detail": `tests/unit/test_stage2_pds_config.py` pins its failure mode, its
    phase, its target and its identity contract, key by key. The net number of
    unchecked authorization mechanisms this file permits is still zero.

    Structural, over the parsed document, for the original's reason: a comment
    discussing `mcpAuthorization` must not trip it (this config now carries
    several paragraphs of them) and an actual rule block must not hide from it.
    Still enumerated over every bind, listener and route, and over
    `frontendPolicies`, which is a document-level block Task 3's tracing lives
    in and therefore a third place a policy can hide.
    """
    config = _yaml(GATEWAY_CONFIG)
    forbidden = ("mcpAuthorization", "authorization", "extAuthz")
    offenders = []
    for where, node in _all_routes(config) + _all_listeners(config):
        policies = node.get("policies") or {}
        present = [key for key in forbidden if key in policies]
        if present:
            offenders.append(f"{where}: {present}")
    frontend = config.get("frontendPolicies") or {}
    present = [key for key in forbidden if key in frontend]
    if present:
        offenders.append(f"frontendPolicies: {present}")
    assert not offenders, (
        "this gateway's only authorization mechanism is `mcpGuardrails`, which "
        "tests/unit/test_stage2_pds_config.py asserts key by key -- including "
        "that it is fail-closed. Found a second, unasserted one at:\n  "
        + "\n  ".join(offenders)
        + "\nA second place a payment can be permitted is a second place the "
        "platform can stop governing without any test noticing."
    )
    # The other half of "only": the reviewed mechanism must actually be there.
    # Without this the test above passes on a gateway with no authorization at
    # all, which is precisely the M2.1 state this one supersedes -- a guard
    # that permits the thing it replaced is not a replacement.
    governed = [
        where
        for where, route in _all_routes(config)
        if ((route.get("policies") or {}).get("mcpGuardrails") or {}).get("processors")
    ]
    assert governed, (
        "no route on this gateway carries an mcpGuardrails processor. M2.2 "
        "replaced M2.1's 'no authorization yet' with 'exactly this "
        "authorization', so an absent guardrail is now a regression, not a "
        "prior state -- the gateway would permit every tool call it can "
        "authenticate."
    )


def test_the_gateway_config_contains_no_dollar_sign_at_all():
    """agentgateway v1.5.0 expands environment variables in this file's raw
    text BEFORE parsing it as YAML -- comments included -- and an unset name is
    a hard startup failure ("error looking key 'PWD' up: environment variable
    not found"). This was hit twice while writing the file, once by a comment
    that quoted a shell command and once by the comment fixing it.

    Asserted over the whole file rather than over the parsed document,
    deliberately: the parsed document is exactly where a comment does not
    appear, and a comment is what broke it.
    """
    text = GATEWAY_CONFIG.read_text()
    offenders = [
        f"line {i + 1}: {line.strip()}"
        for i, line in enumerate(text.splitlines())
        if "$" in line
    ]
    assert not offenders, (
        "this file must contain no dollar signs: the gateway substitutes "
        "environment variables into its raw text before parsing, comments "
        "included, and an unset name aborts startup:\n  "
        + "\n  ".join(offenders)
    )


# --- SPIRE's telemetry reaches the collector ------------------------------


def test_both_collector_configs_scrape_spires_metrics_services():
    """SPIRE has no OTLP exporter, so the only way its telemetry joins this
    system's pipeline is a Prometheus scrape. The chart publishes no Service
    for either metrics port, so `deploy/platform/spire/metrics.yaml` provides
    them; if those two files stop agreeing, the scrape silently returns
    nothing.

    Both collector configs are checked, because `setup-kind.sh` picks one
    wholesale depending on whether a Dash0 token is present -- editing one is
    the drift `test_collector_configs_in_sync.py` exists for, and this is the
    same class of check one level down.

    Every port of every Service is enumerated, not `ports[0]`: a Service may
    declare several, and with first-port-only indexing a second port would be
    unscraped and invisible, while merely *reordering* the list would silently
    change which port the assertion was about.
    """
    services = [
        (doc["metadata"]["name"], port["port"])
        for doc in _yaml_docs(SPIRE_METRICS)
        if doc.get("kind") == "Service"
        for port in doc["spec"]["ports"]
    ]
    assert len(services) == 2, (
        f"expected two metrics Services with one port each; found {services}"
    )
    expected_targets = {
        f"{name}.northstar-platform.svc.cluster.local:{port}"
        for name, port in services
    }

    for path in COLLECTOR_CONFIGS:
        config = _yaml(path)
        receiver = config["receivers"].get("prometheus/spire")
        assert receiver, (
            f"{path.relative_to(REPO_ROOT)} has no `prometheus/spire` "
            "receiver, so SPIRE's metrics are exposed and thrown away"
        )
        scraped = {
            target
            for job in receiver["config"]["scrape_configs"]
            for static in job["static_configs"]
            for target in static["targets"]
        }
        assert scraped == expected_targets, (
            f"{path.relative_to(REPO_ROOT)} scrapes {sorted(scraped)}, but "
            f"deploy/platform/spire/metrics.yaml publishes "
            f"{sorted(expected_targets)}"
        )
        pipeline = config["service"]["pipelines"]["metrics"]["receivers"]
        assert "prometheus/spire" in pipeline, (
            f"{path.relative_to(REPO_ROOT)}'s metrics pipeline does not list "
            f"the prometheus/spire receiver; got {pipeline}"
        )


# --- setup-kind.sh (text, comment-stripped) -------------------------------


@pytest.mark.parametrize(
    "variable,expected",
    [
        ("SPIRE_CHART_VERSION", PINNED_SPIRE_CHART_VERSION),
        ("SPIRE_CRDS_CHART_VERSION", PINNED_SPIRE_CRDS_CHART_VERSION),
    ],
)
def test_setup_kind_pins_each_spire_chart_version_exactly_once(variable, expected):
    """Comment-stripped, and requires exactly one assignment, so neither a
    comment nor a leftover second assignment can satisfy it."""
    text = _effective_script()
    assignments = re.findall(rf'\b{variable}="([^"]+)"', text)
    assert len(assignments) == 1, (
        f"expected exactly one effective {variable} assignment, found "
        f"{len(assignments)}: {assignments}"
    )
    assert assignments[0] == expected, (
        f"setup-kind.sh pins {variable}={assignments[0]}, this guard says "
        f"{expected}"
    )
    # Asserted as a whole stripped line, not as a substring of the file. A
    # substring check was satisfiable by a *trailing* comment quoting the flag
    # (whole-line comments are already stripped, trailing ones are not); a line
    # that is exactly `--version "${VAR}" \` cannot be a comment, because the
    # continuation backslash would be inside it.
    wanted = f'--version "${{{variable}}}" \\'
    lines = [line.strip() for line in text.splitlines()]
    assert lines.count(wanted) == 1, (
        f"expected exactly one line reading `{wanted}` -- the helm install must "
        f"pass ${{{variable}}} explicitly, as its own continued argument; an "
        "unpinned `helm install` floats to whatever the repo cache holds. "
        f"Found {lines.count(wanted)}."
    )


def test_setup_kind_uses_the_hardened_charts_repo():
    text = _effective_script()
    assignments = re.findall(r'\bSPIFFE_HELM_REPO="([^"]+)"', text)
    assert assignments == [HARDENED_CHARTS_REPO], (
        f"expected exactly one SPIFFE_HELM_REPO assignment naming the hardened "
        f"charts repo {HARDENED_CHARTS_REPO!r}; found {assignments!r}. The "
        "older spiffe/helm-charts location does not carry these charts."
    )


# --- Behavioural: run the real script against fakes ----------------------
#
# Stronger than any text assertion, because nothing written in the file --
# comment, string literal or echo -- can satisfy these. Each builds a throwaway
# tree containing the real `setup-kind.sh`, runs it with fake
# `kind`/`kubectl`/`docker`/`helm` ahead of it on PATH, and asserts on what it
# actually invoked.
#
# These fakes differ from `test_stage2_cilium_config.py`'s in one deliberate
# way: `kubectl cluster-info` SUCCEEDS here, so the script runs on past cluster
# creation and reaches the SPIRE step this module is about. It then dies at
# Step 9, because `scripts/rebuild-and-deploy.sh` is not copied into the
# throwaway tree -- which is also what makes the ordering assertion below
# meaningful: anything logged before the script died necessarily ran before the
# workloads would have been deployed.
#
# Nothing here touches a real cluster, a real registry, Docker, the network, or
# the repo's own `.env`.

FAKES = {
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo "true"; exit 0 ;;
esac
exit 0
""",
    "kind": r"""#!/usr/bin/env bash
echo "kind $*" >> "$FAKE_LOG"
if [[ "$1 $2" == "get clusters" ]]; then exit 0; fi
if [[ "$1 $2" == "get nodes" ]]; then echo northstar-control-plane; exit 0; fi
exit 0
""",
    # Succeeds on everything, including `cluster-info`, so the script proceeds
    # to the SPIRE step. The kindnet probe fails (exit 1), i.e. "no default CNI
    # present", which is the stage-2 path.
    "kubectl": r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_LOG"
if [[ "$*" == *"get daemonset kindnet"* ]]; then exit 1; fi
cat >/dev/null 2>&1 || true
exit 0
""",
    "helm": r"""#!/usr/bin/env bash
echo "helm $*" >> "$FAKE_LOG"
exit 0
""",
}

_COPIED = [
    Path("scripts/setup-kind.sh"),
    Path("deploy/kind/stage1.yaml"),
    Path("deploy/kind/stage2.yaml"),
    Path("deploy/k8s/namespaces.yaml"),
    Path("deploy/platform/cilium-values.yaml"),
    Path("deploy/platform/spire/values.yaml"),
    Path("deploy/platform/spire/registration.yaml"),
    Path("deploy/platform/spire/metrics.yaml"),
    # M4 Task 1, fix round 1: setup-kind.sh now *reads* the collector config
    # (it strips the stage-4-only OpenSearch regions below stage 4), so a tree
    # without these files makes the script die at Step 7 under `set -e` before
    # it ever reaches the SPIRE step these tests assert on. The ConfigMap
    # creation itself was always faked, which is why they were absent before.
    Path("deploy/observability/collector-config.yaml"),
    Path("deploy/observability/collector-config-dash0.yaml"),
]


def _run_setup_kind(stage, tmp_path):
    """Copy the real script into a throwaway tree and run it against fakes.

    Returns (returncode, combined_output, invocations).
    """
    tree = tmp_path / "tree"
    for relative in _COPIED:
        destination = tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    # A fake .env with only the one variable the script requires. The repo's
    # real .env is never read, and no secret is involved.
    (tree / ".env").write_text(
        "OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE=fake/collector:0\n"
    )

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in FAKES.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log = tmp_path / "invocations.log"
    log.write_text("")
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_LOG"] = str(log)
    # Never let a real credential reach the subprocess.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("DASH0_AUTH_TOKEN", None)

    proc = subprocess.run(
        ["bash", str(tree / "scripts" / "setup-kind.sh"), "--stage", stage],
        capture_output=True, text=True, env=env, timeout=180,
    )
    combined = proc.stdout + proc.stderr
    invocations = log.read_text().splitlines()
    _write_capture(tmp_path, stage, proc.returncode, combined, invocations)
    return proc.returncode, combined, invocations


def _capture_path(tmp_path):
    """Where `_run_setup_kind` leaves its evidence. Derived from `tmp_path`, so
    an assertion can name the file without the harness having to hand it back
    and without changing this module's long-standing 3-tuple return."""
    return tmp_path / "setup-kind-capture.txt"


def _write_capture(tmp_path, stage, returncode, combined, invocations):
    """Persist the sandboxed run's whole output next to the test that caused it.

    **Why this exists (M2.2 final review, adjudication 4 / ruling R38).**
    `test_stage_two_installs_spire_with_the_pinned_versions_and_values` failed
    once, mid-task, and not in ~90 further runs. Its assertion message already
    interpolates this script's entire captured output, so the failing run
    printed the reason it died — and the record kept only the line number. The
    evidence was on screen and was discarded, and the ruling has now cost two
    milestones' worth of re-runs precisely because nobody has that output.

    So the output is written to a file as well as interpolated. `tmp_path` is
    pytest's own per-test temporary directory, which pytest retains for the
    last three runs, so this survives the re-run that usually clears the
    intermittency — which is the whole problem: the natural response to a red
    R38 is to re-run, and a re-run is exactly what destroys the terminal
    scrollback.

    Written on every run, not only on failure: the assertion has no way to know
    a failure is coming, and a passing run's capture is the healthy baseline to
    diff the next failure against. It is a few kilobytes in a temporary
    directory.

    Nothing secret is in it. `_run_setup_kind` pops `ANTHROPIC_API_KEY` and
    `DASH0_AUTH_TOKEN` out of the subprocess environment and writes a
    throwaway `.env` containing one non-secret variable, so the script under
    test never sees a credential to print.
    """
    try:
        _capture_path(tmp_path).write_text(
            f"# scripts/setup-kind.sh --stage {stage}, sandboxed harness\n"
            f"# returncode: {returncode}\n"
            f"# {len(invocations)} faked tool invocations\n"
            "\n--- invocations (docker/kind/kubectl/helm, in order) ---\n"
            + "\n".join(invocations)
            + "\n\n--- stdout + stderr ---\n"
            + combined
            + "\n"
        )
    except OSError:  # pragma: no cover - diagnostics must never fail a test
        pass


def _diagnostics(tmp_path) -> str:
    """The tail every assertion in this harness carries, naming the capture
    file. See `_write_capture` for why (adjudication 4 / R38)."""
    return (
        f"\n\nFull harness capture (invocations, stdout and stderr) written to:"
        f"\n  {_capture_path(tmp_path)}\n"
        "pytest keeps the last three temporary directories, so this file "
        "survives the re-run that clears this failure. If R38 has recurred, "
        "paste that file rather than re-running: the reason the sandboxed "
        "script exited early is in it."
    )


_bash_only = pytest.mark.skipif(sys.platform == "win32", reason="bash-only harness")


def _helm_installs(calls):
    return [c for c in calls if c.startswith("helm upgrade --install")]


@_bash_only
def test_stage_two_installs_the_crds_chart_before_the_spire_chart(tmp_path):
    """The ordering the chart itself requires: `spiffe/spire` on its own fails
    on the missing spire.spiffe.io CRDs, which its own bundled
    spire-controller-manager needs before it can reconcile a single
    ClusterSPIFFEID. Proven by what the script invoked, not by reading it."""
    _, out, calls = _run_setup_kind("2", tmp_path)
    installs = _helm_installs(calls)
    crds = [i for i, c in enumerate(installs) if "spiffe/spire-crds" in c]
    spire = [
        i
        for i, c in enumerate(installs)
        if "spiffe/spire " in c or c.rstrip().endswith("spiffe/spire")
    ]
    # Same failure mode as the R38 assertion below, from the same harness, so
    # it carries the same pointer at the capture file (adjudication 4).
    assert crds, (
        f"`spiffe/spire-crds` was never installed. Invocations:\n{installs}\n{out}"
        + _diagnostics(tmp_path)
    )
    assert spire, (
        f"`spiffe/spire` was never installed. Invocations:\n{installs}\n{out}"
        + _diagnostics(tmp_path)
    )
    assert max(crds) < min(spire), (
        "the CRDs chart must be installed before the spire chart; got, in "
        f"order:\n" + "\n".join(installs)
    )


@_bash_only
def test_stage_two_installs_spire_with_the_pinned_versions_and_values(tmp_path):
    """Pins reach `helm` as arguments, not merely as shell variables.

    Checks **every** matching invocation rather than the first. `next(...)` was
    the same first-element narrowness as the config walk: a second, unpinned
    `helm upgrade --install spire` later in the script would have been the one
    that actually determined the installed version, and the assertion would
    have been about the earlier one.
    """
    _, out, calls = _run_setup_kind("2", tmp_path)
    installs = _helm_installs(calls)
    crds = [c for c in installs if "spiffe/spire-crds" in c]
    spire = [c for c in installs if "spiffe/spire " in c and "spire-crds" not in c]
    # THIS is the R38 assertion. When it fails, `installs` lacks the
    # `helm upgrade --install spire` line, which means the sandboxed script
    # exited before scripts/setup-kind.sh's SPIRE install -- so the reason is
    # in the output below and in the capture file named at the end of this
    # message. Do not re-run before reading it (adjudication 4).
    assert crds and spire, (
        f"missing a SPIRE install:\n{installs}\n{out}" + _diagnostics(tmp_path)
    )
    for call in crds:
        assert f"--version {PINNED_SPIRE_CRDS_CHART_VERSION}" in call, call
    for call in spire:
        assert f"--version {PINNED_SPIRE_CHART_VERSION}" in call, call
        assert "deploy/platform/spire/values.yaml" in call, (
            "every spire install must be handed this repo's values file; "
            "without it the chart's own defaults apply and the trust domain is "
            f"example.org: {call}"
        )
        assert "--namespace northstar-platform" in call, call


@_bash_only
def test_spire_is_installed_before_the_workloads_that_mount_its_socket(tmp_path):
    """A pod requesting a CSI volume whose driver is not registered stays in
    ContainerCreating rather than failing fast, so installing SPIRE after the
    workloads would wedge settlement-agent and block the script on `rollout
    status`. The behavioural proof is that the script died at Step 9 with the
    SPIRE install already logged."""
    rc, out, calls = _run_setup_kind("2", tmp_path)
    assert rc != 0, (
        "this harness expects the run to stop at Step 9 (rebuild-and-deploy.sh "
        f"is not copied into the throwaway tree); it exited 0 instead:\n{out}"
    )
    assert "rebuild-and-deploy.sh" in out, (
        f"expected the run to reach and fail at Step 9; got:\n{out[-2000:]}"
    )
    assert _helm_installs(calls), (
        "SPIRE was not installed before the script reached Step 9, so it would "
        f"be installed after the workloads that mount its socket:\n{out}"
    )
    assert any("spiffe/spire" in c for c in _helm_installs(calls))
    applies = [c for c in calls if c.startswith("kubectl") and "registration.yaml" in c]
    assert applies, (
        "the registration entries must be applied as part of the stage-2 "
        f"install; kubectl was invoked as:\n" + "\n".join(
            c for c in calls if c.startswith("kubectl")
        )
    )


@_bash_only
def test_stage_two_installs_no_cert_manager(tmp_path):
    """The behavioural half of `test_cert_manager_is_not_installed_below_stage_5`.

    That one reads the script and so could in principle be satisfied by
    something textual; this one asserts on what the script actually invoked,
    which nothing written in a comment or a `--help` heredoc can supply.
    """
    _, out, calls = _run_setup_kind("2", tmp_path)
    offenders = [c for c in calls if re.search(r"cert-?manager|jetstack", c)]
    assert not offenders, (
        "`--stage 2` invoked cert-manager tooling:\n"
        + "\n".join(offenders)
        + f"\n{out}"
    )


@_bash_only
def test_stage_one_installs_no_spire_at_all(tmp_path):
    """Stage 1 must stay bring-up-able without any of this: the whole point of
    the stage profiling is that a laptop runs exactly one stage's worth of
    components."""
    _, out, calls = _run_setup_kind("1", tmp_path)
    spire_calls = [c for c in calls if "spiffe" in c or "spire" in c]
    assert not spire_calls, (
        f"`--stage 1` touched SPIRE:\n" + "\n".join(spire_calls) + f"\n{out}"
    )
