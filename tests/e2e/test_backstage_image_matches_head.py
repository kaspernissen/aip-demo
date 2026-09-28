"""Asserts the running Backstage image contains the committed code.

A stale image can look completely healthy: `grep -rl autonomyContract /app`
finds nothing, the backend's plugin-init log never names
`autonomy-contract`, and `GET /api/autonomy-contract/status` is 404 --
while every RBAC check and every healthy-pod signal stays green, because
none of them need the application code to actually be running. A pod being
`Ready` and a route responding say nothing about which commit's code is
inside the image.

This test asserts what a manual check would otherwise have to eyeball:
`platform/backstage/Dockerfile`'s final stage
takes a `GIT_SHA` build-arg (default `unknown`) and stamps it into the image
label `org.opencontainers.image.revision`; `scripts/rebuild-and-deploy.sh`
passes `--build-arg GIT_SHA=$(git rev-parse HEAD)` for the `backstage`
component only. This test reads the label back off the actual image
`docker image inspect` finds and compares it against `git rev-parse HEAD`,
run from THIS checkout.

Deliberately does not settle for "the pod answers" or "the endpoint exists"
-- a 200 from a stale image that happens to still serve the same route shape
would pass that check too. The label is minted from a build-arg that must
change on every commit, so a stale image (old label), an image built without
this script (`unknown` label, the ARG's own default), or a build that reused
a cached final layer all fail this rather than being indistinguishable from
a current one.

Not gated behind `RUN_BROWSER_TESTS=1`: like `test_autonomy_writer_live_rbac.py`
and `test_egress_policy.py`, this spends no model tokens and drives no
browser -- two `docker`/`kubectl`/`git` invocations, well under a second.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.harness.kube import absent_or_fail

KUBE_CONTEXT = "kind-northstar"
NAMESPACE = "northstar-platform"
IMAGE = "localhost:5001/northstar/backstage:dev"


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module", autouse=True)
def stage_6_backstage_deployed_or_a_reason():
    """Skip if this is not even a live cluster; skip (not fail) if the
    cluster is live but stage 6's Backstage Deployment does not exist --
    that is an absence of stage 6, not a regression, the same discipline
    `test_autonomy_writer_live_rbac.py`'s own fixture uses for the Role it
    depends on."""
    reachable = _kubectl("cluster-info", timeout=15)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'})"
        )

    deployment = _kubectl("-n", NAMESPACE, "get", "deployment", "backstage", "-o", "name")
    if not absent_or_fail(deployment, f"deployment/backstage in {NAMESPACE}"):
        pytest.skip(
            f"needs stage 6: deployment/backstage does not exist in {NAMESPACE}"
        )


def _git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, f"`git rev-parse HEAD` failed: {proc.stderr}"
    return proc.stdout.strip()


def _image_revision_label() -> str:
    proc = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            IMAGE,
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"`docker image inspect {IMAGE}` failed: {proc.stderr} -- has it "
        "ever been built with scripts/rebuild-and-deploy.sh?"
    )
    return proc.stdout.strip()


def test_the_deployed_backstage_image_was_built_from_the_current_commit():
    head = _git_head()
    label = _image_revision_label()

    assert label and label not in ("<no value>", "unknown"), (
        f"{IMAGE} carries no usable org.opencontainers.image.revision label "
        f"(got {label!r}) -- it predates this guard, or was built without "
        "the GIT_SHA build-arg scripts/rebuild-and-deploy.sh now passes for "
        "backstage. Run ./scripts/rebuild-and-deploy.sh backstage."
    )
    assert label == head, (
        f"{IMAGE} was built from commit {label}, but this checkout's HEAD is "
        f"{head} -- the running image does not contain the committed code. "
        "A healthy pod and a passing endpoint check cannot see this: a "
        "security boundary can be reviewed and reported as verified against "
        "an image that predates the commit under review. Run "
        "./scripts/rebuild-and-deploy.sh backstage and re-run this test."
    )


def _pod_image_id() -> str:
    proc = _kubectl(
        "-n", NAMESPACE, "get", "pods", "-l", "app=backstage",
        "-o", "jsonpath={.items[0].status.containerStatuses[0].imageID}",
    )
    assert proc.returncode == 0, (
        f"could not read the running backstage pod's imageID: {proc.stderr}"
    )
    return proc.stdout.strip()


def _local_repo_digest() -> str:
    proc = subprocess.run(
        [
            "docker", "image", "inspect", IMAGE,
            "--format", "{{ index .RepoDigests 0 }}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"`docker image inspect {IMAGE}` failed getting a RepoDigest: "
        f"{proc.stderr} -- has it ever been pushed to the registry?"
    )
    return proc.stdout.strip()


def test_the_running_pod_is_using_the_exact_image_bytes_the_local_build_produced():
    """Compares image DIGESTS, not tags. Comparing the pod's
    `spec.containers[0].image` STRING against the mutable tag constant
    (`localhost:5001/northstar/backstage:dev`) would prove nothing: any
    node-cached older pull of that same tag carries an identical string, so
    equal tags do not mean equal content, and a guard built on tag equality
    could pass against a stale or even uncommitted image.

    Comparing DIGESTS instead closes that gap:
    `status.containerStatuses[0].imageID` is what the kubelet on
    THIS node actually pulled and is running (not merely what the Deployment
    spec asks for), and `docker image inspect --format
    '{{index .RepoDigests 0}}'` is the content-addressed digest the local
    build just pushed to the registry. A stale node-cached pull, a failed
    push, or a registry serving an older digest under the same tag all
    produce a real mismatch here; two different tags can never coincidentally
    share a digest the way they can share a mutable tag string.
    """
    pod_image_id = _pod_image_id()
    local_digest = _local_repo_digest()

    assert pod_image_id, (
        "the running backstage pod reports an empty imageID -- cannot verify "
        "which image bytes are actually running"
    )
    assert "@sha256:" in pod_image_id, (
        f"the pod's imageID {pod_image_id!r} does not look like a digest "
        "reference (expected '...@sha256:<hex>') -- cannot compare content"
    )
    assert "@sha256:" in local_digest, (
        f"`docker image inspect {IMAGE}` returned {local_digest!r}, not a "
        "digest reference -- has this image ever been pushed to "
        "localhost:5001 (scripts/rebuild-and-deploy.sh does this)?"
    )
    assert pod_image_id == local_digest, (
        f"the running pod's image digest is {pod_image_id!r}, but the local "
        f"build's registry digest is {local_digest!r} -- the node is NOT "
        "running the bytes this checkout just built and pushed -- identical "
        "tags, different content, which a tag comparison alone cannot see. "
        "Run ./scripts/rebuild-and-deploy.sh backstage and re-run this test."
    )
