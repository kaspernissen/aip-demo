"""Live read/write access to the `northstar-autonomy` contract, and nothing
else -- no trace assertions, no browser, no policy knowledge.

**Why this lives here rather than inside `tests/e2e/test_progressive_autonomy.py`
itself.** That file is not the only one with an opinion about the contract's
live state: `test_policy_denial.py::denial_run` and
`test_human_in_the_loop.py`'s claims both drive a real settlement expecting the
junior identity to be *denied* under `settlement-agent.assisted.execute_limit`.
That assumption holds only as long as nothing in this suite has flipped the
targeting block away from its shipped no-op, even transiently. Both fixtures
import `assert_assisted` from here and call it before driving, so a leaked
flip from a previous, crashed run of `test_progressive_autonomy.py` fails
loudly and immediately at the one place it would otherwise silently
invalidate a denial regression test, rather than producing a confusing
"the junior executed?!" failure three assertions later.

**The flip is live-only, on purpose: one word in the *contract*, not one word
in the repo.** `deploy/platform/openfeature/featureflag.yaml` is never read
or written here. `set_targeted_branch` issues a `kubectl patch --type=json`
against the live `FeatureFlag` object only, so `tests/test_contract.py`'s
`test_current_level_matches_the_feature_flags_targeting_branches` (which
parses the checked-in YAML, never the cluster) cannot be affected by
anything this module does, and `scorer/contract.py`'s `CURRENT_LEVEL`
constant never needs to move for this file's flip to be real (see that
module's own docstring for why the audit trusts the *published* contract
and not a live-only patch: a transient flip through here, if it were ever
scored, would legitimately read `level-does-not-match-contract`, which is
the audit doing its job, not a defect in this harness).

**Propagation is verified, not assumed.** flagd's `FeatureFlagSource` names
`provider: kubernetes` (`deploy/platform/openfeature/featureflagsource.yaml`),
so it watches the object through the API server rather than polling a
mounted ConfigMap -- measured on this cluster at under a second, patch to
resolved value, via the OFREP REST port (8016) the sidecar exposes inside
the policy-decision-service pod. `set_targeted_branch` blocks until flagd
itself reports the new value for the targeted caller, not merely until
`kubectl patch` returns -- the latter only proves the API server persisted
the spec, which is a weaker claim than "the decision path will see it".
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import requests

KUBE_CONTEXT = "kind-northstar"
PLATFORM_NAMESPACE = "northstar-platform"
FLAG_RESOURCE = "featureflag/northstar-autonomy"
PDS_LABEL_SELECTOR = "app=policy-decision-service"
OFREP_PORT = 8016

# Index 1 of the targeting `if` array: `[condition, then, else]`. This is the
# one branch that ships as a no-op ("assisted") and the only one this module
# ever writes. Index 2 (the else branch, every caller the flag does not name)
# and `defaultVariant` (the fail-closed path) are never touched -- both must
# stay "assisted" always, flip or no flip.
TARGETING_THEN_PATH = "/spec/flagSpec/flags/autonomy-level/targeting/if/1"

# The one caller `featureflag.yaml`'s targeting `in` check names -- reproduced
# verbatim from that file rather than imported from `scorer/authority.py`
# (a different service's venv, not importable from `tests/e2e`'s own).
TARGETED_CALLER = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

ASSISTED = "assisted"
AUTONOMOUS = "autonomous"


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", PLATFORM_NAMESPACE, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_committed_then_branch() -> str:
    """The live object's targeting `if[1]` value, read fresh from the API
    server -- the Kubernetes spec, not flagd's resolved answer. Used for the
    cheap, synchronous "did my patch land" check; `_resolved_variant` below
    is the one that proves propagation."""
    result = _kubectl(
        "get", FLAG_RESOURCE, "-o",
        "jsonpath={.spec.flagSpec.flags.autonomy-level.targeting.if[1]}",
    )
    assert result.returncode == 0, (
        f"could not read {FLAG_RESOURCE}'s targeting branch in "
        f"{PLATFORM_NAMESPACE}: {result.stderr.strip()}"
    )
    return result.stdout.strip()


def _pds_pod_name() -> str:
    result = _kubectl(
        "get", "pods", "-l", PDS_LABEL_SELECTOR,
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    assert result.returncode == 0 and result.stdout.strip(), (
        "no policy-decision-service pod found to verify OFREP resolution "
        f"against (namespace {PLATFORM_NAMESPACE}): {result.stderr.strip()}"
    )
    return result.stdout.strip()


def _resolved_variant(pod: str, timeout: float = 30.0) -> str:
    """What flagd itself currently resolves for `TARGETED_CALLER`, read
    through a fresh ephemeral port-forward to the sidecar's own OFREP port --
    the same "ask kubectl for a free port and parse its own announcement"
    convention `tests/e2e/test_caller_identity.py::gateway_url` established,
    reused rather than copied with a guessed local port that could collide
    with a fixed one."""
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", PLATFORM_NAMESPACE,
            "port-forward", f"pod/{pod}", f":{OFREP_PORT}",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        port = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(
                rf"Forwarding from 127\.0\.0\.1:(\d+) -> {OFREP_PORT}", line
            )
            if match:
                port = int(match.group(1))
                break
        assert port, (
            f"could not establish a port-forward to pod/{pod}:{OFREP_PORT} "
            "(flagd's OFREP listener) within 15s"
        )

        url = f"http://127.0.0.1:{port}/ofrep/v1/evaluate/flags/autonomy-level"
        body = {
            "context": {
                "targetingKey": TARGETED_CALLER,
                "spiffeId": TARGETED_CALLER,
            }
        }
        deadline = time.monotonic() + timeout
        last_response = None
        while time.monotonic() < deadline:
            try:
                resp = requests.post(url, json=body, timeout=5)
                last_response = resp.text
                if resp.status_code == 200:
                    return resp.json()["value"]
            except requests.RequestException as exc:
                last_response = repr(exc)
            time.sleep(0.5)
        raise AssertionError(
            f"flagd's OFREP endpoint never returned a resolvable value within "
            f"{timeout}s of the patch landing on the API server; last "
            f"response: {last_response}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def set_targeted_branch(variant: str, *, propagation_timeout: float = 30.0) -> None:
    """Patch the live contract's targeting `if[1]` branch to `variant`, and
    block until flagd itself resolves it for `TARGETED_CALLER` -- not merely
    until the API server accepts the write.

    A no-op call (the branch is already `variant`) still confirms
    propagation before returning, on purpose: `test_progressive_autonomy.py`
    calls this with `ASSISTED` first, to establish a known starting state
    rather than trust whatever a previous run left behind, and again in a
    `finally` to restore it -- both calls need the same guarantee a genuine
    flip does.
    """
    assert variant in (ASSISTED, AUTONOMOUS), f"not a declared variant: {variant!r}"
    patch = json.dumps(
        [{"op": "replace", "path": TARGETING_THEN_PATH, "value": variant}]
    )
    result = _kubectl("patch", FLAG_RESOURCE, "--type=json", f"-p={patch}")
    assert result.returncode == 0, (
        f"kubectl patch of {FLAG_RESOURCE} to {variant!r} failed: "
        f"{result.stderr.strip()}"
    )

    committed = get_committed_then_branch()
    assert committed == variant, (
        f"patch reported success but the API server's own object still "
        f"reads {committed!r}, not {variant!r} -- kubectl lied or a "
        "concurrent writer raced this one"
    )

    pod = _pds_pod_name()
    resolved = _resolved_variant(pod, timeout=propagation_timeout)
    assert resolved == variant, (
        f"the FeatureFlag object reads {variant!r} but flagd's own OFREP "
        f"resolution for {TARGETED_CALLER!r} still answers {resolved!r} -- "
        "the kubernetes sync watch has not caught up, or something else "
        "changed underneath this call"
    )


def resolved_level(timeout: float = 15.0) -> str:
    """What flagd itself resolves for `TARGETED_CALLER` right now -- the answer
    the decision path consults, not the Kubernetes object's spec. Public so the
    stage driver (`scripts/demo autonomy`) prints the same value the tests
    assert on."""
    return _resolved_variant(_pds_pod_name(), timeout=timeout)


def assert_assisted(context: str) -> None:
    """Fail loudly, immediately, if the live contract is not in its shipped
    refusing state for the targeted caller -- called before a fixture drives
    a settlement that assumes the junior identity is denied.

    Reads flagd's own resolved answer, the same one the decision path
    itself consults, rather than the Kubernetes object's spec: a genuine
    denial regression test must fail on what the platform will actually do,
    not on what the YAML happens to say.
    """
    pod = _pds_pod_name()
    resolved = _resolved_variant(pod, timeout=15.0)
    assert resolved == ASSISTED, (
        f"{context}: the live contract resolves {resolved!r} for "
        f"{TARGETED_CALLER!r}, not {ASSISTED!r}. This fixture's whole "
        "premise is that the junior identity is denied under "
        "settlement-agent.assisted.execute_limit -- if "
        "test_progressive_autonomy.py's flip test crashed mid-run without "
        "reaching its own restore, this is where that leak surfaces, "
        "rather than three assertions further down as an inexplicable "
        "'the junior executed?!' failure."
    )
