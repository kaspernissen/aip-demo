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

The live end-to-end assertions -- span parity between Jaeger and OpenSearch,
the decision span being queryable, and Jaeger keeping the canonical attribute
names -- are in `tests/e2e/test_opensearch_trace_ingest.py`, which needs the
cluster.
"""
import ast
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts/setup-kind.sh"
SMOKE_TEST = REPO_ROOT / "scripts/smoke-test.sh"
START_DEMO = REPO_ROOT / "scripts/start-demo.sh"
COLLECTOR_CONFIGS = [
    REPO_ROOT / "deploy/observability/collector-config.yaml",
    REPO_ROOT / "deploy/observability/collector-config-dash0.yaml",
]
PDS_TELEMETRY = REPO_ROOT / "platform/policy-decision-service/pds/telemetry.py"


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
    catch is the deletion of these three lines in `setup-kind.sh`:

        if [[ "${STAGE}" -eq 4 ]]; then
          KIND_CONFIG="$ROOT/deploy/kind/stage3.yaml"
        fi

    Delete them and `--stage 4` falls through to
    `deploy/kind/stage${STAGE}.yaml`, fails the `[[ -f ... ]]` check with
    "deploy/kind/stage4.yaml not found", and cannot bring a cluster up at all.
    The old assertion -- `"stage3.yaml" in setup` -- stayed green through that
    edit, because the filename also appears in the help text, in two comments
    and in an `echo`. This one binds to the conditional and the assignment
    together, in the comment-stripped script.
    """
    assert not (REPO_ROOT / "deploy/kind/stage4.yaml").exists(), (
        "deploy/kind/stage4.yaml exists. Stage 4 was deliberately built on "
        "stage 3's cluster config (plan amendment A1) because extraPortMappings "
        "is create-time only and the recreate was declined. If a recreate has "
        "since been authorised, delete this test in the same commit that adds "
        "the file, and say who authorised it."
    )
    setup = _without_comments(SETUP_KIND)
    mapping = re.search(
        r'if \[\[ "\$\{STAGE\}" -eq 4 \]\]; then\s*\n'
        r'\s*KIND_CONFIG="\$ROOT/deploy/kind/stage3\.yaml"\s*\n'
        r"\s*fi",
        setup,
    )
    assert mapping, (
        "scripts/setup-kind.sh no longer maps `--stage 4` onto "
        "deploy/kind/stage3.yaml. Without that mapping KIND_CONFIG falls "
        "through to deploy/kind/stage4.yaml, which amendment A1 says must "
        "never exist, so `--stage 4` cannot create a cluster at all -- it dies "
        "at the `[[ -f \"$KIND_CONFIG\" ]]` check. (This assertion used to be "
        "a bare `\"stage3.yaml\" in setup`, which five other mentions of the "
        "filename kept green through exactly that deletion: review finding "
        "F6.)"
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
        "collector naming an OpenSearch that cluster does not have (review "
        "finding F10, ruling M4-R8)"
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
        "OpenSearch branch (finding F10) or no stage does"
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
def test_the_stage_4_markers_are_balanced(config):
    """An unbalanced marker pair deletes to end-of-file or not at all."""
    lines = config.read_text().splitlines()
    begins = [i for i, line in enumerate(lines) if line.strip() == "# BEGIN stage-4-only"]
    ends = [i for i, line in enumerate(lines) if line.strip() == "# END stage-4-only"]
    assert begins, f"{config.name} has no stage-4-only markers at all"
    assert len(begins) == len(ends), (
        f"{config.name} has {len(begins)} `# BEGIN stage-4-only` markers and "
        f"{len(ends)} `# END stage-4-only` -- a sed range with a missing END "
        "deletes everything to the end of the file"
    )
    for begin, end in zip(begins, ends):
        assert begin < end, (
            f"{config.name}: a `# END stage-4-only` at line {end + 1} comes "
            f"before its `# BEGIN` at line {begin + 1}"
        )


# --- Finding F1: the attribute collision, and the processor that fixes it ---


def _pds_attribute_keys() -> set[str]:
    """Every attribute key the PDS declares, parsed out of its own module.

    Parsed with `ast` rather than imported: this is a unit test and must not
    need the PDS's OpenTelemetry dependencies installed in the repo-root venv.
    `pds/telemetry.py` exists precisely so the guard tests and the contract
    cite one source (its own comment says so), so reading it is reading the
    source of truth.
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
    the span sets both `policy.decision` and `policy.decision.retryable`. Of
    the 114 distinct span attribute keys Jaeger held across all ten services
    when this was measured (2026-09-13), that was the only such pair. A second
    one added later would reintroduce the defect silently -- the export fails
    permanently, per document, and only the collector log says so.

    So this derives the collision set from the PDS's own declared attributes
    and requires the `attributes/opensearch-compat` processor to delete each
    colliding key and insert a replacement for it. Add `policy.decision.reason`
    tomorrow and this goes red here, in a unit test, rather than in a bulk
    request nobody is watching.
    """
    collisions = _collisions(_pds_attribute_keys())
    assert collisions, (
        "no dotted-prefix collision found among the PDS's attribute keys. If "
        "the collision was genuinely removed at the source -- e.g. "
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
            assert longer in deleted, (
                f"{config.name}: attribute {longer!r} collides with "
                f"{shorter!r} (OpenSearch would need attributes.{shorter} to "
                "be both a scalar and an object), but the "
                "attributes/opensearch-compat processor does not delete it on "
                "the OpenSearch branch. Every span carrying both keys will be "
                "rejected permanently -- that is review finding F1."
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
if [[ "$*" == *"get svc/"* ]]; then
  for absent in $FAKE_ABSENT_SERVICES; do
    if [[ "$*" == *"svc/$absent"* ]]; then exit 1; fi
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

_FAKE_CURL = "#!/usr/bin/env bash\nexit 0\n"


def _run_start_demo(tmp_path, absent_services: str):
    """Run the real start-demo.sh against fakes and return its output.

    The script ends in `wait`, so it never exits on its own: read until it
    prints its final line, then kill the whole process group (which is this
    test's own group, created with start_new_session, so nothing outside it is
    ever signalled -- the same "kill only what you started" rule the script
    itself follows).
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in {
        "kubectl": _FAKE_KUBECTL, "lsof": _FAKE_LSOF, "curl": _FAKE_CURL,
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
        tmp_path, absent_services="opensearch-dashboards reviewer-ui"
    )
    assert "verified up" in output, (
        "start-demo.sh did not get as far as verifying its surfaces on a "
        "cluster without the stage-gated Services:\n" + output
    )
    for surface in ("OpenSearch Dashboards: SKIPPED", "reviewer-ui: SKIPPED"):
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
