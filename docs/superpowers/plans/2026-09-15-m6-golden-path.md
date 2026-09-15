# M6 "Golden path" — implementation plan

Design: `docs/superpowers/specs/2026-09-15-northstar-m6-golden-path-design.md`.
Branch `m6-golden-path`, from `e62238f` (M5 merged).

**Four tasks.** Sonnet by default; opus for Task 3's review and the whole-branch
review. Task reviews on **Task 3 only**. **One gate run, AFTER the whole-branch
review's fix wave** (M5-R38 — the lesson that cost M5 its measured row).

---

## Task 1 — Stage 6, Backstage in the cluster, and the six contradicted sites

**No task review** (covered by the whole-branch review).

**Creates:** `platform/backstage/` (the scaffolded app + `Dockerfile`),
`deploy/k8s/backstage.yaml`.
**Modifies:** `scripts/setup-kind.sh`, `scripts/rebuild-and-deploy.sh`,
`docs/architecture.md`, `docs/telemetry-contract.md`, the M1/M2/M4 specs.

1. **Scaffold the app** with `@backstage/create-app` into `platform/backstage/`.
   Pin the version. Node 22 and yarn 1.22 are present and verified.
   `app-config.yaml` uses **SQLite**, not PostgreSQL (design §3).
2. **Dockerfile + image**, built and pushed to `localhost:5001` by the existing
   `rebuild-and-deploy.sh` machinery. Read how the other seven images are done
   and match it — do not invent a second pattern.
3. **`deploy/k8s/backstage.yaml`** — Deployment + Service in `northstar-platform`.
   **Read-only ServiceAccount only in this task.** The write grant is Task 3's,
   and keeping them in separate tasks is what makes them separately reviewable
   (design §4).
4. **`setup-kind.sh --stage 6`.** Implement it. `:230` currently refuses stage 6
   by name and `:175`'s usage text says *"+ Crossplane, Argo CD, Backstage"* —
   **both are wrong now** (design §1). Stage 6 is a superset of stage 5.
   **M5-R27 is the warning here:** the cold path is the only recovery path in a
   repo with no remote, and it broke last milestone precisely because nobody ran
   it cold. Prove `--stage 6` works from cold, or state plainly that you did not.
5. **Correct the six contradicted sites** (design §1). `setup-kind.sh:175` and
   `docs/architecture.md:637,:739` are **corrected**; the **M1, M2 and M4 specs
   are annotated, not rewritten** — they are historical records and this repo
   annotates them (same convention as the M2.2 `failureMode` inversion). Also
   take the **stale cert-manager claim** in the M1 spec's M2 row, which M5's §11
   left to "M6's or a doc pass's" — M5 Task 2 actually installed cert-manager,
   so the claim "installed, never clicked" is now doubly wrong.
6. **Bump both document status lines to M6.** Design §8: the guard
   `tests/unit/test_doc_status_lines_name_the_current_milestone.py` derives the
   milestone from the newest plan file, so committing this plan makes it demand
   M6 in `docs/architecture.md:1` and `docs/telemetry-contract.md`'s status
   paragraph. **This is a planned step, not a surprise.**

**Done when:** `--stage 6` brings up a cluster with Backstage Running, the six
sites are corrected or annotated, `tests/unit` is green including the status-line
guard, and the memory figure is re-measured (design §9).

---

## Task 2 — The catalog: eight components, derived from committed files

**No task review.**

**Creates:** `catalog-info.yaml` in each of the eight component directories, plus
a top-level `catalog-info.yaml` declaring the System.
**Modifies:** `platform/backstage/app-config.yaml`, `deploy/k8s/backstage.yaml`.

The eight, measured from the tree: `agents/claims-agent`,
`agents/settlement-agent`, `apps/customer-ui`, `apps/reviewer-ui`,
`platform/eval-scorer`, `platform/policy-decision-service`,
`systems/payments-api`, `tools/payments-mcp`.

1. One `catalog-info.yaml` per component. Each names its owner, its System, and
   **links the agent card in `contracts/agent-cards/` where one exists** — those
   are already this repo's reviewable interface definition and the catalog should
   point at them rather than restate them.
2. Static catalog locations reading from the image filesystem (design §3). **No
   git discovery** — there is no remote.
3. **Wire the Kubernetes plugin** so each entity shows its live workload. This is
   the read-only ServiceAccount from Task 1.
4. **A guard that the catalog matches the tree.** The eight entries must be
   derived from a directory walk, not a hand-maintained list — this repo's
   standing rule, and a catalog is exactly the kind of list that silently rots.
   A ninth component added with no catalog entry must redden.

**Done when:** all eight appear with live workloads, and the guard fails when a
component has no entry.

---

## Task 3 — The autonomy card: reading the contract, and changing it

**TASK REVIEW REQUIRED, on opus.** This is the security boundary (design §4) and
the only task that grants a write.

**Creates:** the Backstage plugin/card, its RBAC (`Role` + `RoleBinding` +
a **second, separate** ServiceAccount).
**Modifies:** `deploy/k8s/backstage.yaml`, `platform/backstage/**`.

1. **Read path first.** The card shows, per agent identity, the level the
   contract currently resolves — read live from the `FeatureFlag`
   `northstar-autonomy`, not from a cached copy. On a fresh cluster both read
   `assisted`, because the contract ships refusing (M5-R8) and **M6 does not
   change that**.
2. **Then the write path**, with the §4 constraints, every one of which is
   testable and all of which must be tested:
   - namespaced `Role`, verbs `get`/`list`/`patch` **only**, no `delete`;
   - `resourceNames: [northstar-autonomy]` — **not `*`**;
   - **a different ServiceAccount from the read-only one** Tasks 1–2 use;
   - no access to Secrets, or to Deployments beyond the read plugin's needs.
3. **The write must be auditable and distinguishable from `kubectl`.** A
   guardrail lowered through a convenience UI that looks identical in the audit
   trail to one lowered deliberately is a hole in what M3/M4/M5 built.
4. **The PDS is still the only decision point.** Nothing here may give Backstage
   a path to a decision except by editing the resource the PDS already reads.

**Mutate every one of these.** A `Role` that is too broad and a `Role` that is
correct look identical until something tries the extra verb: write the negative
tests (patch a *different* FeatureFlag → denied; `delete` → denied; read a
Secret → denied) and watch them fail closed.

**Done when:** the card reads live, writes within the narrow grant, the negative
RBAC tests pass, and the contract is left **refusing**.

---

## Task 4 — The demo, the evidence, the e2e

**No task review** (the whole-branch review is the net).

**Creates:** `tests/e2e/test_golden_path.py`.
**Modifies:** `scripts/start-demo.sh`, `docs/architecture.md`,
`docs/telemetry-contract.md`, `README.md`,
`deploy/observability/dashboards/evidence.ndjson`.

1. **`start-demo.sh`**: forward Backstage as a surface. Use the
   `verify_existing_surface` path. **Add it to the "All surfaces verified" gate
   condition** — M5's J1 was exactly this omission, found by its review, and
   repeating it one milestone later would be embarrassing.
2. **The e2e, and it carries the milestone's anti-vacuity weight** (design §5.5):
   drive the card's write, prove the *same* observable outcome M5 proved from
   `kubectl` — a refused settlement becomes executed, **no redeploy** — then
   revert. **It must fail if the card's write does not reach the resource.**
   Prove that by mutation; do not assert it. M5's equivalent was neutered at the
   harness and watched go red, and that is the standard.
3. **Docs**: architecture rows for Backstage, the contract-UI story, the §10
   limitations **stated plainly** — an unauthenticated UI that can lower a
   guardrail is a thing the talk must say out loud, not a thing the docs omit.
4. **Do NOT run the gate.** It runs after the whole-branch review's fix wave.

---

## Standing constraints, in force for every task

- **Never run `scripts/teardown.sh`. Never `kind delete`. Never touch
  `kind-registry`. Recreating the cluster needs explicit permission, each time.**
- **Never install into an existing service venv.** All seven are on
  `pytest 9.1.1`.
- **Mutate every guard**, with `scripts/mutate-guard.sh`, and **disbelieve a
  GREEN**. **Never pipe the test command** — the pipe's exit status wins, which
  produced a false GREEN in M5's own review (M5-R37).
- **A guard must not skip where it should fail.** Five instances in M5, twice
  inside a guard written to close a hole. When a guard derives "what should be
  true now" from the environment rather than committed content, **ask what it
  answers on `main`** — that is the shape it keeps taking.
- **Measure, then write down what you measured. Derive figures; never retype.**
- **100% telemetry, never sampled** — and watch for *loss*, which looks identical
  to sampling from outside.
- **Leave the contract refusing** (M5-R8) at the end of every task.
- Nothing is filed or written to any upstream repo or tracker. `.env` and
  credentials never appear in committed content.
