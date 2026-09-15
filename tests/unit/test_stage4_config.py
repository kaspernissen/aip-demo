"""M4 Task 1: guards for what actually shipped for stage 4.

Plan amendment A1: the human partner declined a cluster recreate on
2026-09-12 -- Dashboards' fifth host port mapping can only be added at cluster
CREATION (`extraPortMappings`), the same create-time rule that already governs
`disableDefaultCNI` (see tests/unit/test_stage2_cilium_config.py). So stage 4
runs on stage 3's cluster config unchanged, and OpenSearch Dashboards is
reached by port-forward via `scripts/start-demo.sh` (plan amendment A2).

**Fix round 1 rewrote most of this file, because two of its three original
assertions were inert against the edit they claimed to catch.** That is the
defect class this repo keeps producing, and the review found it here twice:

* `"stage3.yaml" in setup` (finding F6) could not distinguish the three
  load-bearing lines that map `--stage 4` onto stage 3's kind config from the
  four other places the filename appears -- help text, two comments and an
  `echo`. Deleting the load-bearing lines left it green while `--stage 4`
  stopped being able to create a cluster at all. It now binds to the
  `STAGE -eq 4` -> `stage3.yaml` mapping itself, in the comment-stripped
  script.
* `"start-demo.sh" in smoke` (finding F7) was satisfied by either of the two
  remedies in `smoke-test.sh`, so reverting one of them alone left it green.
  It now requires both, by their own text, with whole-line comments stripped
  so no comment can supply either.

Two guards are new in the fix round:

* `test_below_stage_4_the_collector_config_names_no_opensearch` (finding F10,
  ruling M4-R8) runs `setup-kind.sh`'s own sed program -- read out of the
  script, so the two cannot drift -- and checks the result is a valid
  collector config that still exports traces to Jaeger and names OpenSearch
  nowhere.
* `test_the_opensearch_compat_processor_covers_every_colliding_attribute`
  (finding F1) derives the colliding attribute keys from the PDS's own
  declared attribute list, so a second collider added later fails a unit test
  instead of a bulk request.

`test_start_demo_skips_stage_gated_surfaces_that_do_not_exist` (finding F8) is
behavioural rather than textual: it runs the real script against fake
`kubectl`/`lsof`/`curl` and asserts on what it did.

**M4 Task 2 and its own fix round widened the file's subject from OpenSearch to
"whatever is stage-4-only", which is now two backends rather than one.** Task 2
added `prometheus` to the absent-services cases above, and its fix round added
three guards for findings F1 and F2:

* `test_below_stage_4_the_collector_config_names_no_prometheus_exporter` --
  ruling M4-R8's *metrics* half, which had been unenforced. The strip was
  correct and nothing said so, which is the state that lets the next
  regression through silently.
* `test_the_strip_message_names_every_backend_it_drops` -- the operator-facing
  `echo` still described a one-backend strip after the commit that made it a
  two-backend one. The list it must name is now DERIVED from the configs, so
  the third bracketed region reddens a test instead of ageing a sentence.
* (the third is the second parametrisation of the first.)

The live end-to-end assertions -- span parity between Jaeger and OpenSearch,
the decision span being queryable, and Jaeger keeping the canonical attribute
names -- are in `tests/e2e/test_opensearch_trace_ingest.py`, which needs the
cluster.
"""
import ast
import json
import os
import re
import signal
import stat
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

from _start_demo_fakes import port_aware_fake_curl

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts/setup-kind.sh"
SMOKE_TEST = REPO_ROOT / "scripts/smoke-test.sh"
START_DEMO = REPO_ROOT / "scripts/start-demo.sh"
COLLECTOR_CONFIGS = [
    REPO_ROOT / "deploy/observability/collector-config.yaml",
    REPO_ROOT / "deploy/observability/collector-config-dash0.yaml",
]
PDS_TELEMETRY = REPO_ROOT / "platform/policy-decision-service/pds/telemetry.py"

# M4 Task 3's deliverable, and the three names every other reference to it
# derives from rather than restates.
EVIDENCE_NDJSON = REPO_ROOT / "deploy/observability/dashboards/evidence.ndjson"
EVIDENCE_DASHBOARD_ID = "northstar-evidence"
EVIDENCE_README_ID = "northstar-evidence-readme"
# The index the `opensearch` exporter's ss4o mapping mode writes traces to.
EVIDENCE_INDEX = "ss4o_traces-default-namespace"
ARCHITECTURE = REPO_ROOT / "docs/architecture.md"


def _without_comments(path: Path) -> str:
    """`path` with the shebang and every whole-line comment removed.

    The same tool `tests/unit/test_stage2_cilium_config.py::_effective_script`
    introduced, and for the same reason: a comment must not be able to satisfy
    an assertion. Trailing comments are deliberately not stripped (doing that
    safely needs a shell parser), so the assertions below are additionally
    written specifically enough that a trailing comment could not supply them.
    """
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


# --- Amendment A1: no stage4.yaml, and --stage 4 still creates a cluster ----


def test_stage_4_reuses_the_stage_3_cluster_config():
    """Amendment A1: no stage4.yaml exists, deliberately, and `--stage 4` is
    wired to stage 3's kind config.

    The human partner declined a cluster recreate on 2026-09-12, so stage 4
    runs on stage 3's cluster and reaches OpenSearch Dashboards by
    port-forward. A stage4.yaml appearing later means someone recreated the
    cluster without asking -- which is a standing constraint, not a preference.

    The second assertion is the one finding F6 rewrote. The edit it has to
    catch is the deletion of the conditional in `setup-kind.sh` that maps
    `--stage 4` onto `deploy/kind/stage3.yaml`. Delete it and `--stage 4`
    falls through to `deploy/kind/stage${STAGE}.yaml`, fails the
    `[[ -f ... ]]` check with "deploy/kind/stage4.yaml not found", and cannot
    bring a cluster up at all. The old assertion -- `"stage3.yaml" in setup`
    -- stayed green through that edit, because the filename also appears in
    the help text, in two comments and in an `echo`. This one binds to the
    conditional and the assignment together, in the comment-stripped script.

    **M5 Task 2 widened both halves to stage 5, for the same amendment-A1
    reasoning stage 4 already established: nothing that task installs needs a
    new host port either.** The conditional this test binds to used to be a
    bare `if [[ "${STAGE}" -eq 4 ]]; then`; M5 made it
    `if [[ "${STAGE}" -eq 4 || "${STAGE}" -eq 5 ]]; then`.

    **M6 Task 1 widens it again, to stage 6: Backstage is reached by
    port-forward, same as every platform component here, so it needs no new
    host port either.** The conditional is now
    `if [[ "${STAGE}" -eq 4 || "${STAGE}" -eq 5 || "${STAGE}" -eq 6 ]]; then`,
    and this test's regex is written to match that combined form
    specifically, not a generic `-eq 4` fragment -- so a future edit that
    split it back into separate `if` blocks, or that dropped any one
    disjunct while leaving the others, reddens here rather than passing on a
    coincidental substring match.
    """
    assert not (REPO_ROOT / "deploy/kind/stage4.yaml").exists(), (
        "deploy/kind/stage4.yaml exists. Stage 4 was deliberately built on "
        "stage 3's cluster config (plan amendment A1) because extraPortMappings "
        "is create-time only and the recreate was declined. If a recreate has "
        "since been authorised, delete this test in the same commit that adds "
        "the file, and say who authorised it."
    )
    assert not (REPO_ROOT / "deploy/kind/stage5.yaml").exists(), (
        "deploy/kind/stage5.yaml exists. M5 Task 2 deliberately built stage 5 "
        "on stage 3's cluster config too (nothing it installs needs a new "
        "host port) -- the same amendment-A1 reasoning stage 4 already "
        "established. If a recreate has since been authorised, delete this "
        "test in the same commit that adds the file, and say who authorised "
        "it."
    )
    assert not (REPO_ROOT / "deploy/kind/stage6.yaml").exists(), (
        "deploy/kind/stage6.yaml exists. M6 Task 1 deliberately built stage 6 "
        "on stage 3's cluster config too -- Backstage is reached by "
        "port-forward, not a kind host port, the same amendment-A1 reasoning "
        "stages 4 and 5 already established. If a recreate has since been "
        "authorised, delete this test in the same commit that adds the file, "
        "and say who authorised it."
    )
    setup = _without_comments(SETUP_KIND)
    mapping = re.search(
        r'if \[\[ "\$\{STAGE\}" -eq 4 \|\| "\$\{STAGE\}" -eq 5 '
        r'\|\| "\$\{STAGE\}" -eq 6 \]\]; then\s*\n'
        r'\s*KIND_CONFIG="\$ROOT/deploy/kind/stage3\.yaml"\s*\n'
        r"\s*fi",
        setup,
    )
    assert mapping, (
        "scripts/setup-kind.sh no longer maps `--stage 4`, `--stage 5` and "
        "`--stage 6` onto deploy/kind/stage3.yaml via one combined condition. "
        "Without that mapping KIND_CONFIG falls through to "
        "deploy/kind/stage${STAGE}.yaml, which amendment A1 says must never "
        "exist for any of the three, so the affected `--stage N` cannot "
        "create a cluster at all -- it dies at the "
        "`[[ -f \"$KIND_CONFIG\" ]]` check. (This assertion used to be a bare "
        "`\"stage3.yaml\" in setup`, which five other mentions of the "
        "filename kept green through exactly that deletion: review "
        "finding F6.)"
    )


# --- Amendment A2: the port-forward remedy names the script ----------------


def test_the_port_forward_remedy_points_at_the_script():
    """Step A2: BOTH of smoke-test.sh's remedies tell the operator to run
    start-demo.sh.

    `smoke-test.sh` fails a port two different ways -- "nothing is listening"
    (lsof) and "LISTENING but carries no request" (curl, ruling R20) -- and
    each has its own remedy line. Finding F7: the original assertion was
    `"start-demo.sh" in smoke`, so reverting *either* remedy on its own left
    it green, and only the mutation that replaced every occurrence went red.
    Both are required here, matched by the text of the remedy that carries
    them, against a comment-stripped file so no comment can supply either.
    """
    smoke = _without_comments(SMOKE_TEST)
    remedies = {
        "the lsof branch (nothing is listening on that port)":
            "Port-forwards die after a redeploy and need re-establishing",
        "the curl branch (bound but dead, ruling R20)":
            "Re-establish every forward, verified live rather than merely bound",
    }
    for description, anchor in remedies.items():
        lines = [line for line in smoke.splitlines() if anchor in line]
        assert len(lines) == 1, (
            f"expected exactly one remedy line for {description} in "
            f"scripts/smoke-test.sh matching {anchor!r}, found {len(lines)}. "
            "If the wording changed, update this guard in the same commit -- "
            "do not delete the assertion."
        )
        assert "./scripts/start-demo.sh" in lines[0], (
            f"the remedy for {description} no longer names "
            "./scripts/start-demo.sh: {}\n"
            "Assembling a `kubectl port-forward` line by hand is what rulings "
            "R10 and R20 came from, and a remedy that names the script in only "
            "one of the two branches is the half-inert guard review finding F7 "
            "found.".format(lines[0].strip())
        )
    assert START_DEMO.exists(), "scripts/start-demo.sh is referenced but missing"


# --- Finding F10 / ruling M4-R8: no OpenSearch in the config below stage 4 --


def _stage4_strip_expression() -> str:
    """setup-kind.sh's own sed program, read out of the script.

    Read rather than duplicated, deliberately: a copy here would let the
    script's rule and this test's rule drift apart, and then the test would be
    green about something the script no longer does.
    """
    setup = _without_comments(SETUP_KIND)
    match = re.search(r"^STAGE4_ONLY_STRIP='([^']+)'$", setup, re.MULTILINE)
    assert match, (
        "scripts/setup-kind.sh no longer defines STAGE4_ONLY_STRIP, so below "
        "stage 4 it is applying the collector config whole -- which deploys a "
        "collector naming an OpenSearch AND a Prometheus that cluster does "
        "not have (review finding F10, ruling M4-R8; the metrics half arrived "
        "with M4 Task 2)"
    )
    return match.group(1)


def test_setup_kind_strips_the_stage_4_regions_below_stage_4():
    """The script must actually *use* the expression, and only below stage 4."""
    setup = _without_comments(SETUP_KIND)
    assert re.search(
        r'if \[\[ "\$\{STAGE\}" -lt 4 \]\]; then', setup
    ), (
        "scripts/setup-kind.sh no longer branches on `STAGE -lt 4` around the "
        "collector ConfigMap assembly, so either every stage gets the "
        "stage-4-only regions -- the OpenSearch branch and the Prometheus "
        "metrics exporter (finding F10) -- or no stage does"
    )
    assert re.search(
        r'sed "\$STAGE4_ONLY_STRIP" "\$COLLECTOR_CONFIG" > "\$COLLECTOR_CONFIG_APPLIED"',
        setup,
    ), "the stage-4 strip is defined but never applied"
    assert re.search(
        r'--from-file=collector-config\.yaml="\$COLLECTOR_CONFIG_APPLIED"', setup
    ), (
        "the ConfigMap is still built from $COLLECTOR_CONFIG rather than from "
        "the possibly-stripped $COLLECTOR_CONFIG_APPLIED, so the strip has no "
        "effect on what is deployed"
    )


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_below_stage_4_the_collector_config_names_no_opensearch(config):
    """Run the real sed program and check what comes out.

    Three things have to hold for the stripped config to be correct, and all
    three are asserted rather than eyeballed: it parses as YAML; it still
    exports traces to Jaeger (a strip that removed the whole traces pipeline
    would satisfy "no OpenSearch" and be catastrophic); and the word
    "opensearch" does not survive anywhere -- exporter, processor, connector or
    pipeline.
    """
    stripped = subprocess.run(
        ["sed", _stage4_strip_expression(), str(config)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "opensearch" not in stripped.lower(), (
        f"stripping the stage-4-only regions of {config.name} leaves an "
        "OpenSearch reference behind, so a stage-1/2/3 cluster would deploy a "
        "collector exporting to a backend it does not have (finding F10). "
        "Every OpenSearch-only region must sit between `# BEGIN stage-4-only` "
        "and `# END stage-4-only`, including the single `- forward/opensearch` "
        "entry in the traces pipeline's exporter list."
    )
    parsed = yaml.safe_load(stripped)
    assert parsed is not None, f"the stripped {config.name} is not valid YAML"
    traces = parsed["service"]["pipelines"]["traces"]
    assert "otlp_grpc/jaeger" in traces["exporters"], (
        f"the stripped {config.name} no longer exports traces to Jaeger. The "
        "strip took too much: below stage 4 the collector must behave exactly "
        f"as it did before M4. Exporters left: {traces['exporters']}"
    )
    assert "traces/opensearch" not in parsed["service"]["pipelines"], (
        "the OpenSearch branch pipeline survived the strip"
    )
    for exporter in traces["exporters"]:
        assert exporter in parsed["exporters"], (
            f"the stripped {config.name}'s traces pipeline names exporter "
            f"{exporter!r}, which its `exporters` section does not define -- "
            "the collector would refuse to start"
        )


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_below_stage_4_the_collector_config_names_no_prometheus_exporter(config):
    """The metrics half of ruling M4-R8, added in M4 Task 2's fix round 1.

    The guard above is the traces half and was the whole of it until M4 Task 2
    bracketed a second stage-4-only region -- the `otlp_http/prometheus`
    exporter and its entry in the `metrics` pipeline. Review finding F1: the
    strip was correct, and nothing asserted it. Remove or misplace either
    marker pair and a `--stage 1|2|3` cluster deploys a collector exporting
    metrics to `prometheus.observability.svc.cluster.local:9090`, a Service
    that does not exist -- F10's exact shape one signal over, with all of
    `tests/unit` staying green through it.

    **Why this is not `"prometheus" not in stripped`.** The metrics pipeline
    legitimately keeps its `prometheus/spire` RECEIVER below stage 4 (M2.1 Task
    4 scrapes SPIRE at every stage), so the broad word would false-positive on
    a correct config. The assertion is the exporter id, and then the same
    declared-ness walk the traces pipeline already gets.
    """
    stripped = subprocess.run(
        ["sed", _stage4_strip_expression(), str(config)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "otlp_http/prometheus" not in stripped, (
        f"stripping the stage-4-only regions of {config.name} leaves "
        "`otlp_http/prometheus` behind, so a stage-1/2/3 cluster would deploy "
        "a collector exporting metrics to a Prometheus it does not have "
        "(ruling M4-R8, metrics half). Both the exporter's declaration and its "
        "entry in the `metrics` pipeline's exporter list must sit between "
        "`# BEGIN stage-4-only` and `# END stage-4-only` -- which is why that "
        "list is written one-per-line."
    )
    parsed = yaml.safe_load(stripped)
    metrics = parsed["service"]["pipelines"]["metrics"]
    assert "debug" in metrics["exporters"], (
        f"the stripped {config.name}'s metrics pipeline exports nowhere at "
        "all. The strip took too much: below stage 4 the collector must behave "
        f"exactly as it did before M4. Exporters left: {metrics['exporters']}"
    )
    assert "prometheus/spire" in metrics["receivers"], (
        f"the stripped {config.name}'s metrics pipeline lost the "
        "`prometheus/spire` receiver, which is not stage-4-only -- SPIRE has "
        "been scraped at every stage since M2.1 Task 4"
    )
    for exporter in metrics["exporters"]:
        assert exporter in parsed["exporters"], (
            f"the stripped {config.name}'s metrics pipeline names exporter "
            f"{exporter!r}, which its `exporters` section does not define -- "
            "the collector would refuse to start"
        )


# Transport and sink names that say nothing about WHICH backend an exporter
# writes to: `otlp_http/prometheus` is a Prometheus exporter, not an "otlp_http"
# one. Everything else in an exporter id is treated as a backend name the
# operator-facing message has to be able to say out loud.
_GENERIC_EXPORTER_SEGMENTS = frozenset({
    "otlp", "otlp_http", "otlp_grpc", "otlphttp", "otlpgrpc",
    "debug", "file", "nop", "forward",
})


def test_the_strip_message_names_every_backend_it_drops():
    """The operator-facing `echo` must name every backend the strip removes.

    Review finding F2, and the structural half of its fix. M4 Task 2 added a
    second stage-4-only exporter and left that message saying the strip drops
    "the stage-4-only OpenSearch branch" -- true when written, false the moment
    a second region was bracketed, in a file that same commit edited. Correcting
    the sentence fixes today; this stops tomorrow, because the message is no
    longer a restatement anyone has to remember to update.

    The list is DERIVED, not duplicated: run the script's own sed program, diff
    the `exporters` section before and after, and require each removed
    exporter to contribute a recognisable backend word to the message. So
    bracketing a third region reddens this with the missing word named, rather
    than silently leaving the operator told about two of three.
    """
    setup = SETUP_KIND.read_text()
    match = re.search(
        r'^\s*echo -e "\$\{YELLOW\}(Stage .*?)\$\{NC\}"$', setup, re.MULTILINE
    )
    assert match, (
        "scripts/setup-kind.sh no longer prints a `Stage N: dropping ...` "
        "message when it strips the stage-4-only regions. The operator has no "
        "other way to learn that the collector they are about to get is not "
        "the collector in the repository."
    )
    message = match.group(1).lower()

    expression = _stage4_strip_expression()
    missing = {}
    for config in COLLECTOR_CONFIGS:
        full = yaml.safe_load(config.read_text())
        stripped = yaml.safe_load(subprocess.run(
            ["sed", expression, str(config)],
            capture_output=True, text=True, check=True,
        ).stdout)
        removed = set(full.get("exporters") or {}) - set(stripped.get("exporters") or {})
        for exporter in sorted(removed):
            backends = [
                segment for segment in exporter.split("/")
                if segment not in _GENERIC_EXPORTER_SEGMENTS
            ]
            if not any(backend.lower() in message for backend in backends):
                missing[exporter] = backends

    assert not missing, (
        "scripts/setup-kind.sh's stage-strip message does not name every "
        "backend it drops. The message reads:\n"
        f"  {match.group(1)}\n"
        "but the strip also removes these exporters, and none of the backend "
        "words in their names appears in it:\n"
        + "\n".join(f"  {exporter} -> any of {backends}"
                    for exporter, backends in sorted(missing.items()))
        + "\nAdd the backend to the message (and to the comment above "
          "STAGE4_ONLY_STRIP, which describes the same convention)."
    )


def test_the_stage_4_completion_message_names_everything_stage_4_installs():
    """The same structural fix as the test above, one message over.

    The `Stage 4 (Observable) is up: ...` line is the last thing an operator
    reads, and it is where they learn what they now have. M4 Task 2 added a
    third Helm release to the stage-4 block and left that line describing two
    -- the same defect as finding F2, in the same file, found by sweeping for
    it rather than by review. Restating an install list in prose is what keeps
    producing this, so the list is derived instead: every
    `helm upgrade --install <release>` between the `STAGE -ge 4` guard and that
    block's closing `fi` must be named in the message.

    Release names are matched case-insensitively, because the message spells
    them as products (`OpenSearch`) and the script as Helm releases
    (`opensearch`); a release whose name is genuinely absent from the message
    fails regardless of case.

    **M4 Task 4 widened "installs" past Helm.** The block gained a workload
    that is not a chart at all -- `scripts/rebuild-and-deploy.sh eval-scorer`,
    a container image built, pushed and applied -- and a helm-only derivation
    cannot see it, so the very defect this test was written for (a completion
    message describing less than the block installs) was reachable again one
    task after it was closed. Components deployed through
    `rebuild-and-deploy.sh` *inside this block* are now derived too. Only
    inside it: Step 9's unconditional `rebuild-and-deploy.sh payments-api ...`
    line deploys the stage-1 workloads at every stage, and requiring the
    stage-4 message to name those would be requiring it to restate the whole
    system.
    """
    lines = SETUP_KIND.read_text().splitlines()

    starts = [i for i, l in enumerate(lines) if re.match(r'if \[\[ "\$\{STAGE\}" -ge 4 \]\]; then', l)]
    assert len(starts) == 1, (
        "expected exactly one `if [[ \"${STAGE}\" -ge 4 ]]; then` block in "
        f"scripts/setup-kind.sh, found {len(starts)} -- this guard reads that "
        "block to learn what stage 4 installs"
    )
    start = starts[0]
    ends = [i for i in range(start + 1, len(lines)) if lines[i] == "fi"]
    assert ends, "the `STAGE -ge 4` block is never closed by a column-0 `fi`"
    block = lines[start:ends[0]]
    releases = [
        m.group(1)
        for l in block
        if (m := re.search(r"helm upgrade --install (\S+)", l))
    ]
    assert releases, (
        "the `STAGE -ge 4` block installs no Helm release at all, so either "
        "stage 4 stopped installing anything or this guard is reading the "
        "wrong block"
    )
    # The non-Helm half: every component name passed to rebuild-and-deploy.sh
    # inside this block. `\S+` per argument so a multi-component invocation is
    # read as the several workloads it is, and comment lines are excluded so
    # prose mentioning the script cannot inflate the list.
    installed = list(releases)
    for l in block:
        if l.lstrip().startswith("#"):
            continue
        m = re.search(r"rebuild-and-deploy\.sh\"?\s+(.+)$", l)
        if m:
            installed.extend(m.group(1).split())

    message = next(
        (l for l in lines if "Stage 4 (Observable) is up" in l), None
    )
    assert message, (
        "scripts/setup-kind.sh no longer prints a `Stage 4 (Observable) is "
        "up: ...` completion message"
    )
    lowered = message.lower()
    missing = [r for r in installed if r.lower() not in lowered]
    assert not missing, (
        f"stage 4 installs {installed}, but its completion message does not "
        f"name {missing}. The operator is told about some of what they now "
        "have and not the rest, which is how they end up not knowing a "
        "backend is running. The message reads:\n  "
        + message.strip()
    )


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_the_stage_4_markers_are_balanced_and_never_nested(config):
    """The markers must alternate strictly BEGIN, END, BEGIN, END, ...

    An unbalanced pair deletes to end-of-file (missing END) or not at all.
    **Nesting is the subtler case, and re-review finding N6 is that the first
    version of this guard could not see it**: it counted BEGINs and ENDs and
    zipped them, so `BEGIN, BEGIN, END, END` passed while `sed` pairs
    `(BEGIN1 ... END1)` and leaves `BEGIN2` deleted and `END2` stranded in the
    output -- a different deletion than the file intends. Walking the markers
    in order and tracking depth is what actually models `sed`'s range
    behaviour, so that is what this does now.
    """
    lines = config.read_text().splitlines()
    markers = [
        (i, line.strip())
        for i, line in enumerate(lines)
        if line.strip() in {"# BEGIN stage-4-only", "# END stage-4-only"}
    ]
    assert markers, f"{config.name} has no stage-4-only markers at all"

    open_at = None
    for index, marker in markers:
        if marker.endswith("BEGIN stage-4-only"):
            assert open_at is None, (
                f"{config.name}: `# BEGIN stage-4-only` at line {index + 1} "
                f"opens while the one at line {open_at + 1} is still open. "
                "Nested markers do not do what they look like: sed closes the "
                "outer range at the FIRST END, so the inner BEGIN is deleted "
                "and the second END is left stranded in the output."
            )
            open_at = index
        else:
            assert open_at is not None, (
                f"{config.name}: `# END stage-4-only` at line {index + 1} has "
                "no matching BEGIN before it, so sed treats it as ordinary "
                "text and whatever it was meant to close is not deleted."
            )
            open_at = None
    assert open_at is None, (
        f"{config.name}: `# BEGIN stage-4-only` at line {open_at + 1} is never "
        "closed -- a sed range with a missing END deletes everything from "
        "there to the end of the file."
    )


# --- Finding N4: the SHIPPED config must declare what its pipelines name ----


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_the_shipped_collector_config_declares_every_component_it_names(config):
    """Re-review finding N4: the same check the stripped config already gets,
    run over the config as shipped.

    `test_below_stage_4_the_collector_config_names_no_opensearch` verifies
    declared-ness only on the *stripped* output and only for the `traces`
    pipeline. Nothing covered the stage-4 config itself, so deleting the
    `connectors:` block from **both** files (the sync guard compares them to
    each other, and `None == None` passes) or dropping `- forward/opensearch`
    from the main traces exporter list left all unit tests green while the
    collector refuses to start at stage 4.

    A connector is the interesting case and the reason this is not merely
    symmetry: it must be used as an exporter in one pipeline and as a receiver
    in another. Declared and used on only one side is a startup failure, and
    `test_the_rename_is_confined_to_the_opensearch_branch` only ever checked
    the receiver side.
    """
    parsed = yaml.safe_load(config.read_text())
    receivers = set(parsed.get("receivers") or {})
    processors = set(parsed.get("processors") or {})
    exporters = set(parsed.get("exporters") or {})
    connectors = set(parsed.get("connectors") or {})

    used_as_receiver: set[str] = set()
    used_as_exporter: set[str] = set()
    for name, pipeline in parsed["service"]["pipelines"].items():
        for component in pipeline.get("receivers") or []:
            assert component in receivers | connectors, (
                f"{config.name}: pipeline {name!r} receives from "
                f"{component!r}, which is declared neither as a receiver nor "
                "as a connector. The collector refuses to start on this."
            )
            used_as_receiver.add(component)
        for component in pipeline.get("processors") or []:
            assert component in processors, (
                f"{config.name}: pipeline {name!r} runs processor "
                f"{component!r}, which the `processors` section does not "
                "declare. The collector refuses to start on this."
            )
        for component in pipeline.get("exporters") or []:
            assert component in exporters | connectors, (
                f"{config.name}: pipeline {name!r} exports to {component!r}, "
                "which is declared neither as an exporter nor as a connector. "
                "The collector refuses to start on this."
            )
            used_as_exporter.add(component)

    assert connectors, (
        f"{config.name} declares no connectors at all. The OpenSearch branch "
        "is a `forward/opensearch` tee off the main traces pipeline (spec "
        "§3); without it, either the branch is gone or it has been "
        "re-declared with its own receivers, which is the silent-drift shape "
        "the tee exists to prevent."
    )
    for connector in connectors:
        assert connector in used_as_exporter, (
            f"{config.name}: connector {connector!r} is declared but no "
            "pipeline exports to it, so nothing feeds it. A connector must be "
            "used on both sides or the collector refuses to start."
        )
        assert connector in used_as_receiver, (
            f"{config.name}: connector {connector!r} is declared but no "
            "pipeline receives from it, so everything sent into it is "
            "discarded. A connector must be used on both sides or the "
            "collector refuses to start."
        )


# --- Finding F1: the attribute collision, and the processor that fixes it ---


def _pds_attribute_keys() -> set[str]:
    """Every attribute key the PDS declares, parsed out of its own module.

    Parsed with `ast` rather than imported: this is a unit test and must not
    need the PDS's OpenTelemetry dependencies installed in the repo-root venv.
    `pds/telemetry.py` exists precisely so the guard tests and the contract
    cite one source (its own comment says so).

    Kept as the *lower bound* the repo-wide scan below must contain. It is not
    the scan's source of truth any more -- see `_first_party_attribute_keys`.
    """
    tree = ast.parse(PDS_TELEMETRY.read_text())
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(
            node.value.value, str
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith("ATTR_"):
                keys.add(node.value.value)
    assert keys, f"no ATTR_* string constants found in {PDS_TELEMETRY}"
    return keys


# Every directory holding a first-party service's source. The OpenSearch
# mapping is index-wide, so a collision does not have to occur within one
# span or even within one service: a scalar `x.y` on one service's span and an
# object-valued `x.y.z` on another's, in the same index, is enough for the
# second to be rejected permanently. Re-review finding N2 -- reading only the
# PDS could never have seen that.
SERVICE_SOURCE_DIRS = ["agents", "apps", "platform", "systems", "tools"]
SERVICE_SOURCE_SUFFIXES = {".py", ".go", ".ts", ".tsx"}
_SKIP_DIR_NAMES = {
    "node_modules", ".venv", "venv", "tests", "__pycache__",
    "dist", "build", ".next", "out",
}

# An OTel attribute key: lowercase dotted segments. Deliberately matched
# against EVERY string literal rather than only the arguments of
# `set_attribute` / `attribute.String` / `setAttribute` calls, because parsing
# call shapes across Python, Go and TypeScript is where a scanner like this
# quietly stops seeing things -- a constant defined in one file and used in
# another, a dict passed to `start_as_current_span(attributes=...)`, a Go
# `attribute.Key(...).String(...)`. Over-collecting is the safe direction: a
# non-attribute literal that happens to look like a key (module paths such as
# `mcp.client.streamable_http`, function references such as `rules.decide`)
# can only ever ADD a pair to the collision set, so the failure mode is a red
# test with a named pair a human can read -- never a silent hole.
#
# The figure worth publishing is the collision count, and the test below
# asserts it rather than this comment stating it: there is exactly one
# collision (`policy.decision` / `policy.decision.retryable`) and the
# `attributes/opensearch-compat` processor must rename it.
#
# The scan's file and key totals are NOT written down any more. This said
# "Measured 2026-09-13: 41 files, 52 candidate keys" and was falsified the
# same day by the seven `platform/eval-scorer/scorer/*.py` files added in the
# same commit -- re-derived, 48 files and 68 candidate keys. Nothing reddened,
# because those totals change whenever any service gains a source file and no
# assertion could usefully pin them. A number that moves on every unrelated
# commit is a number that will be stale on most of them.
_ATTRIBUTE_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_STRING_LITERAL = re.compile(
    r"\"([^\"\\\n]{1,120})\"|'([^'\\\n]{1,120})'|`([^`\\\n]{1,120})`"
)


def _first_party_attribute_keys() -> dict[str, set[str]]:
    """Candidate attribute keys across every first-party service, with the
    files each was found in (so a failure can name where to look).

    **What this cannot see, stated because a guard that overclaims is the
    defect this file keeps fixing:** keys computed at runtime (there are none
    today -- every key in this repo is a literal or a constant bound to one),
    and keys emitted by third-party libraries. The second is the larger set:
    `gen_ai.*` from PydanticAI, `mcp.*` from the MCP SDK, `http.*`/`url.*`
    from the ASGI and httpx instrumentations, and agentgateway's and SPIRE's
    own. Those are covered from the other end, against live data, by
    `tests/e2e/test_opensearch_trace_ingest.py`:
    `test_no_attribute_key_in_the_driven_trace_collides_uncovered` reads every
    key Jaeger actually holds for a driven trace, and
    `test_the_collector_rejected_no_spans_during_the_run` reddens on a
    rejection from any service that exported during the window.
    """
    found: dict[str, set[str]] = {}
    scanned_dirs = set()
    for directory in SERVICE_SOURCE_DIRS:
        root = REPO_ROOT / directory
        assert root.is_dir(), (
            f"{directory}/ does not exist, so this guard silently covers less "
            "than its docstring claims -- has a service moved?"
        )
        for path in sorted(root.rglob("*")):
            if path.suffix not in SERVICE_SOURCE_SUFFIXES or not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            scanned_dirs.add(directory)
            text = path.read_text(errors="replace")
            for match in _STRING_LITERAL.finditer(text):
                literal = match.group(1) or match.group(2) or match.group(3)
                if literal and _ATTRIBUTE_KEY.match(literal):
                    found.setdefault(literal, set()).add(
                        str(path.relative_to(REPO_ROOT))
                    )
    missing_dirs = set(SERVICE_SOURCE_DIRS) - scanned_dirs
    assert not missing_dirs, (
        f"no source files were scanned under {sorted(missing_dirs)}, so this "
        "guard is silently covering less than it claims. Check "
        "SERVICE_SOURCE_SUFFIXES and _SKIP_DIR_NAMES."
    )
    return found


def _collisions(keys: set[str]) -> set[tuple[str, str]]:
    """Pairs where one key is a strict dotted prefix of another.

    This is the shape OpenSearch cannot index: it expands dotted keys into
    nested objects, so `policy.decision` and `policy.decision.retryable` in the
    same document need `attributes.policy.decision` to be a scalar and an
    object at once.
    """
    return {
        (shorter, longer)
        for shorter in keys
        for longer in keys
        if longer != shorter and longer.startswith(shorter + ".")
    }


def test_the_opensearch_compat_processor_covers_every_colliding_attribute():
    """Finding F1: the rename must cover every collider, not just today's one.

    The shipped ingest path rejected 100% of `policy.decision` spans because
    the span sets both `policy.decision` and `policy.decision.retryable`. A
    second collider added later would reintroduce the defect silently -- the
    export fails permanently, per document, and only the collector log says
    so.

    **Re-review finding N2 widened what this reads.** It used to derive the
    collision set from `pds/telemetry.py` alone, while the comment it backed
    in `collector-config.yaml` claimed ten services. That gap mattered rather
    than being a scoping nit: the OpenSearch mapping is index-wide, so a
    scalar `x.y` on one service's span and an object-valued `x.y.z` on
    another's is enough for the second to be rejected -- and a collider added
    anywhere but the PDS, or added to the PDS as an inline
    `set_attribute("policy.decision.reason", ...)` rather than as an `ATTR_*`
    constant, failed no unit test at all. It now scans every first-party
    service's source; `_first_party_attribute_keys` documents exactly what
    that can and cannot see, and the e2e file covers the rest against live
    data.

    Add `policy.decision.reason` in any service tomorrow and this goes red
    here, in a unit test, rather than in a bulk request nobody is watching.
    """
    keys_by_file = _first_party_attribute_keys()
    # The PDS's declared keys are the floor: if the repo-wide scan ever stops
    # containing them, it has stopped working and every collision it reports
    # is meaningless.
    declared = _pds_attribute_keys()
    missing = declared - set(keys_by_file)
    assert not missing, (
        f"the repo-wide attribute scan did not find {sorted(missing)}, which "
        "pds/telemetry.py declares as ATTR_* constants. The scan is broken, "
        "so its collision set cannot be trusted -- check "
        "SERVICE_SOURCE_DIRS, SERVICE_SOURCE_SUFFIXES and _ATTRIBUTE_KEY."
    )

    collisions = _collisions(set(keys_by_file))
    assert collisions, (
        "no dotted-prefix collision found in any first-party service's "
        "source. If the collision was genuinely removed at the source -- e.g. "
        "policy.decision.retryable was renamed in pds/telemetry.py -- then the "
        "attributes/opensearch-compat processor and this test can both go, in "
        "the same commit, with the telemetry contract updated to match. Do not "
        "delete only the test."
    )
    for config in COLLECTOR_CONFIGS:
        parsed = yaml.safe_load(config.read_text())
        actions = parsed["processors"]["attributes/opensearch-compat"]["actions"]
        deleted = {
            action["key"] for action in actions if action.get("action") == "delete"
        }
        copied_from = {
            action["from_attribute"]
            for action in actions
            if action.get("from_attribute")
        }
        for shorter, longer in collisions:
            where = ", ".join(
                sorted(keys_by_file[shorter] | keys_by_file[longer])
            )
            assert longer in deleted, (
                f"{config.name}: attribute {longer!r} collides with "
                f"{shorter!r} (OpenSearch would need attributes.{shorter} to "
                "be both a scalar and an object), but the "
                "attributes/opensearch-compat processor does not delete it on "
                "the OpenSearch branch. Every document carrying both keys -- "
                "and they need not be the same span, or even the same "
                "service, because the mapping is index-wide -- will be "
                f"rejected permanently. That is review finding F1. Seen in: "
                f"{where}"
            )
            assert longer in copied_from, (
                f"{config.name}: {longer!r} is deleted on the OpenSearch "
                "branch but never copied to a non-colliding key first, so the "
                "attribute is lost rather than renamed"
            )


def test_the_rename_is_confined_to_the_opensearch_branch():
    """Ruling M4-R5, as far as a unit test can see it.

    What Jaeger and Dash0 actually receive is asserted against the live
    cluster by tests/e2e/test_opensearch_trace_ingest.py. This is the static
    half: the compat processor must appear in the `traces/opensearch` pipeline
    and in no other, because every other pipeline's output is what the
    canonical-name consumers read.
    """
    for config in COLLECTOR_CONFIGS:
        parsed = yaml.safe_load(config.read_text())
        pipelines = parsed["service"]["pipelines"]
        assert pipelines["traces/opensearch"]["processors"] == [
            "attributes/opensearch-compat"
        ], (
            f"{config.name}: the OpenSearch branch no longer runs "
            "attributes/opensearch-compat, so the colliding attribute reaches "
            f"OpenSearch unrenamed: {pipelines['traces/opensearch']}"
        )
        for name, pipeline in pipelines.items():
            if name == "traces/opensearch":
                continue
            assert "attributes/opensearch-compat" not in pipeline.get(
                "processors", []
            ), (
                f"{config.name}: pipeline {name!r} runs "
                "attributes/opensearch-compat. Ruling M4-R5: the rewrite is "
                "OpenSearch-branch only -- Jaeger and Dash0 must receive the "
                "canonical attribute names, or the evidence view's field names "
                "stop matching the trace an operator reads in Jaeger."
            )
        assert pipelines["traces/opensearch"]["receivers"] == ["forward/opensearch"], (
            f"{config.name}: the OpenSearch branch no longer receives from the "
            "forward/opensearch connector, so it has stopped being a tee off "
            "the main traces pipeline and can drift from what Jaeger sees"
        )


# --- Finding F8: start-demo.sh must not hard-fail below stage 4 ------------


_FAKE_KUBECTL = r"""#!/usr/bin/env bash
# `get svc/NAME -o name` decides whether a stage-gated surface "exists".
# `get featureflag/NAME -o name` does the same for flagd (M5 Task 6: it has
# no Service of its own, so start-demo.sh asks for the FeatureFlag object
# instead -- see FLAGD_AVAILABLE). Both are matched against the same
# FAKE_ABSENT_SERVICES list, keyed by the name after the slash.
if [[ "$*" == *"get svc/"* || "$*" == *"get featureflag/"* ]]; then
  for absent in $FAKE_ABSENT_SERVICES; do
    if [[ "$*" == *"svc/$absent"* || "$*" == *"featureflag/$absent"* ]]; then exit 1; fi
  done
  exit 0
fi
if [[ "$*" == *"port-forward"* ]]; then
  # An absent Service fails the same way real kubectl does -- immediately, so
  # the pid the caller recorded is already dead and fake lsof (which lists
  # live pids) cannot report it. That reproduces finding F8's actual cascade:
  # verify_owned_forward exits 1 and the EXIT trap kills the good forwards.
  for absent in $FAKE_ABSENT_SERVICES; do
    if [[ "$*" == *"svc/$absent"* ]]; then exit 1; fi
  done
  # exec so $! in the caller is this process's own pid, which fake lsof below
  # has to be able to report.
  exec sleep 300
fi
exit 0
"""

# Prints a header line (which verify_owned_forward's `awk NR>1` skips) and then
# every running pid in column 2, so the ownership check finds whichever pid the
# script is asking about.
_FAKE_LSOF = r"""#!/usr/bin/env bash
echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
ps -axo pid= | while read -r p; do echo "fake $p user 1u IPv4 0x0 0t0 TCP *:1 (LISTEN)"; done
exit 0
"""


def _run_start_demo(
    tmp_path, absent_services: str, refusing_ports: Iterable[str] = (),
):
    """Run the real start-demo.sh against fakes and return its output.

    `refusing_ports` (whole-branch review, carry-in 3): the fake `curl` used
    to be `"#!/usr/bin/env bash\\nexit 0\\n"` unconditionally -- inert on the
    one dimension `verify_owned_forward`/`verify_existing_surface` exist to
    check (ruling R20: a bound port proves nothing, only an answering one
    does). It is now `_start_demo_fakes.port_aware_fake_curl`, shared with
    `test_start_demo_v11.py`, which fails only for the ports named here. The
    default (no port refuses) reproduces the old fake's behaviour exactly, for
    callers that only need curl to get out of the way.

    The script ends in `wait`, so it never exits on its own: read until it
    prints its final line, then kill the whole process group (which is this
    test's own group, created with start_new_session, so nothing outside it is
    ever signalled -- the same "kill only what you started" rule the script
    itself follows).
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in {
        "kubectl": _FAKE_KUBECTL,
        "lsof": _FAKE_LSOF,
        "curl": port_aware_fake_curl(refusing_ports),
    }.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_ABSENT_SERVICES"] = absent_services

    proc = subprocess.Popen(
        ["bash", str(START_DEMO)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True,
    )
    output = []
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            output.append(line)
            if "Press Ctrl+C" in line:
                break
        else:  # pragma: no cover -- only on a hang
            raise AssertionError(
                "start-demo.sh produced no final line within 60s:\n"
                + "".join(output)
            )
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:  # pragma: no cover
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    return "".join(output), proc.returncode


def test_start_demo_skips_stage_gated_surfaces_that_do_not_exist(tmp_path):
    """Finding F8, behaviourally: below stage 4 the script must still work.

    As shipped, `start-demo.sh` forwarded `svc/opensearch-dashboards`
    unconditionally. On a stage-1/2/3 cluster that Service does not exist, the
    forward never binds, `verify_owned_forward` exits 1, and the EXIT trap then
    kills the four good forwards it had just created -- so the remedy for a
    dead port-forward left the operator with no port-forwards at all. And it
    was reachable by the documented path: both of `smoke-test.sh`'s remedies
    send the operator here.

    Asserted by running the script, not by reading it.
    """
    output, _ = _run_start_demo(
        tmp_path, absent_services="opensearch-dashboards reviewer-ui prometheus"
    )
    assert "verified up" in output, (
        "start-demo.sh did not get as far as verifying its surfaces on a "
        "cluster without the stage-gated Services:\n" + output
    )
    # `prometheus` joined this list in M4 Task 2, in the same commit that added
    # its forward. It is the third stage-gated surface and takes the same
    # `service_exists` guard as the other two; leaving it off the absent list
    # here would have let an unconditional Prometheus forward -- finding F8
    # exactly -- ship green.
    for surface in (
        "OpenSearch Dashboards: SKIPPED",
        "reviewer-ui: SKIPPED",
        "Prometheus: SKIPPED",
    ):
        assert surface in output, (
            f"start-demo.sh did not say {surface.split(':')[0]!r} was being "
            "skipped. A skip must be loud -- a silently missing surface is how "
            "an operator ends up reading an empty page and blaming the data:\n"
            f"{output}"
        )
    assert "http://localhost:5601" not in output, (
        "start-demo.sh printed a Dashboards URL for a cluster that has no "
        f"Dashboards:\n{output}"
    )
    assert "http://localhost:8081" not in output, (
        "start-demo.sh printed a reviewer-ui URL for a cluster that has no "
        f"reviewer-ui:\n{output}"
    )
    assert "http://localhost:9090" not in output, (
        "start-demo.sh printed a Prometheus URL for a cluster that has no "
        f"Prometheus:\n{output}"
    )
    # The four stage-1 surfaces are not optional and must still be there.
    for url in (
        "http://localhost:19001", "http://localhost:19002",
        "http://localhost:18000", "http://localhost:18080",
    ):
        assert url in output, f"{url} missing from the surface table:\n{output}"


def test_start_demo_forwards_the_stage_4_surfaces_when_they_do_exist(tmp_path):
    """The other half, without which the test above passes on a script that
    skips everything: with every Service present, both stage-gated surfaces
    are forwarded, verified and printed."""
    output, _ = _run_start_demo(tmp_path, absent_services="")
    assert "All surfaces verified up." in output, output
    assert "SKIPPED" not in output, (
        f"a surface was skipped on a cluster that has all of them:\n{output}"
    )
    assert "http://localhost:5601" in output, output
    assert "http://localhost:8081" in output, output
    assert "http://localhost:9090" in output, output


def test_the_all_verified_banner_requires_eval_scorer_and_flagd_too(tmp_path):
    """Whole-branch review, J1: `start-demo.sh:369`'s "All surfaces verified
    up." gate used to check only `DASHBOARDS_AVAILABLE`, `REVIEWER_UI_AVAILABLE`
    and `PROMETHEUS_AVAILABLE`, omitting `EVAL_SCORER_AVAILABLE` and
    `FLAGD_AVAILABLE` -- both forwarded and verified earlier in the same
    script, with their own real SKIPPED branches. On a stage-4 cluster (which
    has neither eval-scorer nor the flagd FeatureFlag, both stage-5-only) the
    old gate printed two YELLOW `SKIPPED` lines immediately followed by GREEN
    `All surfaces verified up.` -- the exact contradiction the else-branch
    exists to prevent, and it never fired.

    Dashboards, reviewer-ui and Prometheus are all present here -- the old
    gate's three conditions are individually satisfied -- so this reproduces
    the contradiction precisely: it is eval-scorer and flagd alone that must
    still turn the banner over to the honest branch.
    """
    output, _ = _run_start_demo(
        tmp_path, absent_services="eval-scorer northstar-autonomy",
    )
    assert "eval-scorer: SKIPPED" in output, output
    assert "flagd metrics: SKIPPED" in output, output
    assert "All surfaces verified up." not in output, (
        "the gate printed the all-clear banner while eval-scorer and flagd "
        f"were both SKIPPED just above it:\n{output}"
    )
    assert "Every surface this cluster has is verified up." in output, output
    assert "Some stage-gated surfaces are absent" in output, output


def test_reviewer_uis_bound_but_dead_forward_is_refused_ruling_r20(tmp_path):
    """Whole-branch review, carry-in 3: `verify_existing_surface`'s R20 branch
    (port bound, but the request it carries never answers) had no test
    anywhere -- the only path this fake `curl` could not previously exercise,
    because it always answered.

    reviewer-ui is a `NodePort`, not a forward this script owns, so it is
    checked through `verify_existing_surface` rather than
    `verify_owned_forward`: fake `lsof` reports its port (8081) bound (as it
    does for every port, unconditionally), and fake `curl` is told to refuse
    exactly that port -- reproducing a dead `kubectl port-forward`'s own
    socket-stays-bound-but-nothing-answers shape for a NodePort instead.
    """
    output, returncode = _run_start_demo(
        tmp_path, absent_services="", refusing_ports={"8081"},
    )
    assert returncode != 0, (
        f"expected start-demo.sh to exit non-zero on reviewer-ui's bound but "
        f"dead port:\n{output}"
    )
    assert "reviewer-ui" in output and "ruling R20" in output, (
        "expected the R20 refusal, naming reviewer-ui, for a bound port that "
        f"carries no request:\n{output}"
    )
    assert "All surfaces verified up." not in output, output


# --- M4 Task 3: the evidence view, and the two things that make it real -----


def _evidence_objects() -> list[dict]:
    """Every saved object in the committed export, with the summary line dropped.

    Parses the file the way the importer does -- line by line, each line a JSON
    object -- which is also the assertion that no line is a comment. NDJSON has
    no comment syntax, and measured against this cluster's Dashboards a single
    leading `// ...` line makes the import fail outright with
    `400 Bad Request, Unexpected token '/'`. That is why the view's own header
    is a markdown panel inside the file rather than a comment above it.
    """
    objects = []
    for number, line in enumerate(EVIDENCE_NDJSON.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover -- diagnostic path
            raise AssertionError(
                f"{EVIDENCE_NDJSON.relative_to(REPO_ROOT)} line {number} is not "
                f"JSON, so `POST /api/saved_objects/_import` will reject the "
                f"whole file with a 400 and import nothing: {exc}. NDJSON has "
                "no comment syntax -- the view's header is the "
                "`northstar-evidence-readme` markdown panel, not a comment."
            ) from exc
        # The exporter's own trailing summary, which the importer ignores.
        if "type" not in parsed and "exportedCount" in parsed:
            continue
        objects.append(parsed)
    return objects


def test_the_evidence_view_is_a_complete_importable_export():
    """Every panel the dashboard names is in the file, and nothing dangles.

    An export with a missing reference imports "successfully" and then renders
    an empty frame where a panel should be -- the exact silent-blank failure
    `tests/e2e/test_evidence_view.py` exists to prevent on the data side, one
    layer out.
    """
    objects = _evidence_objects()
    by_id = {(obj["type"], obj["id"]): obj for obj in objects}
    dashboards = [obj for obj in objects if obj["type"] == "dashboard"]
    assert len(dashboards) == 1, (
        f"expected exactly one dashboard in "
        f"{EVIDENCE_NDJSON.relative_to(REPO_ROOT)}, found "
        f"{[d['id'] for d in dashboards]}"
    )
    assert dashboards[0]["id"] == EVIDENCE_DASHBOARD_ID, (
        f"the dashboard's id changed to {dashboards[0]['id']!r}; the URL "
        "scripts/setup-kind.sh prints and docs/architecture.md links both "
        f"name {EVIDENCE_DASHBOARD_ID!r}"
    )
    # The id appears in exactly three places and they are checked against each
    # other, rather than each restating a URL that ages on its own. Stale prose
    # is this repo's other recurring defect; a `#/view/<id>` link that 404s is
    # its worst form, because the reader concludes the demo is broken.
    for document in (SETUP_KIND, ARCHITECTURE):
        assert EVIDENCE_DASHBOARD_ID in document.read_text(), (
            f"{document.relative_to(REPO_ROOT)} does not name "
            f"{EVIDENCE_DASHBOARD_ID!r}, so the link it gives a reader no "
            "longer points at the dashboard this file imports"
        )

    for obj in objects:
        for reference in obj.get("references", []):
            assert (reference["type"], reference["id"]) in by_id, (
                f"{obj['type']}/{obj['id']} references "
                f"{reference['type']}/{reference['id']}, which is not in "
                f"{EVIDENCE_NDJSON.relative_to(REPO_ROOT)} -- the export was "
                "taken without `includeReferencesDeep`, and that panel will "
                "import to an empty frame"
            )

    panels = json.loads(dashboards[0]["attributes"]["panelsJSON"])
    referenced = {r["name"] for r in dashboards[0].get("references", [])}
    assert len(panels) >= 4, f"the evidence view lost panels: {len(panels)}"
    for panel in panels:
        assert panel["panelRefName"] in referenced, (
            f"dashboard panel {panel['panelRefName']!r} has no matching "
            "reference, so it will render as an empty frame"
        )

    index_patterns = [obj for obj in objects if obj["type"] == "index-pattern"]
    assert len(index_patterns) == 1, index_patterns
    attributes = index_patterns[0]["attributes"]
    assert attributes["title"] == EVIDENCE_INDEX, (
        f"the evidence view reads {attributes['title']!r}; the index the "
        f"`opensearch` exporter's ss4o mapping mode writes to is "
        f"{EVIDENCE_INDEX!r} (tests/e2e/test_opensearch_trace_ingest.py's own "
        "TRACE_INDEX)"
    )
    assert attributes["timeFieldName"] == "startTime", attributes
    assert attributes.get("fields"), (
        "the index-pattern carries no cached field list. Measured 2026-09-13: "
        "without it every `min(startTime)` agg on this dashboard throws "
        "`SavedObjectNotFound: Could not locate that index-pattern-field "
        "(id: startTime)` and the panel 400s. Re-export from the UI rather "
        "than hand-trimming the file."
    )


def test_the_evidence_views_header_records_the_permitted_claim_id_choice():
    """Spec §4 left the €5,800 claim's id to the plan; the choice is recorded.

    Both options were defensible -- a fixed fixture id, or a parameterised
    dashboard -- and an *unrecorded* choice is not. The record lives in the
    markdown panel because the file cannot hold a comment (see
    `_evidence_objects`), which also puts it in front of the person reading
    the evidence rather than in front of whoever next opens the export.
    """
    readme = [
        obj
        for obj in _evidence_objects()
        if obj["type"] == "visualization" and obj["id"] == EVIDENCE_README_ID
    ]
    assert len(readme) == 1, (
        f"{EVIDENCE_README_ID!r} is missing from "
        f"{EVIDENCE_NDJSON.relative_to(REPO_ROOT)} -- that panel IS this "
        "file's header and the only place the claim-id choice is recorded"
    )
    markdown = json.loads(readme[0]["attributes"]["visState"])["params"]["markdown"]
    for required in (
        # The choice itself, and the precedent it rests on.
        "parameterise, do not pin a fixture id",
        "test_supervised.py",
        # The constraint the two-step layout exists for.
        "Gap 4",
        "trace id is the join",
    ):
        assert required in markdown, (
            f"the evidence view's header no longer states {required!r}. It is "
            "the only record of why the permitted claim has no fixed id and "
            "why the control selects trace ids rather than claim ids; a "
            "reader who loses it will 'simplify' the dashboard into one that "
            "silently drops the decision and the money spans."
        )


def test_setup_kind_imports_the_evidence_view_at_stage_4():
    """The dashboard is imported from the committed file, at stage 4, checked.

    Three separate claims, because each one has failed somewhere in this repo
    before: the script names the committed file (not some other path), the
    import is inside the stage-4 block (not run unconditionally against a
    cluster that has no Dashboards), and the HTTP response is checked (curl
    exits 0 on a 400, so an import that imported nothing would otherwise leave
    a cluster that looks complete and a Dashboards that is empty).
    """
    setup = _without_comments(SETUP_KIND)
    relative = str(EVIDENCE_NDJSON.relative_to(REPO_ROOT))
    assert relative in setup, (
        f"scripts/setup-kind.sh does not name {relative} outside of a "
        "comment, so the committed evidence view is never imported and the "
        "dashboard exists only wherever someone made it by hand"
    )
    assert "/api/saved_objects/_import" in setup, (
        "setup-kind.sh names the evidence file but never calls the saved "
        "objects import API with it"
    )
    # Review finding F8: `'"success":true' in setup` was a substring test, and
    # it stayed green for an INVERTED check -- `if [[ $X == *'"success":true'* ]];
    # then exit 1; fi` contains the same substring and aborts on success. Bound
    # to the guard's actual shape instead: the negative comparison, and an
    # `exit 1` inside the block it opens.
    guard = re.search(
        r'if \[\[ "\$\{EVIDENCE_IMPORT\}" != \*\'"success":true\'\* \]\]; then(.*?)\n  fi',
        setup,
        re.S,
    )
    assert guard, (
        "the import's response is not checked with a negative comparison "
        "against '\"success\":true'. `curl` exits 0 on an HTTP 400, so without "
        "that check the script reports a complete stage-4 cluster while "
        "Dashboards holds nothing."
    )
    assert "exit 1" in guard.group(1), (
        "the import-response check exists but does not abort: the block it "
        f"opens is {guard.group(1)!r}. A failed import that only prints is a "
        "stage-4 cluster that looks complete and has no evidence view in it."
    )

    # The readiness poll finding F1 added, bound the same way: `--wait` on the
    # Helm install is a TCP-accept probe (measured), so the saved-objects API
    # can still answer 503 when the import runs.
    assert "/api/status" in setup, (
        "the import no longer waits for OpenSearch Dashboards' /api/status to "
        "answer before posting. The chart's readiness probe is `tcpSocket` on "
        "5601 (measured 2026-09-13 on the deployed object), which says nothing "
        "about whether the saved-objects API is serving -- OSD answers 503 "
        "'OpenSearch Dashboards server is not ready yet' for a window after "
        "the socket binds, and a single unretried import would `exit 1` "
        "mid-stage-4."
    )

    stage_4_block = re.search(
        r'if \[\[ "\$\{STAGE\}" -ge 4 \]\]; then(.*?)\nfi\n', setup, re.S
    )
    assert stage_4_block, (
        "could not find the `STAGE -ge 4` block in the comment-stripped "
        "setup-kind.sh, so this test cannot tell whether the import is gated"
    )
    assert relative in stage_4_block.group(1), (
        "the evidence-view import is not inside the `STAGE -ge 4` block. "
        "Below stage 4 there is no OpenSearch Dashboards to import into, so an "
        "ungated import fails every `--stage 1|2|3` run -- finding F10's shape "
        "applied to a saved object."
    )


# --- M4 Task 3 fix round 1: the SHIPPED panels' own fields and queries -------


def _e2e_string_constants(*relative_paths: str) -> dict[str, str]:
    """Module-level `NAME = "literal"` assignments from `tests/e2e` files, by AST.

    Read rather than imported, for the reason every hermetic guard in this file
    is: importing a `tests/e2e` module would pull in `httpx`, the Jaeger harness
    and a pile of fixtures, and would make a unit test depend on the live
    cluster's test surface. The same `ast` idiom `_pds_attribute_keys` uses.
    """
    constants: dict[str, str] = {}
    for relative in relative_paths:
        tree = ast.parse((REPO_ROOT / relative).read_text())
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    constants.setdefault(target.id, node.value.value)
    return constants


def _panel_fields_and_query(visualization: dict) -> tuple[set[str], str]:
    """Every index field a visualization queries, and its own KQL query string."""
    vis_state = json.loads(visualization["attributes"]["visState"])
    fields = {
        agg["params"]["field"]
        for agg in vis_state.get("aggs", [])
        if agg.get("params", {}).get("field")
    }
    fields |= {
        control["fieldName"]
        for control in vis_state.get("params", {}).get("controls", [])
        if control.get("fieldName")
    }
    source = json.loads(
        visualization["attributes"]["kibanaSavedObjectMeta"]["searchSourceJSON"]
    )
    return fields, (source.get("query") or {}).get("query", "")


def test_the_shipped_panels_query_the_fields_the_evidence_tests_name():
    """Review finding F4: assert the SAVED OBJECT's queries, not a copy of them.

    `tests/e2e/test_evidence_view.py` re-implements the two-step join in Python
    and asserts the instrumentation. That is what the brief asks for and it is
    valuable -- but a re-implementation can agree with itself perfectly while
    the shipped dashboard drifts. Before this test, changing panel 4's query off
    `payments-api`, mistyping a rule field in panel 2, or deleting the money
    panel outright left every test in this repository green while the dashboard
    rendered blank or wrong. That is the exact failure
    `test_evidence_view.py`'s own docstring says it exists to prevent, applied
    to the artefact instead of the data.

    Every name below is **derived from the e2e file's own constants**, read out
    of its source by AST rather than restated here, so a renamed attribute has
    to move the test file and the saved object in the same commit or this goes
    red.

    Cross-checking each field against the index-pattern's cached `fields` list
    also closes the implementer's own concern 1 ("a *new* attribute will not
    appear ... Nothing detects that today"): a panel that queries a field the
    cached list does not carry now reddens here.
    """
    constants = _e2e_string_constants(
        "tests/e2e/test_evidence_view.py",
        "tests/e2e/test_policy_denial.py",
        "tests/e2e/test_opensearch_trace_ingest.py",
        "tests/e2e/test_supervised.py",
    )

    def keyword(attribute: str) -> str:
        return f"attributes.{attribute}.keyword"

    expected_fields = {
        "northstar-evidence-controls": {"traceId.keyword"},
        "northstar-evidence-claims": {
            keyword(constants["ATTR_CLAIM_ID"]),
            "traceId.keyword",
        },
        "northstar-evidence-decisions": {
            keyword(constants["ATTR_POLICY_TOOL"]),
            keyword(constants["ATTR_DECISION"]),
            keyword(constants["ATTR_POLICY_RULE"]),
            keyword(constants["ATTR_POLICY_CALLER"]),
            f"attributes.{constants['ATTR_POLICY_AMOUNT']}",
            f"attributes.{constants['ATTR_POLICY_LIMIT']}",
        },
        "northstar-evidence-review": {
            keyword(constants["ATTR_REVIEW_DECISION"]),
            keyword(constants["ATTR_REVIEW_REVIEWER"]),
        },
        "northstar-evidence-money": {"name.keyword", "traceId.keyword"},
    }
    expected_queries = {
        "northstar-evidence-controls": "",
        "northstar-evidence-claims": "",
        "northstar-evidence-decisions": f'name:"{constants["DECISION_SPAN"]}"',
        "northstar-evidence-review": f'name:"{constants["REVIEW_DECISION_SPAN"]}"',
        "northstar-evidence-money": (
            f'resource.service.name:"{constants["PAYMENTS_API"]}"'
        ),
    }

    objects = {(o["type"], o["id"]): o for o in _evidence_objects()}
    index_pattern = objects[("index-pattern", "northstar-evidence-traces")]
    assert index_pattern["attributes"]["title"] == constants["TRACE_INDEX"], (
        "the dashboard reads a different index than "
        "tests/e2e/test_opensearch_trace_ingest.py's TRACE_INDEX: "
        f"{index_pattern['attributes']['title']!r} vs "
        f"{constants['TRACE_INDEX']!r}"
    )
    cached = {field["name"] for field in json.loads(index_pattern["attributes"]["fields"])}

    for panel_id, fields in expected_fields.items():
        panel = objects.get(("visualization", panel_id))
        assert panel is not None, (
            f"panel {panel_id!r} is not in "
            f"{EVIDENCE_NDJSON.relative_to(REPO_ROOT)} at all"
        )
        actual_fields, actual_query = _panel_fields_and_query(panel)
        missing = fields - actual_fields
        assert not missing, (
            f"panel {panel_id!r} no longer queries {sorted(missing)}. Those "
            "names come from tests/e2e/test_evidence_view.py's own constants, "
            "so either the dashboard drifted or an attribute was renamed in "
            "one place and not the other -- and the panel now renders blank or "
            f"wrong. It queries: {sorted(actual_fields)}"
        )
        assert actual_query == expected_queries[panel_id], (
            f"panel {panel_id!r}'s query is {actual_query!r}, expected "
            f"{expected_queries[panel_id]!r}. A panel filtered on the wrong "
            "span name or service renders rows that are not evidence of "
            "anything, and no e2e test can see it -- they read the index "
            "directly, not this saved object."
        )
        uncached = {field for field in actual_fields if field not in cached}
        assert not uncached, (
            f"panel {panel_id!r} queries {sorted(uncached)}, which the "
            "index-pattern's cached field list does not carry, so OpenSearch "
            "Dashboards cannot resolve them and the aggregation 400s "
            "(measured: `SavedObjectNotFound: Could not locate that "
            "index-pattern-field`). Refresh the field list in the UI and "
            "re-export."
        )


def test_the_evidence_view_ships_exactly_its_six_panels():
    """Review finding F5: `>= 4` let two panels vanish silently.

    `len(panels) >= 4` was satisfied by five, so the money panel -- the half of
    the story a claim-id filter drops, and the reason the join has two steps at
    all -- could be deleted without a single test noticing. The panels are now
    named, in the order the dashboard lays them out.
    """
    dashboards = [o for o in _evidence_objects() if o["type"] == "dashboard"]
    assert len(dashboards) == 1, dashboards
    dashboard = dashboards[0]
    by_name = {r["name"]: r["id"] for r in dashboard.get("references", [])}
    panels = json.loads(dashboard["attributes"]["panelsJSON"])
    shipped = [by_name[panel["panelRefName"]] for panel in panels]
    assert shipped == [
        "northstar-evidence-readme",
        "northstar-evidence-controls",
        "northstar-evidence-claims",
        "northstar-evidence-decisions",
        "northstar-evidence-review",
        "northstar-evidence-money",
    ], (
        f"the evidence view's panels are now {shipped}. Each one is load-bearing "
        "-- the header carries the only record of the claim-id choice, the "
        "control is the Gap 4 join key, and panels 3 and 4 are the two whose "
        "CONTENTS (a reviewer, or none) and whose EXISTENCE (the money hops a "
        "claim-id filter drops) are the evidence. Add a panel deliberately by "
        "adding it here too."
    )


def test_the_surface_table_and_start_demos_printed_list_agree():
    """Review finding F3: prose that sent the reader to a shorter list.

    `docs/architecture.md`'s "See it yourself" table told a reader to prefer
    `scripts/start-demo.sh`'s printed output when the two disagree -- and that
    output named neither the Customer UI nor Jaeger, rows 1 and 3 of the table,
    which are the two URLs the walkthrough's first two steps open. Both were
    added to the script; this is what stops them diverging again, in either
    direction, rather than a third sentence asserting that they do not.

    **M4 final review, finding M2: "in either direction" was a claim this test
    did not implement, and the divergence it would have caught already
    existed.** The body was `for url in urls: assert url in printed` -- table ->
    script only. M4 Task 2 added `Prometheus: http://localhost:9090` to
    `start-demo.sh`; Task 3 then wrote this guard in the one direction that
    could not see it, and wrote the docstring above; `architecture.md`'s table
    went the rest of the milestone with no Prometheus row while the prose under
    it asserted the property this test lacked. An inert guard whose docstring
    claims otherwise is the class M4 found eight times, reproduced inside the
    guard built to end it.

    The reverse direction is not "every printed URL is in the table": the
    script deliberately prints agent and tool ports the walkthrough never
    opens. So each printed line declares `#surface` or `#tool-port`, and this
    requires **set equality** between the `#surface` lines and the table. A
    printed URL carrying neither marker fails -- which is what makes this
    bidirectional rather than merely two-way, because a future echo cannot
    escape by being unclassified.
    """
    table = re.search(
        r"^## See it yourself\n\n\| Surface \| URL \|\n\|---\|---\|\n((?:\|.*\n)+)",
        ARCHITECTURE.read_text(),
        re.M,
    )
    assert table, (
        "could not find the 'See it yourself' surface table in "
        f"{ARCHITECTURE.relative_to(REPO_ROOT)} -- it was renamed or "
        "reformatted, and this guard cannot read it"
    )
    urls = re.findall(r"(http://localhost:\d+)", table.group(1))
    assert len(urls) >= 4, f"only {len(urls)} surfaces parsed out of the table"
    printed = START_DEMO.read_text()
    for url in urls:
        assert url in printed, (
            f"{ARCHITECTURE.relative_to(REPO_ROOT)}'s surface table lists {url} "
            "and scripts/start-demo.sh never prints it, while the prose under "
            "that table tells the reader to prefer the script's output. One of "
            "the two has to change."
        )

    # --- the other direction, and the classification that makes it possible ---
    echoed = re.findall(
        r'^\s*echo "[^"]*(http://localhost:\d+)[^"]*"\s*(#\S+)?\s*$', printed, re.M
    )
    assert echoed, (
        "no `echo \"... http://localhost:N ...\"` line was parsed out of "
        "scripts/start-demo.sh. The summary block was reformatted and this "
        "guard has gone blind rather than found agreement -- which is exactly "
        "the failure mode it was rewritten to stop."
    )
    unmarked = sorted({url for url, marker in echoed if marker not in ("#surface", "#tool-port")})
    assert not unmarked, (
        f"scripts/start-demo.sh prints {unmarked} with no `#surface` or "
        "`#tool-port` marker. Every URL this script hands an operator is "
        "either a documented walkthrough surface (add it to "
        f"{ARCHITECTURE.relative_to(REPO_ROOT)}'s 'See it yourself' table and "
        "mark the line `#surface`) or deliberately undocumented (mark it "
        "`#tool-port`). Skipping the choice is how Prometheus stayed out of "
        "that table for four tasks."
    )
    surfaces = {url for url, marker in echoed if marker == "#surface"}
    assert surfaces == set(urls), (
        "the surface table and start-demo.sh's `#surface` lines disagree.\n"
        f"  printed as a surface but not in the table: {sorted(surfaces - set(urls))}\n"
        f"  in the table but not printed as a surface: {sorted(set(urls) - surfaces)}\n"
        "The prose under that table tells the reader to prefer the script's "
        "output and claims a test requires the two to agree in either "
        "direction. This is that test."
    )


# --- M4 Task 5: two operator-facing strings that kept going stale ------------

SPECS_DIR = REPO_ROOT / "docs/superpowers/specs"
README = REPO_ROOT / "README.md"
CONTRACT = REPO_ROOT / "docs/telemetry-contract.md"


def _current_milestone() -> str:
    """The milestone this repo is on, derived from the newest design spec.

    Not a constant. `scripts/smoke-test.sh`'s banner has now been renamed three
    times by hand -- `8c0e980` took it from M2.2 to M3, M3 Task 5B's commit
    message called that rename "the same stale-prose class the final review
    found thirteen times", and it then sat at M3 for the whole of M4 while the
    gate grew four new e2e files' worth of M4 assertions. The specs are named
    `<date>-northstar-<milestone>-<name>-design.md` and sort by date, so the
    milestone is a fact this directory already knows and nobody has to retype.
    """
    specs = sorted(SPECS_DIR.glob("*-northstar-m*-design.md"))
    assert specs, f"no design specs found in {SPECS_DIR.relative_to(REPO_ROOT)}"
    match = re.search(r"-northstar-(m[0-9.]+)-", specs[-1].name)
    assert match, f"cannot read a milestone out of {specs[-1].name}"
    return match.group(1).upper()


def test_the_gates_banner_names_the_milestone_it_actually_proves():
    """The gate announces which milestone a green run signs off on, and it has
    to be the current one.

    A gate that prints `=== Northstar M3 smoke test ===` while running M4's
    OpenSearch ingest, Prometheus, evidence-view and policy-score assertions
    misreports what a passing run proved -- which is the precise reason
    `8c0e980` renamed it from M2.2 in the first place, and the reason its own
    comment gives. That comment then noted "no test pins either string". This
    is that test, and it derives the expected milestone rather than restating
    it, so the next milestone does not have to remember either.
    """
    milestone = _current_milestone()
    script = SMOKE_TEST.read_text()
    banner = re.search(r"=== Northstar (\S+) smoke test ===", script)
    assert banner, (
        "scripts/smoke-test.sh no longer prints a `=== Northstar <milestone> "
        "smoke test ===` banner; this guard cannot tell what a green run "
        "claims to prove"
    )
    signoff = re.search(r"\bNorthstar (\S+) smoke test passed\.", script)
    assert signoff, (
        "scripts/smoke-test.sh no longer prints a `<milestone> smoke test "
        "passed.` sign-off"
    )
    assert banner.group(1) == milestone and signoff.group(1) == milestone, (
        f"scripts/smoke-test.sh announces {banner.group(1)!r} and signs off as "
        f"{signoff.group(1)!r}, but the newest design spec in "
        f"{SPECS_DIR.relative_to(REPO_ROOT)} is {milestone}'s. A gate that "
        "names an older milestone tells an operator that a green run proved "
        "less than it did -- and this is the third time the pair has gone "
        "stale. Update both strings; the expected value is derived, so nothing "
        "else needs touching."
    )


def test_the_bare_run_failure_names_the_command_that_fixes_it():
    """A bare `pytest tests/` fails without port-forwards, deliberately (see
    the contract's "The first run of `pytest tests/`" section). The ruling that
    leaves it failing is only defensible while the remedy is one command away
    in the two documents a reader has open, so the remedy is pinned rather than
    trusted to survive an edit.
    """
    readme = README.read_text()
    contract = CONTRACT.read_text()
    assert "The first run of `pytest tests/`" in contract, (
        "docs/telemetry-contract.md no longer carries the section that rules "
        "on the bare-run failure. If the ruling changed, change this guard "
        "with it; do not drop the ruling silently."
    )
    for name, text in (("README.md", readme), ("docs/telemetry-contract.md", contract)):
        assert "./scripts/start-demo.sh" in text, (
            f"{name} no longer names ./scripts/start-demo.sh, which is the "
            "single remedy for the ConnectError failures a bare `pytest "
            "tests/` produces without port-forwards. Leaving that run failing "
            "was ruled acceptable *because* the fix is named where the reader "
            "is; without the name it is just a broken first run."
        )
