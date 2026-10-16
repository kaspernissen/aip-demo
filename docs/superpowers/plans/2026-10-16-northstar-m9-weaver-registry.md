# Northstar M9 — Weaver Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The platform's own telemetry (policy decisions, human review, claims, the autonomy contract, the approval-wait histogram) is defined once in an OTel Weaver registry, every owned name is prefixed `northstar.`, the live system is checked against the registry in the gate, and the demo's evidence step reads Jaeger.

**Architecture:** A semconv-format registry in `telemetry/registry/` depending on OTel semconv v1.44.0. `weaver registry check` and a generated docs region run in `scripts/check-registry.sh` (called by the gate). An in-cluster `weaver registry live-check` receives a copy of all OTLP from the collector; the gate reads its report and fails on violations about `northstar.*` names. All writers and readers are renamed in one wave.

**Tech Stack:** OTel Weaver v0.28.0 (`otel/weaver:v0.28.0`), OTel semantic conventions v1.44.0, OpenTelemetry Collector, Python services, TypeScript (customer-ui, Backstage plugin), Go (payments-api), pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-northstar-m9-weaver-registry-design.md`

## Global Constraints

- Weaver pinned to `v0.28.0`, image `otel/weaver:v0.28.0`. Semconv dependency pinned to `v1.44.0`.
- Every name this project owns starts with `northstar.`. No owned name in `policy.`, `review.`, `claim.`.
- A name is never both an attribute and a namespace.
- Durations in seconds; no units in names.
- Everything we define is `stability: development`.
- Never sample, never drop telemetry: live-check gets a **copy** via an extra exporter.
- Derive figures; never retype them (counts in docs come from a command or a generated file).
- Tests fail rather than skip.
- Commit with explicit paths. No `Co-Authored-By` or generated-by footers.
- Never `kind delete`, teardown, or touch `kind-registry`. Never edit `scripts/` while `setup-kind.sh` is running.
- `tests/unit` stays hermetic: no network, no cluster, no docker.
- Images are rebuilt and redeployed from a clean HEAD before any live verification (`./scripts/setup-kind.sh --stage 7` rebuilds).

## Review Focus

1. A permit with no limit (`unlimited_tool`, `human-approved.execute`) carries no `northstar.policy.limit.amount`: its requirement level must be `conditionally_required`, or live-check reports a violation on every proposal. (Task 1 registry + Task 3 live-check.)
2. A misspelled `northstar.*` attribute on a real span must turn the gate red; framework attributes on our own spans (`http.*`, `a2a.*`, `mcp.*`) must not. (Task 3 mutation check.)
3. Live-check unreachable, restarted or empty during the gate must fail the gate, never pass it with an empty report. (Task 3.)
4. `northstar.review.wait.duration` under one second (a fast click) is a double like `0.84`, not `0`; evidence prints it sensibly. (Task 2 unit test, Task 4.)
5. The OpenSearch evidence view and the eval-scorer keep working after the rename (longer slot): their queries use the new names. (Task 2.)

---

### Task 1: Inventory and the registry

**Files:**
- Create: `telemetry/registry/manifest.yaml`, `telemetry/registry/policy.yaml`, `review.yaml`, `claim.yaml`, `contract.yaml`, `metrics.yaml`
- Create: `telemetry/templates/markdown/` (one template rendering our attribute tables), `scripts/check-registry.sh`
- Modify: `docs/telemetry-contract.md` (a generated region between `<!-- BEGIN weaver-generated -->` and `<!-- END weaver-generated -->`)
- Test: `scripts/check-registry.sh` exit 0; `tests/unit/test_registry_layout.py` (hermetic: YAML parses, every owned id starts `northstar.`, no attribute id is a prefix-namespace of another, every enum member snake_case)

**Interfaces:**
- Produces: the registry at `telemetry/registry/`; `scripts/check-registry.sh` (no args, exit non-zero on any finding); the final rename map in the task report (old name → new name, by emitting file), which Task 2 consumes.

- [ ] **Step 1: Verify the tool before writing YAML.** Run `docker run --rm otel/weaver:v0.28.0 --version` and `docker run --rm otel/weaver:v0.28.0 registry live-check --help`. Read `schemas/semconv-syntax.md` at the `v0.28.0` tag of github.com/open-telemetry/weaver and its `docs/` for a "v2" registry format. Record in the report: whether v2 is recommended for a new registry at v0.28.0, the exact live-check flags for bind address, ports, `--output http`, and the config file name/keys for `finding_filters` / `finding_level_overrides`. If v2 is recommended, write the registry in v2; otherwise in the `groups:` format below. If the docker credential helper hangs, use a throwaway `DOCKER_CONFIG` (see memory note `docker-credential-helper-hangs`).

- [ ] **Step 2: Measure the inventory.** Every telemetry name the project emits, by file:
```bash
git grep -n -E "['\"\`](northstar|policy|review|claim|settlement)\.[a-z_.]+['\"\`]" -- agents apps platform tools systems ':!**/node_modules/**' ':!**/tests/**'
```
plus span names passed to `start_as_current_span`/`startSpan`/`tracer.Start`, metric names, event names. Cross-check against a live trace: `curl -s localhost:16686/api/traces/<id>` for one assisted claim + its approval trace (find via service `reviewer-ui`, operation `review.decision`). Map each owned name with the spec §3 table; any name not in the table gets a row ruled by spec §2 and recorded in the report. Check whether anything still emits `settlement.execute_approved`; if not, it is not registered (Task 2 deletes the scorer constant).

- [ ] **Step 3: Write the failing layout test** `tests/unit/test_registry_layout.py`:
```python
"""The registry's own shape, checked without Weaver (hermetic)."""
from pathlib import Path
import yaml

REG = Path(__file__).resolve().parents[2] / "telemetry" / "registry"

def _groups():
    for f in sorted(REG.glob("*.yaml")):
        if f.name == "manifest.yaml":
            continue
        yield from (yaml.safe_load(f.read_text()) or {}).get("groups", [])

def _owned_attribute_ids():
    for g in _groups():
        for a in g.get("attributes", []):
            if "id" in a:
                yield a["id"]

def test_manifest_pins_semconv_v1_44_0():
    m = yaml.safe_load((REG / "manifest.yaml").read_text())
    assert m["name"] == "northstar"
    deps = m["dependencies"]
    assert any("v1.44.0" in d["registry_path"] for d in deps)

def test_every_owned_attribute_is_prefixed():
    ids = list(_owned_attribute_ids())
    assert ids, "no attributes defined"
    assert all(i.startswith("northstar.") for i in ids), [i for i in ids if not i.startswith("northstar.")]

def test_no_attribute_is_also_a_namespace():
    ids = set(_owned_attribute_ids())
    clashes = sorted(a for a in ids for b in ids if b.startswith(a + "."))
    assert not clashes, clashes

def test_every_owned_definition_is_development():
    for g in _groups():
        assert g.get("stability") == "development", g["id"]
        for a in g.get("attributes", []):
            if "id" in a:
                assert a.get("stability") == "development", a["id"]
```
If Step 1 chose v2, adapt `_groups`/`_owned_attribute_ids` to v2's top-level keys; keep the four assertions.

- [ ] **Step 4: Run it, expect FAIL** (`telemetry/registry` missing): `.venv/bin/python -m pytest tests/unit/test_registry_layout.py -q`

- [ ] **Step 5: Write the registry.** `manifest.yaml`:
```yaml
name: northstar
description: Northstar's own telemetry -- the platform's policy decisions, human review, claims and autonomy contract. Everything else is OpenTelemetry semantic conventions.
schema_url: https://northstar.local/schemas/0.1.0
dependencies:
  - schema_url: https://opentelemetry.io/schemas/1.44.0
    registry_path: https://github.com/open-telemetry/semantic-conventions@v1.44.0[model]
```
`policy.yaml` (groups format; shape for the others):
```yaml
groups:
  - id: registry.northstar.policy
    type: attribute_group
    stability: development
    brief: A policy decision the platform made about one MCP tools/call.
    attributes:
      - id: northstar.policy.decision
        type:
          members:
            - {id: permit, value: permit, stability: development, brief: The call may proceed.}
            - {id: deny, value: deny, stability: development, brief: The call is refused; the rule and limit say why.}
        stability: development
        brief: What the policy-decision-service decided.
      - id: northstar.policy.rule
        type: string
        stability: development
        brief: The rule that decided, e.g. settlement-agent.assisted.execute_limit.
        examples: [settlement-agent.assisted.execute_limit, human-approved.execute]
      - id: northstar.policy.limit.amount
        type: double
        stability: development
        brief: The limit in EUR the rule applied. Absent when the rule has no limit.
      # ... retryable, caller.spiffe_id, request.amount, request.currency,
      #     autonomy.level (enum assisted|autonomous), autonomy.reason,
      #     ledger.status (enum proposed|awaiting_approval|executed|rejected)
  - id: span.northstar.policy.decision
    type: span
    span_kind: server
    stability: development
    brief: One decision, a child of the gateway's tools/call span.
    attributes:
      - {ref: northstar.policy.decision, requirement_level: required}
      - {ref: northstar.policy.rule, requirement_level: required}
      - ref: northstar.policy.limit.amount
        requirement_level: {conditionally_required: when the rule has a limit}
      - {ref: gen_ai.tool.name, requirement_level: required}
      - {ref: feature_flag.version, requirement_level: recommended}
```
Match each attribute's `type` to what the code emits today (int vs double): read the emitting line. `northstar.review.wait.duration` is `double`, seconds. Fill `review.yaml`, `claim.yaml`, `contract.yaml`, `metrics.yaml` (histogram `northstar.approval.wait.duration`, `instrument: histogram`, `unit: s`, attributes `northstar.review.decision` and `northstar.policy.autonomy.level`; read `apps/reviewer-ui/approval_metrics.py` for its current label keys and rename them to those refs).

- [ ] **Step 6: Write `scripts/check-registry.sh`:**
```bash
#!/usr/bin/env bash
# Validate the Weaver registry and check the generated docs region is current.
# Needs docker and the network (Weaver resolves the semconv dependency).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEAVER_IMAGE="otel/weaver:v0.28.0"
run_weaver() { docker run --rm -u "$(id -u):$(id -g)" -v "$ROOT:/repo" -w /repo "$WEAVER_IMAGE" "$@"; }

echo "weaver registry check"
run_weaver registry check -r telemetry/registry

echo "generated docs region is current"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
run_weaver registry generate -r telemetry/registry -t telemetry/templates markdown "/repo/.demo/weaver-docs"
python3 "$ROOT/scripts/weaver_docs_region.py" --check "$ROOT/docs/telemetry-contract.md" "$ROOT/.demo/weaver-docs"
```
and `scripts/weaver_docs_region.py` (`--check` compares, `--write` replaces the region between the BEGIN/END markers with the generated file's contents; exit 1 with a diff on mismatch). Use whatever generate syntax Step 1 recorded for v0.28.0.

- [ ] **Step 7: Run** `./scripts/check-registry.sh` until exit 0, then `python3 scripts/weaver_docs_region.py --write ...` once to fill the region, and re-run the check. Run the layout test: PASS. Mutation-check the layout test with `./scripts/mutate-guard.sh telemetry/registry/policy.yaml "id: northstar.policy.rule" "id: policy.rule" .venv/bin/python -m pytest tests/unit/test_registry_layout.py -q` → must FAIL.

- [ ] **Step 8: Commit**
```bash
git add telemetry/ scripts/check-registry.sh scripts/weaver_docs_region.py tests/unit/test_registry_layout.py docs/telemetry-contract.md
git commit -m "feat(m9): the telemetry contract as a Weaver registry, checked, with generated docs"
```

---

### Task 2: The rename wave

**Files (from Task 1's rename map; at least):**
- Modify writers: `platform/policy-decision-service/pds/telemetry.py`, `pds/server.py`, `pds/autonomy.py`; `apps/reviewer-ui/review.py`, `approval_metrics.py`, `bootstrap.py`; `agents/claims-agent/executor.py`, `correlation.py`; `agents/settlement-agent/correlation.py`, `mcp_tools.py`; `apps/customer-ui/src/app/page.tsx` (and wherever `claim.wait` is opened); `platform/backstage/packages/backend/src/plugins/autonomyContract/telemetry.ts`; `systems/payments-api/handlers.go` (only if its names change)
- Modify readers: `platform/eval-scorer/scorer/{trajectory.py,contract.py,opensearch.py,scores.py}`; `scripts/demo.py`; `tests/harness/*.py`; every test referencing an old name; `deploy/observability/opensearch/` saved objects / dashboards; `deploy/observability/collector-config.yaml` and `collector-config-dash0.yaml` (drop the `policy.decision.retryable` → `policy.decision_retryable` actions; keep the others)
- Test: every service's own suite; `tests/unit`; a new `tests/unit/test_no_legacy_names.py`

**Interfaces:**
- Consumes: Task 1's rename map and registry.
- Produces: the system emits only registry names. `review.refused.trace_id` is gone; readers find the claim trace through the span link (Jaeger reference `FOLLOWS_FROM`) or `northstar.claim.id`.

- [ ] **Step 1: Write the failing guard** `tests/unit/test_no_legacy_names.py`:
```python
"""No source file still writes an old, un-prefixed name (spec §3)."""
import re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = re.compile(r"""['"`](policy|review)\.[a-z_]+(\.[a-z_]+)*['"`]|['"`]claim\.(submit|wait)['"`]|['"`]northstar\.policy\.number['"`]""")
PATHS = ["agents", "apps", "platform", "tools", "systems", "scripts", "deploy"]

def test_no_source_writes_a_legacy_name():
    out = subprocess.run(["git", "grep", "-n", "-E", LEGACY.pattern, "--", *PATHS,
                          ":!**/node_modules/**", ":!*.md"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    assert out == "", out
```
Narrow the pattern if it matches something that is not telemetry (e.g. an HTTP route, an RBAC verb, a Kubernetes key); record each exclusion in the report with why.

- [ ] **Step 2: Run, expect FAIL** listing the current names.

- [ ] **Step 3: Rename writers**, one service at a time, running that service's own suite after each (`cd <svc> && .venv/bin/python -m pytest tests -q`; `cd systems/payments-api && go test ./...`; Backstage `yarn --cwd platform/backstage test autonomyContract` or the repo's existing invocation). `review.elapsed_ms` (int ms) becomes `northstar.review.wait.duration` as `elapsed_s = round(elapsed_ms / 1000, 3)` (double). Add a reviewer-ui unit test: a 840 ms decision records `0.84`. Remove `review.refused.trace_id`; the span link stays. In `pds/telemetry.py` delete `ATTRIBUTES` and `INVENTED_ATTRIBUTES` and the provenance guard tests that use them (`tests/test_servicer.py` "visibly ours"); keep every behaviour assertion, renamed.

- [ ] **Step 4: Rename readers.** eval-scorer (constants in `trajectory.py`; delete `settlement.execute_approved` if Task 1 found nothing emits it, and the code paths that only served it), `scripts/demo.py`, `tests/harness`, tests, OpenSearch saved objects. Where a reader used `review.refused.trace_id`, use the root span's FOLLOWS_FROM reference (Jaeger) or the claim id (OpenSearch: `northstar.claim.id`). Collector configs: drop only the two `policy.decision*` actions in `attributes/opensearch-compat`; update the comment above it.

- [ ] **Step 5: Run** `tests/unit` (PASS including `test_no_legacy_names`), every service suite, eval-scorer suite. Mutation-check the guard: `./scripts/mutate-guard.sh apps/reviewer-ui/review.py '"northstar.review.decision"' '"review.decision"' .venv/bin/python -m pytest tests/unit/test_no_legacy_names.py -q` → FAIL.

- [ ] **Step 6: Commit** per area with explicit paths, e.g.:
```bash
git add platform/policy-decision-service/ && git commit -m "refactor(m9): the decision span and its attributes are northstar.policy.*"
```
(one commit each: PDS, reviewer-ui, agents, customer-ui + Backstage, payments-api if touched, readers + collector + OpenSearch, tests).

---

### Task 3: Live-check in the cluster and in the gate

**Files:**
- Create: `deploy/observability/weaver-live-check.yaml` (Deployment + Service in `observability`), `telemetry/live-check.toml` (or the config name Task 1 recorded), `tests/harness/livecheck.py`, `tests/e2e/test_registry_live_check.py`
- Modify: `deploy/observability/collector-config.yaml`, `collector-config-dash0.yaml` (exporter `otlp_grpc/weaver` on traces, metrics and logs, inside the `stage-4-only` markers), `scripts/setup-kind.sh` (apply the manifest at stage ≥4, ConfigMap from `telemetry/registry` + `telemetry/live-check.toml`), `scripts/smoke-test.sh` (call `scripts/check-registry.sh` in Step 1; restart live-check before Step 2; read and save the report after Step 2; fail on violations), `scripts/start-demo.sh` (port-forward the admin port, e.g. local 18400)

**Interfaces:**
- Consumes: the registry (Task 1); renamed emitters (Task 2).
- Produces: `tests/harness/livecheck.py` with `restart() -> None`, `report() -> dict` (POST /stop, GET /report, POST /shutdown), `owned_violations(report: dict) -> list[dict]`.

- [ ] **Step 1: Manifest.** Deployment, 1 replica, image `otel/weaver:v0.28.0`, args per Task 1's recorded flags, e.g.:
```yaml
args: ["registry", "live-check", "-r", "/registry", "--input-source", "otlp",
       "--otlp-grpc-address", "0.0.0.0", "--otlp-grpc-port", "4317",
       "--admin-port", "4320", "--output", "http", "--format", "json"]
```
Registry and config mounted from a ConfigMap built by `setup-kind.sh` (`kubectl create configmap weaver-registry --from-file=telemetry/registry -o yaml --dry-run=client | kubectl apply -f -`). The semconv dependency is fetched from GitHub at start: the pod needs egress to github.com — check the Cilium policies for the `observability` namespace and add a scoped egress rule if needed. Readiness probe `GET /health` on 4320. `restartPolicy` default; the gate restarts with `kubectl rollout restart` and waits for Ready.

- [ ] **Step 2: Collector.** Exporter:
```yaml
  # A COPY of everything for Weaver live-check (M9). Never a sample.
  otlp_grpc/weaver:
    endpoint: weaver-live-check.observability.svc.cluster.local:4317
    tls: {insecure: true}
    sending_queue: {enabled: true}
    retry_on_failure: {enabled: true}
```
Add `- otlp_grpc/weaver` to traces, metrics and logs exporter lists inside `BEGIN/END stage-4-only` markers, in both collector configs. Verify `setup-kind.sh`'s stage stripping still produces a valid config below stage 4 (`tests/unit` has a test for the stripping; extend it to assert `otlp_grpc/weaver` is stripped).

- [ ] **Step 3: Findings policy** `telemetry/live-check.toml`: findings about names outside `northstar.*` and outside our groups are lowered to `information`; `fail_on = "violation"`. Use the exact keys Task 1 recorded.

- [ ] **Step 4: Harness and e2e test.** `tests/harness/livecheck.py` talks to `http://localhost:18400`. `tests/e2e/test_registry_live_check.py` runs **last** in the gate (name it so collection order or an explicit marker puts it last; record how): it asserts the report exists, its sample count is > 0 for spans, metrics and logs, the samples include a `northstar.policy.decision` span and a `northstar.review.decision` span, and `owned_violations(report) == []`, printing every owned violation on failure. A report with zero samples FAILS ("live-check received nothing").

- [ ] **Step 5: Gate wiring.** In `smoke-test.sh`: Step 1 runs `./scripts/check-registry.sh`; before Step 2, `kubectl -n observability rollout restart deploy/weaver-live-check && kubectl -n observability rollout status deploy/weaver-live-check --timeout=120s`, then re-establish its port-forward; after Step 2, save the report to `.demo/m9-live-check-report.json`. Unreachable live-check fails the gate.

- [ ] **Step 6: Live verification** (cluster at stage 7, rebuilt from HEAD): run one assisted claim + approve, then `.venv/bin/python -m pytest tests/e2e/test_registry_live_check.py -v`. PASS. Then mutation: deploy a reviewer-ui with `"northstar.review.decision"` misspelled as `"northstar.review.decison"` (build, load, rollout), drive one approval, run the test → FAIL naming the attribute; restore and redeploy. Record both outputs in the report.

- [ ] **Step 7: Commit** with explicit paths.

---

### Task 4: The demo reads Jaeger; docs and deck

**Files:**
- Modify: `scripts/demo.py` (`cmd_evidence`, `_claims_seen_in_opensearch` → a Jaeger-based discovery; keep `claim_ids_since(..., filed_after_ms=run_started_ms)` semantics), `tests/e2e/test_demo_flow.py`, `docs/demo-runbook.md` (§5 Step 5, the trace-A note, every attribute name), `README.md`, `docs/architecture.md`, `talk/` (Step 5 slide and notes, one new slide "the decision contract is a published schema", screenshots showing attribute names)
- Test: `tests/e2e/test_demo_flow.py` evidence assertions; `tests/unit/test_talk_deck_is_not_m7.py` style guard extended: no deck/notes file contains a legacy name

**Interfaces:**
- Consumes: renamed spans (Task 2), `tests/harness/jaeger.py`.

- [ ] **Step 1: Failing test.** In `tests/e2e/test_demo_flow.py`, the evidence assertion expects, per claim, lines `rule`, `limit`, `autonomy level`, `outcome`, `approved by ... after N.N s`, and a `trace  http://localhost:16686/trace/<32 hex>` line; and that `scripts/demo evidence` makes **no** request to OpenSearch (run it with `OPENSEARCH_URL=http://127.0.0.1:9` and assert success).

- [ ] **Step 2: Implement** `cmd_evidence` from Jaeger: find this run's decision spans via service `policy-decision-service`, operation `northstar.policy.decision`, `start` ≥ run start; group by `northstar.claim.id` (via the claim trace's spans); for each approval find service `reviewer-ui`, operation `northstar.review.decision`, tag `northstar.claim.id`, and read `northstar.review.reviewer.id`, `northstar.review.wait.duration`. Keep the bounded wait (`EVIDENCE_WAIT_S`) and its "waiting for Jaeger (...)" line. Print the Jaeger trace URL per claim (the approval trace when there is one, else the claim trace).

- [ ] **Step 3: Docs.** Runbook Step 5 SHOW/SAY/Land/If-it-fails rewritten for Jaeger and span attributes; the evidence transcript regenerated from a real run (not retyped). README "What it shows" bullet on proof: traces and span attributes, defined in a Weaver registry. Architecture: the attribute inventory points at the registry and the generated region. Deck: Step 5 slide/notes; new slide with a real excerpt of `telemetry/registry/policy.yaml` beside a Jaeger screenshot of the same span; retake screenshots that show old names. Extend the deck guard to fail on any legacy name.

- [ ] **Step 4: Run** the deck guard and `tests/unit`; run `tests/e2e/test_demo_flow.py` live (`RUN_BROWSER_TESTS=1`). PASS.

- [ ] **Step 5: Commit** with explicit paths.

---

### Task 5: Gate run (controller, after the final review)

- [ ] Rebuild every image from a clean HEAD (`./scripts/setup-kind.sh --stage 7`), `./scripts/start-demo.sh`, warm Backstage, then `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh 2>&1 | tee .demo/m9-gate-run1.log`.
- [ ] Record the MEASURED row in `docs/telemetry-contract.md`: commit, passed/failed/skipped counts, duration, and live-check's sample and violation counts read from `.demo/m9-live-check-report.json`. Commit docs only.
