"""The live gateway ConfigMap against the committed file — parsed, not bytes.

**The gap this closes** (M2.2 final review, adjudication 1). Every other guard
in this repository catches a *consequence* of the deployed gateway config being
wrong: `tests/unit/test_stage2_pds_config.py` reads the committed file,
`test_policy_denial.py::test_criterion_5_...` drives two SPIRE SVIDs at the
deployed gateway and reddens if the live config dropped `mcpGuardrails`,
flipped to `failOpen`, dropped `metadata.caller` or set `methods` to `off`, and
criterion 3 catches a live `requestHeaders.allowed` regression through span
parentage. Nothing asserted live-vs-committed identity in either direction. So
a live config that is *stricter* than the committed one, or that differs only
in `frontendPolicies.tracing`, was invisible: the committed file would be
reviewed, the cluster would be running something else, and every existing
assertion would pass.

**Why parsed and not bytes, which is the whole point of this test.** The
review measured the real drift on this cluster: two hunks, both inside a single
comment block, with `yaml.safe_load` of the two sides comparing **equal**. That
is not a defect — `scripts/setup-kind.sh` recreates the ConfigMap from the
committed file *and* restarts the deployment (a mounted ConfigMap change is
invisible to a running gateway, which reads its config once at startup with
`-f`), so convergence is guaranteed at the next `--stage 2`, and bouncing the
choke point mid-milestone to publish a comment would be the worse trade. A
byte comparison would have called that state a failure, wrongly, and the
predictable response to a guard that cries wolf is to delete it.

agentgateway parses this file as YAML. So the assertion that matches what the
gateway actually consumes is equality of the *parsed* documents: comment drift
stays legal, and any change that could reach the gateway fails. That is also
the only version of this test that can be left switched on permanently.

**Skips without a cluster, fails with one — and never skips on the object under
test.** Same split, and the same reasons, as
`tests/e2e/test_gateway_is_the_only_route.py::policy_present`: the skip
discriminates on a *different object* than the one being asserted. Whether this
is a stage-2 cluster at all is decided by the `agentgateway` Deployment; the
ConfigMap and its `config.yaml` key are the target, so their absence is a
**failure**, never a skip. A missing or empty ConfigMap on a cluster that is
running the gateway is itself a live/committed divergence — the gateway is
serving a configuration this repository cannot see — which is precisely what
this file exists to catch.

The first version of this test got that wrong: it skipped when the ConfigMap or
its key was missing, and the M2.2 fix-round re-review killed it by pointing the
test at a key the live ConfigMap does not carry, which produced `1 skipped` and
a skip reason that positively asserted a falsehood ("not a stage-2 cluster")
about a cluster that was plainly stage 2. `policy_present` records the same
defect in its own history, and `platform/policy-decision-service/tests/
conftest.py` cites the same incident as its reason to raise rather than skip. A
skip does not fail a run.

**Why this is not behind `RUN_BROWSER_TESTS=1`.** Like the two files above, it
spends no model tokens and starts no browser: two `kubectl get` calls and a
YAML parse.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_CONFIG = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "config.yaml"

KUBE_CONTEXT = "kind-northstar"
PLATFORM_NAMESPACE = "northstar-platform"
CONFIGMAP_NAME = "agentgateway-config"
# The stage-2 discriminator: a different object than the one under test, so a
# missing ConfigMap can fail instead of silently skipping.
GATEWAY_DEPLOYMENT = "agentgateway"
# The key `scripts/setup-kind.sh` creates the ConfigMap under
# (`--from-file=config.yaml=...`) and the file name the deployment mounts.
CONFIGMAP_KEY = "config.yaml"


def _kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def live_gateway_config_text() -> str:
    """The `config.yaml` value out of the live ConfigMap, as text.

    Read with `-o jsonpath` rather than `-o yaml` deliberately: the latter
    would hand back the ConfigMap *object*, whose own YAML quoting of a
    multi-thousand-character string value is an extra encoding layer between
    this test and the thing it wants to compare.
    """
    reachable = _kubectl("cluster-info")
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'}); "
            "run ./scripts/setup-kind.sh --stage 5"
        )

    # The stage-2 probe, deliberately NOT the object under test. If the gateway
    # Deployment is absent there is no deployed gateway to have drifted and
    # nothing to say; if it is present, everything below is an assertion.
    deployment = _kubectl(
        "-n", PLATFORM_NAMESPACE, "get", "deployment", GATEWAY_DEPLOYMENT,
        "-o", "name",
    )
    if deployment.returncode != 0:
        pytest.skip(
            f"no {GATEWAY_DEPLOYMENT} Deployment in {PLATFORM_NAMESPACE}, so "
            "this is not a stage-2 cluster and no gateway config is deployed "
            f"({deployment.stderr.strip() or 'no output'}); "
            "run ./scripts/setup-kind.sh --stage 5"
        )

    proc = _kubectl(
        "-n", PLATFORM_NAMESPACE, "get", "configmap", CONFIGMAP_NAME,
        "-o", "jsonpath={.data." + CONFIGMAP_KEY.replace(".", r"\.") + "}",
    )
    # Not a skip. The gateway Deployment exists, so a missing or empty
    # ConfigMap key means the running gateway is serving a configuration this
    # repository cannot read -- the divergence this file exists to catch.
    assert proc.returncode == 0 and proc.stdout.strip(), (
        f"THE DEPLOYED GATEWAY CONFIG CANNOT BE READ. {GATEWAY_DEPLOYMENT} is "
        f"deployed in {PLATFORM_NAMESPACE}, so this IS a stage-2 cluster, but "
        f"{CONFIGMAP_NAME} key {CONFIGMAP_KEY!r} is missing or empty. The "
        "gateway reads its config once at startup, so it is enforcing "
        "something -- and this repository has no way to see what. Re-run "
        "./scripts/setup-kind.sh --stage 4 to recreate the ConfigMap from the "
        f"committed file and restart the gateway ({proc.stderr.strip() or 'no output'})"
    )
    return proc.stdout


def test_the_live_gateway_configmap_parses_identically_to_the_committed_file(
    live_gateway_config_text,
):
    """Semantic drift fails; comment drift does not.

    One assertion, on `yaml.safe_load` of both sides. If it fails, the cluster
    is running a gateway configuration that was never reviewed, and the fix is
    `./scripts/setup-kind.sh --stage 4` (which recreates the ConfigMap from the
    committed file and restarts the deployment) or a deliberate commit of
    whatever the cluster is right about.
    """
    committed_text = COMMITTED_CONFIG.read_text()
    committed = yaml.safe_load(committed_text)
    assert isinstance(committed, dict) and committed, (
        f"{COMMITTED_CONFIG.relative_to(REPO_ROOT)} did not parse as a "
        "non-empty YAML mapping, so there is nothing to compare against"
    )

    live = yaml.safe_load(live_gateway_config_text)
    assert isinstance(live, dict) and live, (
        f"the live {CONFIGMAP_NAME}/{CONFIGMAP_KEY} did not parse as a "
        f"non-empty YAML mapping: {live_gateway_config_text[:300]!r}"
    )

    assert live == committed, (
        "THE DEPLOYED GATEWAY CONFIG IS NOT THE COMMITTED ONE. The live "
        f"{PLATFORM_NAMESPACE}/{CONFIGMAP_NAME} key {CONFIGMAP_KEY!r} parses "
        "to a different document than "
        f"{COMMITTED_CONFIG.relative_to(REPO_ROOT)}, so the file this "
        "repository reviews is not the policy the cluster enforces. Comments "
        "are not the cause -- this comparison is of parsed YAML and ignores "
        "them entirely.\n"
        f"  live top-level keys:      {sorted(live)}\n"
        f"  committed top-level keys: {sorted(committed)}\n"
        f"  live frontendPolicies:    {live.get('frontendPolicies')!r}\n"
        f"  committed frontendPolicies: {committed.get('frontendPolicies')!r}\n"
        "Re-run ./scripts/setup-kind.sh --stage 5 to recreate the ConfigMap "
        "from the committed file and restart the gateway (the restart is "
        "mandatory: a mounted ConfigMap change is invisible to a running "
        "gateway), or commit the change the cluster is right about."
    )
