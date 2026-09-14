# M5 "Progressive autonomy" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the autonomy level a contract that lives in a Kubernetes resource
rather than a constant compiled into the Policy Decision Service — so the €6,200
claim M3 refuses settles unattended when that resource changes, and refuses again
when it changes back, with no agent redeployed and no prompt edited.

**Architecture:** The Open Feature Operator injects a flagd sidecar into the
Policy Decision Service; the PDS resolves the autonomy level per decision, keyed
on the caller's SPIFFE ID, and passes it into the rule table that has been keyed
by it since M2.2. `pds/rules.py`'s policy is otherwise untouched. Each evaluation
is recorded twice — as the span event the OpenFeature hook emits natively, and as
the semantic-convention log event the PDS emits itself — because the two upstream
specifications disagree, and the difference between the two records is the
milestone's upstream finding.

**Tech Stack:** Open Feature Operator v0.9.3, flagd v0.16.3, cert-manager
v1.21.2, `openfeature-sdk` 0.10.0, `openfeature-provider-flagd` 0.5.2,
`openfeature-hooks-opentelemetry` 0.3.1, OpenTelemetry Collector
(`opensearchexporter`, logs at Alpha), OpenSearch, Python 3.13, kind.

**Spec:** `docs/superpowers/specs/2026-09-14-northstar-m5-progressive-autonomy-design.md`

## Global Constraints

- **No unmeasured claims.** Every number, version, attribute key, metric name and
  default written into code, comments, docs or assertions must trace to a live
  measurement or to read source. If you find yourself typing a figure a second
  time, derive it instead.
- **Derive, do not retype.** A count, file list or name set published in prose
  must be computed from one checked place and asserted against it. Prefer
  deleting a stale-prose site to correcting it. This was M4's most transferable
  lesson: every structural fix held, every retyped figure rotted, and three were
  falsified by the commit that wrote them.
- **100% telemetry, never sampled.** No head sampling, no tail sampling, no
  probabilistic processor, and no library default that drops spans, logs or
  events. When adding a telemetry destination, assert **count parity** against an
  existing one; "no errors in the log" is not a measurement unless you read the
  whole window.
- **Mutate every guard.** A guard is not trusted until it has been mutated and
  watched go red *for the reason it names*. Use `./scripts/mutate-guard.sh`,
  never a hand-rolled edit/run/revert loop — it sets `PYTHONDONTWRITEBYTECODE=1`
  and purges `__pycache__`, without which a stale `.pyc` yields both false GREENs
  and false REDs. **Disbelieve a GREEN before you believe it.**
- **Fail closed.** Anything that cannot be resolved, verified or parsed denies.
  A telemetry outage, a missing resource or an unparseable value must never widen
  what is permitted.
- **Never install into an existing service venv.** Their contents are pinned and
  asserted. A new dependency means a new pin, asserted in that service's
  `test_pins.py`.
- **Pin the latest released version, verified upstream.** Check the GitHub
  releases API or PyPI, not a local Helm index, which only reflects the last
  `helm repo update`. Note that flagd tags **per component** — `flagd/v0.16.3`,
  `flagd-proxy/v0.9.9` and `core/v0.17.0` are independent series, the same trap
  `spiffe/helm-charts-hardened` set in M2.
- **Never run `scripts/teardown.sh`. Never touch the `kind-registry` container.**
  Recreating the cluster requires explicit permission, each time.
- **Nothing is filed upstream.** `docs/upstream-findings.md` stays local.
- **`.env` and credentials never appear in committed content.** Report secrets as
  `SET`/`EMPTY` only; no `set -x` near secret handling.

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `deploy/platform/openfeature/featureflag.yaml` | The contract. A `FeatureFlag` CR holding autonomy profiles keyed by SPIFFE ID. |
| `deploy/platform/openfeature/featureflagsource.yaml` | A `FeatureFlagSource` CR telling OFO which flags to inject and how. |
| `platform/policy-decision-service/pds/autonomy.py` | Flag resolution, isolated from gRPC and from the rule engine. Owns the OpenFeature client, the fail-closed default and the evaluation record. |
| `platform/eval-scorer/scorer/contract.py` | The audit's independent resolution of the same flag, plus the ruleset-version comparison that decides whether a score may be emitted at all. |
| `tests/unit/test_autonomy_contract.py` | Hermetic guards over the contract resource and the deprecated-spelling rule. |
| `tests/e2e/test_progressive_autonomy.py` | The anti-vacuity control: the same claim refuses at `assisted` and settles at `autonomous`. |
| `tests/e2e/test_flag_evaluation_telemetry.py` | Both evaluation records land, with count parity between collector and OpenSearch. |

**Modified:**

| Path | Change |
|---|---|
| `platform/policy-decision-service/pds/server.py:51` | `AUTONOMY_LEVEL` constant becomes a per-decision resolution. |
| `platform/policy-decision-service/pds/rules.py:201,208` | The `rule` literal stops mislabelling the `autonomous` row. |
| `platform/policy-decision-service/pds/telemetry.py` | Attribute constants for the evaluation record. |
| `platform/eval-scorer/scorer/scores.py` | `score_identity_had_authority` consults `contract.py` before `authority.py`. |
| `platform/eval-scorer/tests/test_authority_matches_the_policy.py` | AST check extended to the flag source. |
| `deploy/observability/collector-config.yaml` | `logs` pipeline gains an OpenSearch exporter. |
| `deploy/k8s/policy-decision-service.yaml` | OFO injection annotations. |
| `scripts/setup-kind.sh` | Stage 5: cert-manager, OFO, flagd, the CRs. |
| `scripts/smoke-test.sh` | Stage floor raised to 5; zero-skip check anchored on pytest's summary. |
| `scripts/start-demo.sh` | Foreign-forward acceptance; EXIT-trap fix. |
| `docs/upstream-findings.md` | Findings 13 and 14. |

---

## Task 1: The carried batch, and the pins

Six behaviour-neutral fixes plus three version bumps, batched because **every one
of them lands inside the gate-row staleness guard's `gate_paths`**, so each alone
would re-label the published gate row `PREDICTED, NOT MEASURED` and demand a
fresh gate run. One run covers all nine. This is M4 §9's explicit schedule.

**Files:**
- Modify: `tests/unit/test_telemetry_contract_counts.py` (V2, V13)
- Modify: `scripts/smoke-test.sh` (V5), `tests/unit/test_smoke_test_gate.py` (V5)
- Modify: `tests/unit/test_kube_helper.py` (V9)
- Modify: `scripts/start-demo.sh` (V11)
- Modify: `deploy/observability/collector-config.yaml` (V12)
- Modify: `README.md`, `docs/superpowers/specs/2026-09-11-northstar-m4-observable-design.md` (citation fix)
- Modify: every `requirements.txt` pinning `pytest`, every `test_pins.py` asserting it, every file naming the collector image or the `spire` chart version

**Interfaces:**
- Consumes: nothing from earlier tasks — this is the first.
- Produces: a clean tree at the pinned versions. Later tasks assume `pytest`
  9.1.1, collector `0.160.0` and `spire` chart `0.30.2` are already in place.

- [ ] **Step 1: Enumerate the pin sites rather than trusting this document**

Counts are deliberately **not** written here. Derive them:

```bash
cd /Users/kaspernissen/kaspernissen/aip-demo-m1.1
grep -rln "pytest==9\.0\.3" --include=requirements.txt .
grep -rln "9\.0\.3" --include=test_pins.py .
grep -rln "0\.158\.0" .
grep -rln "0\.30\.1" .
```

Record each list in the task report. The bumps are `pytest` 9.0.3 → **9.1.1**,
`otel/opentelemetry-collector-contrib` 0.158.0 → **0.160.0**, `spiffe/spire`
chart 0.30.1 → **0.30.2**. `spire-crds` 0.6.1 is already latest and is **not**
debt.

- [ ] **Step 2: Do not edit the captured collector output**

`docs/telemetry-contract.md` contains a **record of a run** that names
`0.158.0`. It must not be rewritten to a version that run did not use. Confirm
before editing:

```bash
grep -n "0\.158\.0" docs/telemetry-contract.md
```

Any hit inside a captured-output block is left alone. Note it in the report.

- [ ] **Step 3: V2 — widen the staleness guard's `gate_paths`, and delete the false comment**

In `tests/unit/test_telemetry_contract_counts.py`, the guard's path list omits
`systems` and `tools`, and the comment above it asserts they "are not executed by
the gate". That is false — `scripts/smoke-test.sh` runs
`(cd "$ROOT/systems/payments-api" && go test ./...)` and
`(cd "$ROOT/tools/payments-mcp" && pytest tests -q)`. Add both, delete the
comment, and replace it with one naming the two gate lines that prove it.

- [ ] **Step 4: Prove V2 on a real commit, both directions**

```bash
git diff --name-only 716d70c^..716d70c -- systems tools | wc -l   # expect 1
```

`716d70c` changed `tools/payments-mcp/bootstrap.py`. The narrow list reports 0
for that commit; the widened list reports 1. Record both numbers.

- [ ] **Step 5: V5 — anchor the zero-skip check on pytest's own summary**

`scripts/smoke-test.sh`'s `grep -cE "^[^ ]+::[^ ]+ SKIPPED"` misses a
collection-time module skip — which prints no `path::test` line at all, the
largest possible instance of what the check exists for — and any parametrised id
containing a space. One such id already exists in this tree
(`test_no_stray_core_networkpolicy_shadows_the_cilium_one[kind: NetworkPolicy]`).
Anchor on pytest's summary line instead:

```bash
SKIPPED=$(printf '%s' "$PYTEST_OUTPUT" | sed -n 's/.*=\{2,\}.*[^0-9]\([0-9][0-9]*\) skipped.*/\1/p' | tail -1)
SKIPPED=${SKIPPED:-0}
```

The old comment's objection — that a bare `grep SKIPPED` matches the word inside
a test name — does not apply to the summary line. Then re-point
`tests/unit/test_smoke_test_gate.py` at the **property** (that a module-level
skip is counted) rather than at the literal regex it currently pins.

- [ ] **Step 6: Verify V5 empirically, not by reading**

Create a scratch file outside the repo with one module-level skip and one
parametrised id containing a space, run it, and confirm the new expression
reports the same number pytest's summary does. Delete the scratch file. Record
both numbers.

- [ ] **Step 7: V9 — give the `absent_or_fail` AST walk a positive control**

`tests/unit/test_kube_helper.py`'s walk would pass green forever if
`_reads_returncode` stopped matching. Add a test asserting the walk **finds** a
known-offending synthetic source:

```python
def test_the_walk_detects_a_known_offender():
    """Vacuity control: the walk must fail a source it is supposed to catch."""
    offending = textwrap.dedent("""
        def probe():
            result = run(["kubectl", "get", "thing"])
            return result.returncode == 0
    """)
    assert _reads_returncode(ast.parse(offending)), (
        "the walk found nothing in a source written to offend it — "
        "it is guarding nothing"
    )
```

Also correct the `_RAW_RETURNCODE_SKIPS_ALLOWED` claim: the list holds **11**
entries against a report claiming 8 "with a reason each" — verify the live count
with `len()` and publish it once, derived.

- [ ] **Step 8: V11 — accept a healthy foreign forward, and stop tearing down your own**

`scripts/start-demo.sh` refuses to start when any of its ports is held, and
under `set -e` the `exit 1` fires the EXIT trap, which kills the forwards it had
already created — so an operator with one stray forward ends with fewer than
they started with. Refusing on an *unverified* holder is correct (ruling R20: a
dead `kubectl port-forward` keeps its socket bound, so "something is bound" is
not evidence). The fix is to reach for the check the script already has:
`verify_existing_surface` (`lsof` **plus** a real HTTP request), which
reviewer-ui already uses. Apply it to a foreign holder of any forwarded port;
accept it if it answers; and make the refusal path not fire the teardown trap.

Also at `:128`: the comment says "the **three** stage-gated surfaces" and there
are four `service_exists` gates — derive the number or delete it.

- [ ] **Step 9: V13 and V12 — one line each**

`test_a_predicted_gate_row_is_labelled_as_one`'s docstring says it "compares
that to `HEAD`"; the code and its inline comment say the opposite, and explain
why comparing `HEAD` would have been satisfied by the fix wave itself. The
docstring describes the defect, not the fix, on the merge's load-bearing guard.
Correct it to say **working tree**.

In `deploy/observability/collector-config.yaml`, repair the duplicated clause and
verbless fragment: the comment reads "…ATTR_DECISION and ATTR_RETRYABLE in
…/pds/telemetry.py, **as ATTR_DECISION and ATTR_RETRYABLE** — cited by NAME… **In
the comment explaining the milestone's central mechanism.** OpenSearch expands
dotted keys…". The facts are right; the prose broke while being corrected.

- [ ] **Step 10: Correct the memory-budget citation**

`README.md` and the M4 spec both cite "M1 spec §12" for the ~8 GB stage budget.
§12 is *Acceptance criteria*. The table is in **§9 "Cluster and deployment"**,
row `| 5 | flagd | ~8 GB |`. Verify, then fix both sites:

```bash
grep -n "^## " docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md | sed -n '9p;12p'
grep -rn "M1 spec §12" README.md docs/
```

- [ ] **Step 11: Apply the three version bumps across every site from Step 1**

Edit each file the Step 1 lists named. Do **not** install into any existing
service venv — the bumps take effect on the next `uv venv` / image build.

- [ ] **Step 12: Run the hermetic suite**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: all pass. The count is **not** written here; record what it reports.

- [ ] **Step 13: Mutate the two guards this task changed**

```bash
./scripts/mutate-guard.sh tests/unit/test_telemetry_contract_counts.py \
  '"systems", "tools"' '"systems"' \
  .venv/bin/python -m pytest tests/unit/test_telemetry_contract_counts.py -q
```

Expected: RED, naming the widened path set. Repeat for the V5 property test.
**Disbelieve a GREEN**: if either passes, the mutation did not land — check that
the anchor string was unique, since `mutate-guard.sh` replaces only the first
match.

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "fix(m5): close M4's carried batch and take the three deferred pins"
```

---

## Task 2: Stage 5 — cert-manager, the operator, flagd, and the contract

**Files:**
- Create: `deploy/platform/openfeature/featureflag.yaml`
- Create: `deploy/platform/openfeature/featureflagsource.yaml`
- Modify: `scripts/setup-kind.sh`, `deploy/k8s/policy-decision-service.yaml`
- Modify: `scripts/smoke-test.sh` (stage floor), `tests/unit/test_stage2_cilium_config.py` (`CURRENT_STAGE`)
- Create: `tests/unit/test_autonomy_contract.py`

**Interfaces:**
- Consumes: a clean tree at Task 1's pins.
- Produces: a flagd sidecar answering on **localhost:8013** inside the
  `policy-decision-service` pod, serving a flag named **`autonomy-level`** whose
  variants are `assisted` and `autonomous`, targeted by the evaluation-context
  key **`spiffeId`**. Task 3 resolves against exactly these names.

- [ ] **Step 1: Verify the pins against upstream, and record the check**

```bash
curl -s https://api.github.com/repos/open-feature/open-feature-operator/releases/latest | grep tag_name
curl -s https://api.github.com/repos/cert-manager/cert-manager/releases/latest | grep tag_name
curl -s "https://api.github.com/repos/open-feature/flagd/releases?per_page=20" | grep tag_name | head -4
```

Expected at plan time, 2026-09-14: OFO **v0.9.3**, cert-manager **v1.21.2**,
flagd **v0.16.3**. If any has moved, take the newer one and record both the old
and new in the report. **Do not** copy the version from OFO's own installation
page — it pins cert-manager `v1.14.3`, which is seven minor releases stale, and
is exactly the "read the docs instead of the releases" trap this repo has a
standing rule against.

- [ ] **Step 2: Write the contract resource**

`deploy/platform/openfeature/featureflag.yaml`. The **`autonomous` variant is
not the default** — the default is the restrictive level, so a mis-applied or
truncated resource denies:

```yaml
apiVersion: core.openfeature.dev/v1beta1
kind: FeatureFlag
metadata:
  name: northstar-autonomy
  namespace: northstar-platform
spec:
  flagSpec:
    flags:
      autonomy-level:
        state: ENABLED
        variants:
          assisted: "assisted"
          autonomous: "autonomous"
        defaultVariant: assisted
        targeting:
          if:
            - in:
                - var: spiffeId
                - - "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
            - "assisted"
            - "assisted"
```

Both targeting branches read `assisted` deliberately: the resource ships in the
**refusing** state, and the demo's whole point is changing it. Task 6 flips the
first branch to `autonomous`.

- [ ] **Step 3: Write the source resource and annotate the PDS**

`deploy/platform/openfeature/featureflagsource.yaml`:

```yaml
apiVersion: core.openfeature.dev/v1beta1
kind: FeatureFlagSource
metadata:
  name: northstar-autonomy-source
  namespace: northstar-platform
spec:
  sources:
    - source: northstar-autonomy
      provider: kubernetes
  port: 8013
```

Then add to `deploy/k8s/policy-decision-service.yaml`'s **pod template**
annotations:

```yaml
openfeature.dev/enabled: "true"
openfeature.dev/featureflagsource: "northstar-autonomy-source"
```

- [ ] **Step 4: Add stage 5 to `setup-kind.sh`**

Follow the existing stage-gated block pattern exactly — the same
`BEGIN/END stage-N-only` marker convention the collector config uses, because
`setup-kind.sh` strips blocks below their stage with a line-range `sed` and
cannot edit inside a flow sequence. Install in order: cert-manager, wait for its
deployments Available, then OFO, then the two CRs.

```bash
kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/cert-manager.yaml"
kubectl wait --for=condition=Available=True deploy --all -n cert-manager --timeout=300s
helm repo add openfeature https://open-feature.github.io/open-feature-operator/
helm repo update
helm upgrade --install openfeature openfeature/open-feature-operator --version v0.9.3 --wait
kubectl apply -f deploy/platform/openfeature/
```

- [ ] **Step 5: Raise the stage floor everywhere it is written**

This defect class has recurred **three times** — the README said `--stage 2` for
all of M3, and `--stage 3` through M4 Task 1. Raise `CURRENT_STAGE` to **5** in
`tests/unit/test_stage2_cilium_config.py::test_no_operator_facing_instruction_still_points_at_an_older_stage`,
then fix every site it reddens. Let the test find them; do not grep by hand and
assume you got them all.

Run: `.venv/bin/python -m pytest tests/unit/test_stage2_cilium_config.py -q`
Expected: RED, listing each stale instruction. Fix until green.

- [ ] **Step 6: Bring stage 5 up and verify the sidecar is actually serving**

```bash
./scripts/setup-kind.sh --stage 5
kubectl -n northstar-platform get pod -l app=policy-decision-service \
  -o jsonpath='{.items[0].spec.containers[*].name}'
```

Expected: the PDS container **and** a flagd sidecar. Then prove it answers,
behaviourally — not by reading rollout status:

```bash
kubectl -n northstar-platform exec deploy/policy-decision-service -c flagd -- \
  wget -qO- localhost:8014/healthz
```

- [ ] **Step 7: Write the hermetic contract guards**

`tests/unit/test_autonomy_contract.py`. These parse the YAML; they need no
cluster:

```python
def test_the_contract_defaults_to_the_restrictive_variant():
    """A truncated or mis-applied contract must deny, not permit."""
    spec = _load_featureflag()["spec"]["flagSpec"]["flags"]["autonomy-level"]
    assert spec["defaultVariant"] == "assisted", (
        "defaultVariant is the fail-closed position; an 'autonomous' default "
        "means a resource that fails to parse its targeting permits everything"
    )

def test_every_variant_has_a_row_in_the_policy_table():
    """A variant the rule table does not know denies via unknown_caller — but
    silently. Assert the two stay in step."""
    variants = set(_load_featureflag()["spec"]["flagSpec"]["flags"]
                   ["autonomy-level"]["variants"])
    levels = {level for _, level in _parse_execute_limits()}
    assert variants <= levels, f"variants with no policy row: {variants - levels}"
```

`_parse_execute_limits` reads `pds/rules.py` with `ast` — no import, the same
technique `platform/eval-scorer/tests/test_authority_matches_the_policy.py`
already uses.

- [ ] **Step 8: Measure the memory footprint**

```bash
docker stats --no-stream northstar-control-plane
```

Take three readings 3s apart on the complete stage-5 cluster with every pod
Running. Record all three against the M1 spec **§9** budget of ~8 GB. Stage 4
measured 5.06 GiB; cert-manager and OFO add pods, so this is a real check, not a
formality.

- [ ] **Step 9: Mutate the contract guards**

```bash
./scripts/mutate-guard.sh deploy/platform/openfeature/featureflag.yaml \
  'defaultVariant: assisted' 'defaultVariant: autonomous' \
  .venv/bin/python -m pytest tests/unit/test_autonomy_contract.py -q
```

Expected: RED naming the fail-closed default.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(m5): stage 5 — cert-manager, the Open Feature Operator, and the contract resource"
```

---

## Task 3: The Policy Decision Service resolves the level

**Task review required** — this task is on the policy/security path.

**Files:**
- Create: `platform/policy-decision-service/pds/autonomy.py`
- Modify: `platform/policy-decision-service/pds/server.py`, `pds/rules.py`, `pds/telemetry.py`, `requirements.txt`, `tests/test_pins.py`
- Create: `platform/policy-decision-service/tests/test_autonomy.py`

**Interfaces:**
- Consumes: flagd on `localhost:8013`, flag `autonomy-level`, context key
  `spiffeId` (Task 2).
- Produces: `resolve_autonomy(caller: str | None) -> AutonomyResolution`, where
  `AutonomyResolution` is a frozen dataclass with fields `level: str`,
  `reason: str`, `variant: str | None`, `version: str | None`,
  `error_type: str | None`. Tasks 4 and 5 read exactly these field names.

- [ ] **Step 1: Write the failing fail-closed test**

`platform/policy-decision-service/tests/test_autonomy.py`:

```python
def test_an_unreachable_provider_resolves_to_the_restrictive_level():
    """A flagd outage must never widen what is permitted."""
    resolution = resolve_autonomy(
        "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
        client=_client_that_raises(ConnectionError("flagd unreachable")),
    )
    assert resolution.level == "assisted"
    assert resolution.reason == "ERROR"
    assert resolution.error_type is not None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd platform/policy-decision-service && .venv/bin/python -m pytest tests/test_autonomy.py -q`
Expected: FAIL — `pds.autonomy` does not exist.

- [ ] **Step 3: Add the three pins**

Append to `platform/policy-decision-service/requirements.txt`:

```
openfeature-sdk==0.10.0
openfeature-provider-flagd==0.5.2
openfeature-hooks-opentelemetry==0.3.1
```

Assert each in `tests/test_pins.py` alongside the existing pins. Recreate the
venv — **never** install into the existing one:

```bash
cd platform/policy-decision-service && rm -rf .venv && uv venv --python 3.13 .venv && uv pip install -r requirements.txt
```

- [ ] **Step 4: Write `pds/autonomy.py`**

```python
"""Resolving the autonomy level, and nothing else.

Deliberately free of gRPC and of the rule engine: `server.py` adapts the wire
to this, `rules.py` never learns a flag exists.
"""
from dataclasses import dataclass

FLAG_KEY = "autonomy-level"
CONTEXT_KEY = "spiffeId"

# The fail-closed position. Not a fallback in the ordinary sense: every path
# that cannot establish a level lands here, and the rule table's most
# restrictive allowance is keyed by it.
RESTRICTIVE_LEVEL = "assisted"


@dataclass(frozen=True)
class AutonomyResolution:
    level: str
    reason: str
    variant: str | None = None
    version: str | None = None
    error_type: str | None = None


def resolve_autonomy(caller: str | None, client) -> AutonomyResolution:
    try:
        details = client.get_string_details(
            FLAG_KEY, RESTRICTIVE_LEVEL,
            evaluation_context=EvaluationContext(
                targeting_key=caller or "", attributes={CONTEXT_KEY: caller or ""}
            ),
        )
    except Exception as exc:
        return AutonomyResolution(
            RESTRICTIVE_LEVEL, "ERROR", error_type=type(exc).__name__
        )

    metadata = details.flag_metadata or {}
    return AutonomyResolution(
        level=details.value,
        reason=str(details.reason or "UNKNOWN"),
        variant=details.variant,
        # The hook does NOT emit this (Finding 14) -- we read it ourselves,
        # because §7's audit discipline depends on it.
        version=metadata.get("version"),
        error_type=None,
    )
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_autonomy.py -q`
Expected: PASS.

- [ ] **Step 6: Write the unknown-variant test, which needs no new code**

```python
def test_an_unknown_variant_denies_through_the_existing_table():
    """Garbage in the contract must deny -- and does, without a new branch,
    because EXECUTE_LIMITS is keyed by (caller, level) and will simply miss."""
    decision = decide(PolicyInput(
        caller=SETTLEMENT_AGENT, tool="execute_payment",
        amount=100.0, currency="EUR", autonomy_level="banana",
    ))
    assert decision.permit is False
    assert decision.rule == "unknown_caller"
```

Run it. Expected: PASS with no production change — record that in the report,
because a fail-closed property that holds structurally is worth more than one
added defensively.

- [ ] **Step 7: Replace the constant in `server.py`**

Delete `AUTONOMY_LEVEL = "assisted"` at `:51`. Build one OpenFeature client at
module scope with `TracingHook()` registered, resolve per decision after the
caller's SPIFFE ID is extracted, and pass `resolution.level` on the
`PolicyInput`. Keep `telemetry.ATTR_AUTONOMY` on the span carrying the level
**actually used**, and add the resolution reason beside it so a reader can tell
"autonomous because the contract says so" from "assisted because we could not
ask". Update the startup log line at `:350` — it prints the constant today.

- [ ] **Step 8: Fix the `rule` literal, preserving M3's asserted string**

`rules.py:201,208` hardcode `"settlement-agent.assisted.execute_limit"`, which
mislabels the `(settlement-agent, autonomous)` row. The file's own comment
forbids composing the name wholesale, because
`(senior-settlement-agent, assisted)` must **keep** naming the junior — that
shared name is how M3 shows approval changing *who asks* rather than what is
allowed, and `tests/e2e/test_supervised.py`'s criterion 4 asserts the exact
string. Compose **only the autonomy segment**:

```python
rule = f"settlement-agent.{req.autonomy_level}.execute_limit"
```

- `(settlement-agent, assisted)` → unchanged
- `(senior-settlement-agent, assisted)` → unchanged, still names the junior
- `(settlement-agent, autonomous)` → now honest

Replace the long comment's "left as a literal" paragraph with one recording that
M5 composed the level segment and why the caller segment stays fixed.

- [ ] **Step 9: Run both suites**

Run: `.venv/bin/python -m pytest tests -q` (PDS), then from the repo root
`.venv/bin/python -m pytest tests/unit -q`
Expected: all pass. Record both counts.

- [ ] **Step 10: Mutate the fail-closed path**

```bash
./scripts/mutate-guard.sh platform/policy-decision-service/pds/autonomy.py \
  'RESTRICTIVE_LEVEL = "assisted"' 'RESTRICTIVE_LEVEL = "autonomous"' \
  platform/policy-decision-service/.venv/bin/python -m pytest platform/policy-decision-service/tests/test_autonomy.py -q
```

Expected: RED. Then mutate the rule-name composition and confirm
`test_supervised.py` criterion 4's asserted string is what catches it.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(m5): the autonomy level comes from the contract, and the rule name stops lying"
```

---

## Task 4: Two records per evaluation, and a logs pipeline that goes somewhere

**Files:**
- Modify: `platform/policy-decision-service/pds/telemetry.py`, `pds/autonomy.py`
- Modify: `deploy/observability/collector-config.yaml`
- Create: `tests/e2e/test_flag_evaluation_telemetry.py`
- Modify: `tests/unit/test_autonomy_contract.py` (deprecated-spelling guard)
- Modify: `docs/upstream-findings.md`

**Interfaces:**
- Consumes: `AutonomyResolution` from Task 3.
- Produces: `feature_flag.evaluation` records in two signals. Task 5 reads the
  log event's `feature_flag.version`.

- [ ] **Step 1: Record what the hook actually emits, measured**

The hook is already installed from Task 3. Read its source and confirm, rather
than trusting this plan:

```bash
cat platform/policy-decision-service/.venv/lib/python3.13/site-packages/openfeature/contrib/hook/opentelemetry/__init__.py
```

Measured 2026-09-14 against 0.3.1: it calls
`current_span.add_event("feature_flag.evaluation", …)` — a **span event**, per
OpenFeature Appendix D, not the OTel semconv's log record. Attribute spellings
are current (no deprecated names). On error it sets `error.type` and
**`error.message`** — Appendix D's mapping, not the registry's
`feature_flag.error.message`. And it declares `SET_ID` and `VERSION` constants
that **it never emits**, because `flag_metadata` is never read.

- [ ] **Step 2: Emit the semconv log event ourselves, with what the hook omits**

In `pds/autonomy.py`, after resolving, emit an event record carrying the full
attribute set — including the two the hook drops:

```python
_event_logger.emit(Event(
    name="feature_flag.evaluation",
    attributes={
        "feature_flag.key": FLAG_KEY,
        "feature_flag.provider.name": provider_name,
        "feature_flag.result.variant": resolution.variant,
        "feature_flag.result.reason": resolution.reason,
        "feature_flag.result.value": resolution.level,
        "feature_flag.context.id": caller or "",
        # The two the hook declares and never sends. §7's audit rests on
        # `version`, so it cannot ride the hook.
        "feature_flag.set.id": FLAG_SET_ID,
        "feature_flag.version": resolution.version,
    },
))
```

- [ ] **Step 3: Write the deprecated-spelling guard, derived not retyped**

```python
DEPRECATED_SPELLINGS = frozenset({
    "feature_flag.provider_name",
    "feature_flag.variant",
    "feature_flag.evaluation.reason",
    "feature_flag.evaluation.error.message",
})

def test_no_deprecated_feature_flag_spelling_is_emitted():
    """Four spellings were renamed; they are what most published examples
    still use. Semconv 1.44.0."""
    emitted = _emitted_attribute_keys()   # parsed from pds/autonomy.py by ast
    assert not (emitted & DEPRECATED_SPELLINGS), (
        f"deprecated spellings emitted: {sorted(emitted & DEPRECATED_SPELLINGS)}"
    )
```

- [ ] **Step 4: Point the logs pipeline at OpenSearch**

In `deploy/observability/collector-config.yaml`, give the `logs` pipeline a real
exporter — it is `exporters: [debug]` today, exactly the shape the metrics
pipeline had before M4 Task 2. Use the same `BEGIN/END stage-5-only` marker
convention, one exporter per line, because `setup-kind.sh` strips by line range
and cannot edit inside a flow sequence. Ruling M4-R8 forbids the collector
naming a backend the cluster does not have, so below stage 5 this list stays
`- debug` alone.

The `opensearchexporter` supports logs at **Alpha** — the same stability as the
traces path stage 4 already ships.

- [ ] **Step 5: Write the parity test**

```python
def test_flag_evaluation_events_reach_opensearch_without_loss():
    """M4 lost every policy.decision span to a mapping conflict and the check
    that should have caught it read `logs --tail=200` against a 17,173-line
    log. Parity against an existing backend is the measurement; 'no errors'
    is not."""
    driven = _drive_one_claim()
    collector_count = _collector_accepted_log_records(driven.window)
    opensearch_count = _opensearch_log_documents(driven.window)
    assert opensearch_count == collector_count, (
        f"collector accepted {collector_count}, OpenSearch holds "
        f"{opensearch_count} — the difference is loss, and loss looks "
        f"exactly like sampling to an audience"
    )
```

- [ ] **Step 6: Run it against the live cluster**

Run: `RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/e2e/test_flag_evaluation_telemetry.py -q`
Expected: PASS. If OpenSearch rejects the records on a mapping conflict, read
the **whole** collector log window, not a `--tail` of it.

- [ ] **Step 7: Write Findings 13 and 14**

Append to `docs/upstream-findings.md`, and update its header count — the header
carries the count a reader should trust, and this line has gone stale twice.

**Finding 13** — OpenFeature Appendix D recommends span events; the OTel semantic
conventions define a log-record event named `feature_flag.evaluation` and retired
the spans model; Appendix D acknowledges the trend and recommends span events
anyway. They also disagree on one attribute: Appendix D maps the error message to
`error.message`, the registry defines `feature_flag.error.message`. Two current
specifications that cite each other, disagreeing on both the signal and a name.

**Finding 14** — `openfeature-hooks-opentelemetry` 0.3.1 declares
`feature_flag.set.id` and `feature_flag.version` in its `EventAttributes` class
and never emits either; `FlagEvaluationDetails.flag_metadata` is never read, so
Appendix D's flag-metadata mapping is unimplemented. Consequence for anyone
auditing: `feature_flag.version` is the conventions' answer to "which ruleset
produced this decision", and the hook cannot answer it.

Neither is filed.

- [ ] **Step 8: Mutate the parity guard**

Mutate the equality to `>=` and confirm RED. **Disbelieve a GREEN** — a parity
test that cannot fail is the exact shape of the defect it exists to catch.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(m5): flag evaluations in two signals, and the logs pipeline reaches OpenSearch"
```

---

## Task 5: An audit that can still disagree

**Task review required** — this task decides whether a score may be emitted.

**Files:**
- Create: `platform/eval-scorer/scorer/contract.py`
- Modify: `platform/eval-scorer/scorer/scores.py`, `scorer/trajectory.py`
- Modify: `platform/eval-scorer/tests/test_authority_matches_the_policy.py`
- Create: `platform/eval-scorer/tests/test_contract.py`

**Interfaces:**
- Consumes: the log event's `feature_flag.version` and
  `feature_flag.result.value` (Task 4); `AutonomyResolution`'s field names
  (Task 3).
- Produces: `TrajectoryUnscorable`, raised when the ruleset version at scoring
  time differs from the one recorded at decision time.

- [ ] **Step 1: Write the failing test for the vacuity hole**

```python
def test_a_level_the_pds_invented_is_caught():
    """authority.py holds its own limits so the audit can disagree. Until now
    it took the autonomy LEVEL from the span it was judging -- so a PDS
    claiming 'autonomous' sent the audit to the 1,000,000 row and the audit
    agreed all the way down."""
    span_claims = "autonomous"
    contract_says = "assisted"
    score = score_identity_had_authority(
        _execution(caller=SETTLEMENT_AGENT, amount=6200.0, autonomy=span_claims),
        contract_level=contract_says,
    )
    assert score.value == 0.0
    assert "contract" in score.explanation.lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd platform/eval-scorer && .venv/bin/python -m pytest tests/test_contract.py -q`
Expected: FAIL — `scorer.contract` does not exist.

- [ ] **Step 3: Write `scorer/contract.py`**

Resolve the flag independently, exactly as `authority.py` holds the limits
independently — a deliberate second copy, independent at runtime, checked at
build time. Raise on the temporal problem rather than answering through it:

```python
class TrajectoryUnscorable(Exception):
    """The audit cannot establish the contract as of decision time.

    Scoring is pull-only and runs minutes later. If the contract changed in
    between, an independent resolution describes a different moment than the
    decision it judges -- and would report a disagreement that is really just
    time passing. Emitting no score beats a flattering one AND a false alarm.
    """


def contract_level_at(decision_version: str | None, *, now_version: str,
                      now_level: str) -> str:
    if decision_version is None or decision_version != now_version:
        raise TrajectoryUnscorable(
            f"contract ruleset moved: decision ran under {decision_version!r}, "
            f"scoring sees {now_version!r}"
        )
    return now_level
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q`
Expected: PASS.

- [ ] **Step 5: Extend the AST check to the flag source**

`tests/test_authority_matches_the_policy.py` parses `pds/rules.py` with `ast`
and fails if the two limit tables disagree. Extend it to parse
`deploy/platform/openfeature/featureflag.yaml` and assert every variant there
has a row in both tables. The audit's copy must not silently fall behind the
contract the way it must not fall behind the limits.

- [ ] **Step 6: Wire `scores.py`**

`score_identity_had_authority` consults `contract.py` for the level, then
`authority.py` for the limit at that level. Let `TrajectoryUnscorable` propagate
— `/score` must **emit no score** for that dimension, the discipline ruling H1
established one function away. Do not catch it and return 1.0.

- [ ] **Step 7: Close S-b while you are here**

Both truncation guards read an absent field as "nothing was dropped":
`aggregation.get("sum_other_doc_count") or 0`, and a `len(hits)` default that
makes the page check vacuously false. That is "I could not look" recorded as
"there was nothing to find" — the exact rule `search()`'s own error check
enforces ten lines away. Make both raise instead.

- [ ] **Step 8: Run the full scorer suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass. Record the count.

- [ ] **Step 9: Mutate the refusal**

```bash
./scripts/mutate-guard.sh platform/eval-scorer/scorer/contract.py \
  'raise TrajectoryUnscorable' 'return now_level  # raise TrajectoryUnscorable' \
  platform/eval-scorer/.venv/bin/python -m pytest platform/eval-scorer/tests -q
```

Expected: RED. A refusal that cannot be observed to fire is a refusal nobody has
tested.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(m5): the audit resolves the contract itself, and refuses when it cannot"
```

---

## Task 6: The demo, the evidence, and the gate

**Files:**
- Create: `tests/e2e/test_progressive_autonomy.py`
- Modify: `README.md`, `docs/architecture.md`, `docs/telemetry-contract.md`
- Modify: `deploy/observability/dashboards/evidence.ndjson`
- Modify: `scripts/start-demo.sh` (stage-5 surfaces)

**Interfaces:**
- Consumes: everything above.
- Produces: the milestone's anti-vacuity control.

- [ ] **Step 1: Write the anti-vacuity e2e**

This is the milestone. It must **fail** if the contract does nothing:

```python
@pytest.mark.skipif(not os.getenv("RUN_BROWSER_TESTS"), reason="drives a live settlement")
def test_the_same_claim_refuses_then_settles_when_only_the_contract_changes():
    """M5's whole claim in one test. If both halves pass, the flag is
    decoration -- which is what M4's policy scores taught us to prove."""
    _set_contract_variant("assisted")
    refused = _submit_claim(amount=6200.0)
    assert refused.status == "refused"
    assert refused.rule == "settlement-agent.assisted.execute_limit"

    _set_contract_variant("autonomous")
    settled = _submit_claim(amount=6200.0)
    assert settled.status == "executed"
    assert settled.rule == "settlement-agent.autonomous.execute_limit"
    assert settled.approvals == [], "a human was involved; that is not autonomy"

    _set_contract_variant("assisted")   # leave the tree in the refusing state
```

- [ ] **Step 2: Prove nothing else changed**

The claim is "we didn't change a prompt". Assert it mechanically rather than in
prose — capture image digests and pod UIDs before and after the flip:

```python
def test_no_workload_was_redeployed_by_the_flip():
    before = _pod_uids_and_image_digests("northstar-agents")
    _set_contract_variant("autonomous")
    _await_propagation()
    after = _pod_uids_and_image_digests("northstar-agents")
    assert before == after, "something restarted; the contract is not the only change"
    _set_contract_variant("assisted")
```

- [ ] **Step 3: Run both against the live cluster**

Run: `RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/e2e/test_progressive_autonomy.py -q`
Expected: PASS. Record the refused and executed amounts from the ledger.

- [ ] **Step 4: Update the evidence view**

Add the autonomy level and contract version to the evidence view's decision
panel, and assert the new query out of the shipped NDJSON the way
`tests/e2e/test_evidence_view.py` asserts every other one. A panel nothing
asserts is a panel that will rot.

- [ ] **Step 5: Update the docs, deriving every figure**

`README.md` status section, the stages table's row 5, `docs/architecture.md`'s
component tables and "What the system deliberately does not do",
`docs/telemetry-contract.md`'s per-service row. **Publish no count twice.** Where
a figure is needed, publish it in exactly one document and guard it with a live
measurement or `--collect-only`.

- [ ] **Step 6: Add stage-5 surfaces to `start-demo.sh`**

flagd's metrics port if it is worth forwarding; otherwise state that nothing new
is forwarded. Use the `verify_existing_surface` path Task 1 fixed.

- [ ] **Step 7: Run the gate — once**

```bash
RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh
```

Expected: exit 0, **zero skips**, at `--stage 5`. ~7 minutes of live LLM
settlements. This is the milestone's single gate run; fix rounds run targeted
suites only.

- [ ] **Step 8: Record the measured gate row**

Write the row into `docs/telemetry-contract.md` labelled **MEASURED**, naming the
commit it was measured at. The staleness guard — now covering `systems` and
`tools` after Task 1 — will re-label it `PREDICTED` if anything under a gate path
moves afterwards. If you must change a gate path after this step, you owe another
gate run; that is the guard working, not the guard being wrong.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(m5): the contract flips and the claim settles — stage 5, measured"
```

---

## Self-Review

**Spec coverage.** All 11 exit criteria in spec §9 map to a task: criterion 1 →
Task 2 Steps 4–6; 2 → Task 3 Step 7; 3 → Task 3 Steps 1–6, 10; 4 → Task 6 Steps
1–3; 5 → Task 4 Steps 1–3, 8; 6 → Task 4 Steps 4–6; 7 → Task 5; 8 → Task 3 Step
8; 9 → Task 4 Step 7; 10 → Task 2 Step 8; 11 → Task 6 Step 7. Spec §11's carried
debt is deliberately not implemented — that is what carried means.

**Five unverified items from spec §12, all resolved before planning** rather than
left as look-it-up steps: cert-manager **is** mandatory for OFO (and OFO's own
docs pin a stale v1.14.3 against the current v1.21.2); OFO v0.9.3, flagd v0.16.3
verified against the releases API; the hook emits a **span event** with current
spellings, `error.message` rather than `feature_flag.error.message`, and
**never** emits `set.id` or `version`; the `opensearchexporter` carries logs at
Alpha. Only the stage-5 memory footprint remains genuinely unmeasurable before
implementation, and it has its own step.

**One spec statement is superseded by measurement.** §7 assumed
`feature_flag.version` would be available from the evaluation record. The hook
does not emit it (Finding 14), so Task 3 reads `flag_metadata` directly and Task
4 emits it on the PDS's own log event. The discipline is unchanged; its plumbing
is not.

**Type consistency.** `AutonomyResolution`'s five field names (`level`, `reason`,
`variant`, `version`, `error_type`) are defined in Task 3 Step 4 and read in Task
4 Step 2 and Task 5 Step 3. `FLAG_KEY` (`autonomy-level`) and `CONTEXT_KEY`
(`spiffeId`) are fixed in Task 2 Step 2 and consumed in Task 3 Step 4.
`TrajectoryUnscorable` is defined in Task 5 Step 3 and propagates in Step 6.

**No counts are published in this plan.** Every figure is derived by a command
the step gives — deliberately, because the one thing this repo has proven about
itself is that retyped figures rot and derived ones survive.
