"""The demo's own anti-vacuity e2e.

**Why this file exists, stated as bluntly as the two failures that made it
necessary.** This repo has shipped things reported as "verified" that were
not, because the check that passed was adjacent to the claim rather than
the claim itself:

* **A complete, correct catalog with every backend signal green** -- zero
  processor errors, "Collating documents for software-catalog succeeded",
  RBAC verified live -- and nobody could see the catalog: the guest auth
  provider 403s under `NODE_ENV=production`, which is what the in-cluster
  image actually runs under. *A backend health signal is not a delivery
  check.*
* **A security boundary reviewed and reported as verified** -- a
  `kubectl auth can-i` matrix, every permit permitted and every deny
  denied -- against an image built an **hour before** the commit under
  review. `grep -rl autonomyContract /app` found nothing in the running pod;
  `GET /api/autonomy-contract/status` was 404. The RBAC matrix was correct
  about the API server's authorizer, which needs no running application at
  all. *An RBAC matrix is not a delivery check.*

So every test below asserts against **the surface an audience touches**: over
HTTP, through a port-forward this file establishes itself, as a caller that
went through Backstage's own guest-auth flow (`POST /api/auth/guest/refresh`)
-- never against a manifest, a log line, or `kubectl auth can-i`. Where a
config-shaped check already exists and is sufficient
(`tests/unit/test_autonomy_writer_rbac.py`,
`tests/e2e/test_autonomy_writer_live_rbac.py`,
`tests/e2e/test_backstage_image_matches_head.py`) this file does not repeat
it; it drives the one thing none of those can: the actual HTTP route, as the
actual caller, changing the actual resource.

**The whole claim of this file is `test_driving_the_cards_write_over_http_...`
below, and it is proven by mutation, not by reading the code.** The
equivalent claim in `tests/e2e/test_progressive_autonomy.py` was verified by
neutering its harness -- `set_targeted_branch` patched to always write
`"assisted"` regardless of what was asked -- and watching it go red with
`assert 'refused' == 'executed'`, reproduced twice, once in a clean tree, with
the unmutated control passing in the same session. This file is held to the
same standard: mutating `platform/backstage/packages/backend/src/plugins/
autonomyContract/router.ts`'s `POST /variant` handler to skip the actual
`client.patchThenBranch(...)` call while still returning a 200 -- the exact
"looks like it worked" shape a convenience UI can produce -- reddens this
file's independent object-read assertion (not merely the HTTP response),
which is exactly what it is built to catch.

**`tests/harness/contract.py` is reused, not reimplemented.**
`contract.assert_assisted` is the precondition every
settlement-driving test here calls first (same reason
`test_policy_denial.py::denial_run` and `test_supervised.py::denial_run` call
it: a leaked flip from a crashed earlier run must fail loudly here, not three
assertions later as an inexplicable "the junior executed?!"). What this file
adds that `contract.py` does not have is the write path itself: `contract.py`
flips the contract with `kubectl patch` on purpose -- "one word in the
contract. It is not one word in the repo"; this file
flips the identical resource through Backstage's HTTP route instead, because
proving *that* changes the same real outcome is this file's entire
point. `contract.get_committed_then_branch()` (a synchronous, independent
`kubectl get` of the object's own spec) is used here specifically as the
witness that the HTTP write actually reached the object -- independent of
whatever the HTTP response itself claims.

**Cost.** The full claim
(`test_driving_the_cards_write_over_http_flips_a_refused_settlement_to_executed`)
spends two live settlements and a browser, same as
`test_progressive_autonomy.py`'s own headline test, and is gated the same way
(`RUN_BROWSER_TESTS=1`). Everything else in this file spends no model call and
drives no browser: a guest-token request, a handful of authenticated HTTP
calls, and (for the no-redeploy check and the OpenSearch-span check) a
`kubectl patch` through the HTTP route plus two `kubectl get pods`, or an
in-cluster OpenSearch query, respectively. The OpenSearch-span test
(`test_the_contract_change_span_lands_in_opensearch`) skips below stage 4,
the same way the rest of this file skips below stage 6 -- a golden-path
cluster has both, but the two stages are independent and this file does not
assume the higher one implies the lower.
"""
from __future__ import annotations

import datetime
import re
import subprocess
import time
import uuid

import pytest
import requests

from tests.harness import contract
from tests.harness.kube import absent_or_fail
from tests.unit.test_catalog_matches_the_tree import REPO_ROOT, _component_dirs

from tests.e2e.test_policy_denial import (  # noqa: F401 -- reused, not reimplemented
    ATTR_DECISION,
    ATTR_POLICY_AMOUNT,
    ATTR_POLICY_LIMIT,
    ATTR_POLICY_RULE,
    EXECUTE_TOOL,
    _decisions,
    _drive_browser,
    browser_test,
    claims_agent_route,
)
from tests.harness.live import get_payment as _get_payment  # same ledger-read helper
from tests.e2e.test_progressive_autonomy import _pod_uids_and_image_digests
from tests.e2e.test_opensearch_trace_ingest import (  # noqa: F401 -- reused, not reimplemented
    OBSERVABILITY_NAMESPACE,
    TRACE_INDEX,
    _opensearch,
    _opensearch_is_installed,
)

KUBE_CONTEXT = "kind-northstar"
PLATFORM_NAMESPACE = "northstar-platform"
AGENTS_NAMESPACE = "northstar-agents"
BACKSTAGE_PORT = 7007

# The same figure test_progressive_autonomy.py uses -- this file drives the
# identical claim through a second write path, not a different one. Under
# maker-checker (M8) EUR 5,800 waits for a human at `assisted` (limit 0) and
# settles on its own at `autonomous` (limit 6,000).
AMOUNT = 5800.0


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module", autouse=True)
def stage_6_backstage_deployed_or_a_reason():
    """Skip if this is not even a live cluster; skip if Backstage's own
    Deployment does not exist (absence of stage 6, not a regression); FAIL if
    Backstage exists but the write Role this whole file depends on does not
    (a stage-6 cluster missing its own write Role is this file's own
    regression, never an absence to skip past -- same discipline
    `test_autonomy_writer_live_rbac.py`'s own fixture uses for the identical
    Role).
    """
    reachable = _kubectl("cluster-info", timeout=15)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'})"
        )

    deployment = _kubectl(
        "-n", PLATFORM_NAMESPACE, "get", "deployment", "backstage", "-o", "name",
    )
    if not absent_or_fail(deployment, f"deployment/backstage in {PLATFORM_NAMESPACE}"):
        pytest.skip(
            f"needs stage 6: deployment/backstage does not exist in "
            f"{PLATFORM_NAMESPACE}. Run ./scripts/setup-kind.sh --stage 6."
        )

    role = _kubectl(
        "-n", PLATFORM_NAMESPACE, "get", "role", "backstage-autonomy-writer", "-o", "name",
    )
    assert absent_or_fail(role, f"role/backstage-autonomy-writer in {PLATFORM_NAMESPACE}"), (
        "Backstage is deployed (stage 6) but "
        "role/backstage-autonomy-writer is missing -- this is a regression, "
        "not an absence of stage 6: run `kubectl apply -f deploy/k8s/backstage.yaml`"
    )


@pytest.fixture(scope="module")
def backstage_url():
    """An ephemeral port-forward to `svc/backstage`, owned and torn down by
    this file -- same convention `test_caller_identity.py::gateway_url` and
    `tests/harness/contract.py::_resolved_variant` both already use: `:PORT`
    with no local port lets `kubectl` choose a free one and print it, so this
    can never collide with the repo-managed 7007 forward
    `scripts/start-demo.sh` may already have established.
    """
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", PLATFORM_NAMESPACE,
            "port-forward", "svc/backstage", f":{BACKSTAGE_PORT}",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(
                rf"Forwarding from 127\.0\.0\.1:(\d+) -> {BACKSTAGE_PORT}", line
            )
            if match:
                port = int(match.group(1))
                break
        assert port, (
            f"could not establish a port-forward to svc/backstage:{BACKSTAGE_PORT} "
            "within 30s"
        )
        base = f"http://127.0.0.1:{port}"
        # Confirm it actually answers before handing it to every test below --
        # a bound socket proves nothing, only an answering one does.
        deadline = time.monotonic() + 30
        last_exc = None
        while time.monotonic() < deadline:
            try:
                requests.get(f"{base}/", timeout=3)
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1)
        else:
            raise AssertionError(
                f"svc/backstage never answered a plain GET / within 30s of "
                f"the port-forward binding: {last_exc}"
            )
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _guest_token(base_url: str) -> str:
    """A real Backstage credential, obtained the only way a guest can:
    `POST /api/auth/guest/refresh`. Never printed, never logged -- assertion
    messages below carry response bodies and status codes, never this token.
    """
    resp = requests.post(
        f"{base_url}/api/auth/guest/refresh",
        json={},
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"could not obtain a guest token from {base_url} (HTTP "
        f"{resp.status_code}): {resp.text[:300]}"
    )
    token = resp.json().get("backstageIdentity", {}).get("token")
    assert token, f"guest/refresh response carried no backstageIdentity.token: {resp.text[:300]}"
    return token


@pytest.fixture(scope="module")
def auth_headers(backstage_url):
    return {"Authorization": f"Bearer {_guest_token(backstage_url)}"}


# --- 1. Backstage answers through the port-forward, as an authenticated ----
# --- caller -- and refuses an unauthenticated one ---------------------------


def test_unauthenticated_is_refused_authenticated_is_not(backstage_url, auth_headers):
    """The surface an audience actually touches: a bare HTTP client gets 401
    on every `/api/*` route (`httpRouter`'s default policy), and a
    caller that went through the real guest-auth flow gets through. This
    repo's stated posture -- "Backstage here is unauthenticated" -- means
    guest auth is trivial to obtain, not that no auth flow exists at all;
    this is the distinction this test pins.
    """
    unauth = requests.get(f"{backstage_url}/api/catalog/entities", timeout=10)
    assert unauth.status_code == 401, (
        f"an unauthenticated GET /api/catalog/entities returned "
        f"{unauth.status_code}, not 401: {unauth.text[:300]}"
    )

    authed = requests.get(
        f"{backstage_url}/api/catalog/entities", headers=auth_headers, timeout=10
    )
    assert authed.status_code == 200, (
        f"an authenticated GET /api/catalog/entities returned "
        f"{authed.status_code}: {authed.text[:300]}"
    )


# --- 2. The running code is the committed code -----------------------------


def test_the_autonomy_status_route_answers_which_a_stale_image_cannot(
    backstage_url, auth_headers,
):
    """Restated as a live HTTP assertion instead of a
    `docker image inspect` label comparison (that half is
    `tests/e2e/test_backstage_image_matches_head.py`, which this does not
    repeat). `GET /api/autonomy-contract/status` exists only because the
    autonomy-contract plugin registered it; measured directly against a
    stale image that predates the plugin, this route was 404 ("Plugin
    initialization started:" never named `autonomy-contract`). A 200 here,
    with this exact shape, cannot be produced by an image built before that
    plugin existed --
    whereas a healthy pod, a passing RBAC matrix, or a 200 from `/` (the
    static frontend bundle, unrelated to this plugin) all could be, and all
    were, which is the whole lesson this file exists to not repeat.
    """
    resp = requests.get(
        f"{backstage_url}/api/autonomy-contract/status", headers=auth_headers, timeout=10
    )
    assert resp.status_code == 200, (
        f"GET /api/autonomy-contract/status returned {resp.status_code}, not "
        f"200 -- either this plugin is not registered (the running image "
        f"predates the autonomy-contract plugin) or it failed for some "
        f"other reason: {resp.text[:300]}"
    )
    body = resp.json()
    for key in ("flag", "namespace", "state", "defaultVariant", "perIdentity"):
        assert key in body, f"status response is missing {key!r}: {body}"
    assert body["flag"] == "northstar-autonomy", body
    assert body["namespace"] == PLATFORM_NAMESPACE, body


# --- 3. The catalog serves every component the tree has, derived not -------
# --- retyped -----------------------------------------------------------------


def test_the_catalog_serves_every_component_the_tree_actually_has(
    backstage_url, auth_headers,
):
    """Derived from a directory walk, the same one
    `tests/unit/test_catalog_matches_the_tree.py` uses to guard that every
    component has its own `catalog-info.yaml` -- reused here, not
    reimplemented, so this file cannot drift into a hand-typed list of eight
    (or nine) names that quietly stops matching the tree. `_component_dirs`
    walks `agents/`, `apps/`, `platform/`, `systems/`, `tools/` and includes
    `platform/backstage` itself (a real ninth entry, not one of the eight
    components typically counted, but a real Component in the live catalog
    all the same -- Backstage catalogs itself).
    """
    expected = {d.name for d in _component_dirs(REPO_ROOT)}
    assert len(expected) >= 8, (
        f"the tree walk found only {sorted(expected)} -- fewer than the "
        "eight components this demo's catalog names; either the repo "
        "layout changed or this guard is reading the wrong root"
    )

    resp = requests.get(
        f"{backstage_url}/api/catalog/entities",
        params={"filter": "kind=component"},
        headers=auth_headers,
        timeout=15,
    )
    assert resp.status_code == 200, (
        f"GET /api/catalog/entities?filter=kind=component returned "
        f"{resp.status_code}: {resp.text[:300]}"
    )
    entities = resp.json()
    found = {e["metadata"]["name"] for e in entities}

    missing = expected - found
    assert not missing, (
        f"the live catalog is missing these components, each with its own "
        f"catalog-info.yaml in the tree: {sorted(missing)}. Found instead: "
        f"{sorted(found)}"
    )
    extra = found - expected
    assert not extra, (
        f"the live catalog serves components with no directory in the tree: "
        f"{sorted(extra)} -- either a stale/invented entry, or this guard's "
        f"walk (agents/, apps/, platform/, systems/, tools/) needs widening"
    )


# --- 4. The autonomy card reads live, per identity, on the refusing --------
# --- contract ----------------------------------------------------------------


def test_the_card_reads_assisted_for_the_identity_on_the_refusing_contract(
    backstage_url, auth_headers,
):
    """On a freshly built cluster (and at every point this suite
    is not itself mid-flip) both identities read `assisted`, because the
    contract ships refusing by design. Precondition
    checked live against flagd's own OFREP resolution
    (`contract.assert_assisted`), not merely against the object's spec, for
    the same reason `test_policy_denial.py`/`test_supervised.py`'s
    `denial_run` fixtures call it first: a leaked flip from a crashed earlier
    run in this same suite must fail loudly here, immediately, rather than
    surface as a confusing mismatch three assertions later.

    On the shipped contract BOTH targeting branches
    are `assisted` (a deliberate no-op), so `level` alone cannot fail --
    every path through `resolveAutonomyLevel` (`targeting-then`,
    `targeting-else`, `default-variant`, `flag-disabled`) returns the same
    string here. A reader that ignores `spiffeId` entirely, hardcodes
    `'assisted'`, resolves both identities through the same branch, or
    regresses the settlement-agent SPIFFE literal would all pass unchanged. `branch` is
    already in the response for free -- asserting it too means a caller that
    is not actually reaching the targeting-else arm cannot pass as one that
    is.
    """
    contract.assert_assisted("test_the_card_reads_assisted_for_the_identity")

    resp = requests.get(
        f"{backstage_url}/api/autonomy-contract/status", headers=auth_headers, timeout=10
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["state"] == "ENABLED", body
    per_identity = body["perIdentity"]
    expected_branch = {
        "settlement-agent": "targeting-then",
    }
    assert set(per_identity) == set(expected_branch), (
        f"the card names {sorted(per_identity)}; since M8 the dial governs "
        "settlement-agent alone (the senior was retired)"
    )
    for label, branch in expected_branch.items():
        assert label in per_identity, f"{label!r} missing from perIdentity: {per_identity}"
        assert per_identity[label]["level"] == "assisted", (
            f"{label} reads {per_identity[label]} on a contract "
            "`contract.assert_assisted` just confirmed is refusing"
        )
        assert per_identity[label]["branch"] == branch, (
            f"{label} resolved via branch {per_identity[label].get('branch')!r}, "
            f"expected {branch!r} -- both branches happen to read 'assisted' "
            "today, so `level` alone cannot tell a correct per-identity "
            "resolution from one that ignores spiffeId entirely"
        )


# --- 5. No redeploy: a card-driven flip, alone, moves nothing else ----------


def test_no_workload_is_redeployed_by_a_card_driven_flip(backstage_url, auth_headers):
    """The cheap half of this file's claim -- no model call, no browser --
    proving the card's write, alone, restarts nothing in `northstar-agents`.
    Same mechanism `test_progressive_autonomy.py::
    test_no_workload_was_redeployed_by_the_flip` uses (pod UID + image digest,
    imported rather than reimplemented), driven through Backstage's HTTP route
    instead of `kubectl patch`.

    This also performs one full flip-and-revert of the live contract through
    the card, independently of whether `RUN_BROWSER_TESTS=1` is set, and
    checks the write landed with `contract.get_committed_then_branch()` --
    a synchronous, independent `kubectl get` of the object's own spec --
    rather than trusting the HTTP response alone.
    """
    contract.assert_assisted("test_no_workload_is_redeployed_by_a_card_driven_flip")
    before = _pod_uids_and_image_digests(AGENTS_NAMESPACE)
    try:
        resp = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "autonomous"},
            timeout=15,
        )
        assert resp.status_code == 200, (
            f"POST /api/autonomy-contract/variant (autonomous) returned "
            f"{resp.status_code}: {resp.text[:300]}"
        )
        landed = contract.get_committed_then_branch()
        assert landed == contract.AUTONOMOUS, (
            f"the card's write reported success but the FeatureFlag object's "
            f"own targeting.if[1] still reads {landed!r}, not "
            f"{contract.AUTONOMOUS!r} -- the write did not reach the resource"
        )

        after = _pod_uids_and_image_digests(AGENTS_NAMESPACE)
        assert before == after, (
            "something in northstar-agents restarted when only the contract "
            f"changed: before={before!r} after={after!r}"
        )

        # This test already holds the contract
        # flipped, at no extra cost -- the discriminating check that
        # `test_the_card_reads_assisted_for_the_identity...` cannot make on
        # the shipped (both-assisted) contract. With the then-branch flipped,
        # the settlement-agent identity must read the NEW level on
        # `targeting-then`.
        resp = requests.get(
            f"{backstage_url}/api/autonomy-contract/status",
            headers=auth_headers,
            timeout=10,
        )
        assert resp.status_code == 200, resp.text[:300]
        per_identity = resp.json()["perIdentity"]
        assert per_identity["settlement-agent"] == {
            "level": "autonomous",
            "branch": "targeting-then",
        }, (
            f"settlement-agent reads {per_identity['settlement-agent']} while "
            "the contract is flipped to autonomous -- expected the "
            "targeting-then branch to carry the new level"
        )
    finally:
        resp = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "assisted"},
            timeout=15,
        )
        assert resp.status_code == 200, (
            f"failed to revert the contract to assisted through the card: "
            f"HTTP {resp.status_code}: {resp.text[:300]}"
        )
        contract.assert_assisted("test_no_workload_is_redeployed_by_a_card_driven_flip (revert)")


# --- 6. This file's central claim: the card's write changes a real ---------
# --- settlement decision, live, with no redeploy -----------------------------


@browser_test
def test_driving_the_cards_write_over_http_flips_a_refused_settlement_to_executed(
    backstage_url, auth_headers, claims_agent_route,
):
    """Driven through the surface an audience actually
    uses. Submit the identical over-the-assisted-limit claim twice, under the
    junior identity, with nothing between the two runs but a card-driven flip
    of the live contract -- the same claim
    `test_progressive_autonomy.py::test_the_same_claim_refuses_then_settles_...`
    already proves for a `kubectl patch`, proven again here for the card's own
    HTTP route.

    **This is the test that was mutated to prove it can fail**: with
    `router.ts`'s `POST /variant` handler changed to skip the actual
    `client.patchThenBranch(...)` call while still returning 200, the
    `landed == "autonomous"` assertion below reddens immediately --
    `assert 'assisted' == 'autonomous'` -- before a second settlement is even
    driven, which is a faster and just as conclusive RED as
    `assert 'refused' == 'executed'` would have been had this file reached
    that assertion first.
    """
    contract.assert_assisted("test_driving_the_cards_write_over_http_...")
    try:
        refused = _drive_browser(f"CLM-GOLDEN-{uuid.uuid4().hex[:8]}", AMOUNT)
        assert refused.reply.get("status") == "awaiting_approval", refused.reply
        refused_decisions = _decisions(refused.trace, EXECUTE_TOOL)
        assert len(refused_decisions) == 1, (
            f"expected exactly one northstar.policy.decision span for {EXECUTE_TOOL}, "
            f"got {len(refused_decisions)}"
        )
        refused_decision = refused_decisions[0]
        assert refused_decision.attrs.get(ATTR_DECISION) == "deny", refused_decision.attrs
        assert (
            refused_decision.attrs.get(ATTR_POLICY_RULE)
            == "settlement-agent.assisted.execute_limit"
        ), refused_decision.attrs
        assert refused_decision.attrs.get(ATTR_POLICY_LIMIT) == 0, refused_decision.attrs
        assert refused_decision.attrs.get(ATTR_POLICY_AMOUNT) == AMOUNT, refused_decision.attrs

        flip = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "autonomous"},
            timeout=15,
        )
        assert flip.status_code == 200, (
            f"POST /api/autonomy-contract/variant (autonomous) returned "
            f"{flip.status_code}: {flip.text[:300]}"
        )
        # Independent witness that the write reached the object -- not merely
        # that the HTTP response claimed success. This is the assertion the
        # mutation described above reddens.
        landed = contract.get_committed_then_branch()
        assert landed == contract.AUTONOMOUS, (
            f"the card reported a successful flip to 'autonomous' but the "
            f"live FeatureFlag object's targeting.if[1] still reads "
            f"{landed!r} -- the write did not reach the resource, which "
            "means the settlement below would refuse again and this test "
            "would prove nothing about the card"
        )

        settled = _drive_browser(f"CLM-GOLDEN-{uuid.uuid4().hex[:8]}", AMOUNT)
        assert settled.reply.get("status") == "executed", settled.reply
        settled_decisions = _decisions(settled.trace, EXECUTE_TOOL)
        assert len(settled_decisions) == 1, (
            f"expected exactly one northstar.policy.decision span for {EXECUTE_TOOL}, "
            f"got {len(settled_decisions)}"
        )
        settled_decision = settled_decisions[0]
        assert settled_decision.attrs.get(ATTR_DECISION) == "permit", settled_decision.attrs
        assert (
            settled_decision.attrs.get(ATTR_POLICY_RULE)
            == "settlement-agent.autonomous.execute_limit"
        ), settled_decision.attrs
        assert settled_decision.attrs.get(ATTR_POLICY_LIMIT) == 6000, (
            settled_decision.attrs
        )
        assert settled_decision.attrs.get(ATTR_POLICY_AMOUNT) == AMOUNT, settled_decision.attrs

        assert "reviewer-ui" not in settled.trace.services(), (
            "a reviewer-ui span appeared in a settlement this test never "
            f"drove through reviewer-ui: {settled.trace.services()!r} -- a "
            "human was involved; that is not autonomy"
        )

        payment_id = settled.reply.get("paymentId")
        assert payment_id, f"no paymentId in the executed reply: {settled.reply}"
        record = _get_payment(payment_id)
        assert record.get("status") == "executed", record
        assert record.get("amount") == AMOUNT, record
    finally:
        # Leave the contract refusing whether the assertions above
        # passed or raised, through the SAME route this test claims to prove
        # -- verified, not merely attempted.
        revert = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "assisted"},
            timeout=15,
        )
        assert revert.status_code == 200, (
            f"failed to revert the contract to assisted through the card: "
            f"HTTP {revert.status_code}: {revert.text[:300]}"
        )
        contract.assert_assisted("test_driving_the_cards_write_over_http_... (revert)")


# --- 7. The durable audit signal actually lands -----------------------------

# Reused rather than retyped from telemetry.ts, the same way this file already
# reuses contract.py's constants for the flag/namespace instead of restating
# them: the module docstring names three signals this system emits for a
# contract change, and says plainly that only this one -- the OTLP span --
# is durable. Reproduced as a
# literal because this is a Python file reading a TypeScript module's
# behaviour, not importing it.
SPAN_NAME = "northstar.autonomy_contract.change"
BACKSTAGE_SERVICE = "backstage"
ATTR_NEW_VARIANT = "northstar.contract.new_variant"
ATTR_PREVIOUS_VARIANT = "northstar.contract.previous_variant"
ATTR_FIELD_MANAGER = "northstar.contract.field_manager"
ATTR_ACTOR = "northstar.contract.actor"
FIELD_MANAGER = "backstage-autonomy-card"  # k8sClient.ts's own FIELD_MANAGER
OPENSEARCH_SETTLE_TIMEOUT_S = 90.0


def _opensearch_contract_change_spans(new_variant: str, not_before: str) -> list[dict]:
    """Every indexed `northstar.autonomy_contract.change` document for
    `backstage` with this exact `new_variant`, started at or after
    `not_before` (an ISO-8601 UTC string) -- polled rather than read once,
    for the same reason `test_opensearch_trace_ingest.py`'s own poll exists:
    OpenSearch is near-real-time and the collector batches on a 1s timeout.

    Filtered by `new_variant` and a time floor rather than by trace id,
    because this span carries none the caller can know in advance: it is
    hand-built in `telemetry.ts` with a fresh random trace/span id
    (`buildOtlpTracePayload`'s `hex(16)`/`hex(8)`), never linked to any
    inbound request, and `router.ts`'s `POST /variant` response carries no
    trace id back to attach to either -- see that module's own docstring on
    why this is the ONE durable signal among the three, and the one none
    of this file's other tests assert actually reaches the collector.
    """
    deadline = time.time() + OPENSEARCH_SETTLE_TIMEOUT_S
    query = {
        "size": 10,
        "sort": [{"startTime": "desc"}],
        "query": {
            "bool": {
                "filter": [
                    {"match_phrase": {"name": SPAN_NAME}},
                    {"term": {"resource.service.name": BACKSTAGE_SERVICE}},
                    {"range": {"startTime": {"gte": not_before}}},
                ]
            }
        },
    }
    hits: list[dict] = []
    while time.time() < deadline:
        hits = _opensearch(f"/{TRACE_INDEX}/_search", query)["hits"]["hits"]
        documents = [hit["_source"] for hit in hits]
        if any(doc["attributes"].get(ATTR_NEW_VARIANT) == new_variant for doc in documents):
            return documents
        time.sleep(3)
    return [hit["_source"] for hit in hits]


def test_the_contract_change_span_lands_in_opensearch(backstage_url, auth_headers):
    """Asserts that the span lands, the
    way `test_opensearch_trace_ingest.py` already does for `northstar.policy.decision`
    -- nothing else in this suite does for this span.

    This system emits three independent signals for a contract change; two
    are already covered elsewhere and are not repeated here. The RBAC grant
    that lets the write happen at all is
    `tests/e2e/test_autonomy_writer_live_rbac.py`'s own
    subject; the k8s write itself (and, by extension, the `managedFields`
    field-manager entry the object carries afterward) is what this file's
    other tests already prove with `contract.get_committed_then_branch()`,
    though no test anywhere asserts `managedFields` directly, because it is
    a point-in-time fact about who wrote a field *last*, not a history worth
    pinning. What none of them prove is that the THIRD signal -- the only
    durable one of the three -- actually reaches the collector and is
    queryable, the same standard this suite already holds `northstar.policy.decision` to.
    """
    if not _opensearch_is_installed():
        pytest.skip(
            f"needs stage 4: svc/opensearch-cluster-master does not exist "
            f"in {OBSERVABILITY_NAMESPACE}. Run ./scripts/setup-kind.sh --stage 4."
        )

    contract.assert_assisted("test_the_contract_change_span_lands_in_opensearch")
    not_before = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    try:
        flip = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "autonomous"},
            timeout=15,
        )
        assert flip.status_code == 200, (
            f"POST /api/autonomy-contract/variant (autonomous) returned "
            f"{flip.status_code}: {flip.text[:300]}"
        )
        landed = contract.get_committed_then_branch()
        assert landed == contract.AUTONOMOUS, (
            f"the card reported success but targeting.if[1] still reads "
            f"{landed!r} -- the k8s write did not reach the object, so a "
            "span search below would prove nothing about this write"
        )

        documents = _opensearch_contract_change_spans("autonomous", not_before)
        matching = [d for d in documents if d["attributes"].get(ATTR_NEW_VARIANT) == "autonomous"]
        assert matching, (
            f"the card's write to the FeatureFlag object landed (confirmed "
            f"live against the object itself), but no {SPAN_NAME!r} span "
            f"naming new_variant='autonomous' appeared in OpenSearch's "
            f"{TRACE_INDEX!r} within {OPENSEARCH_SETTLE_TIMEOUT_S:.0f}s -- the "
            "write is real but the durable audit signal this span is meant "
            f"to provide is not reaching the collector. Documents found for this window: "
            f"{documents!r}"
        )
        span = matching[0]
        assert span["resource"]["service.name"] == BACKSTAGE_SERVICE, span
        attrs = span["attributes"]
        assert attrs.get(ATTR_PREVIOUS_VARIANT) == "assisted", attrs
        assert attrs.get(ATTR_NEW_VARIANT) == "autonomous", attrs
        assert attrs.get(ATTR_FIELD_MANAGER) == FIELD_MANAGER, attrs
        assert attrs.get(ATTR_ACTOR) == FIELD_MANAGER, attrs
        assert attrs.get("k8s.namespace.name") == PLATFORM_NAMESPACE, attrs
        assert attrs.get("k8s.resource.name") == "northstar-autonomy", attrs
    finally:
        revert = requests.post(
            f"{backstage_url}/api/autonomy-contract/variant",
            headers=auth_headers,
            json={"variant": "assisted"},
            timeout=15,
        )
        assert revert.status_code == 200, (
            f"failed to revert the contract to assisted through the card: "
            f"HTTP {revert.status_code}: {revert.text[:300]}"
        )
        contract.assert_assisted("test_the_contract_change_span_lands_in_opensearch (revert)")
