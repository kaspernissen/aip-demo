"""Guards for what stage 4 actually ships.

Stage 4 has no stage4.yaml: it runs on stage 3's cluster config unchanged,
and its UIs are reached by port-forward via `scripts/start-demo.sh`.

Several guards bind to a script's actual content in the comment-stripped
file, so a comment or help text cannot keep them green.

The strip tests run `setup-kind.sh`'s own sed program, read out of the
script, and check the result is a valid collector config that still exports
traces to Jaeger, keeps the `prometheus/spire` receiver, and names no
Prometheus exporter below stage 4.
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts/setup-kind.sh"
COLLECTOR_CONFIGS = [
    REPO_ROOT / "deploy/observability/collector-config.yaml",
]


def _without_comments(path: Path) -> str:
    """`path` with the shebang and every whole-line comment removed.

    The same tool `tests/unit/test_stage2_linkerd_config.py::_effective_script`
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


# --- No stage4.yaml, and --stage 4 still creates a cluster ------------------


def test_stage_4_reuses_the_stage_3_cluster_config():
    """No stage4.yaml exists, deliberately, and `--stage 4` is wired to stage
    3's kind config.

    Host port mappings can only be added at cluster CREATION time
    (`extraPortMappings`), so stage 4 runs on stage 3's cluster and reaches
    its UIs by port-forward instead.

    The second assertion binds to the actual conditional and assignment
    together, in the comment-stripped script, rather than to a loose
    `"stage3.yaml" in setup` substring test -- the filename also appears in
    the help text, in comments and in an `echo`, so a substring test can stay
    green while the mapping itself is deleted: `--stage 4` would then fall
    through to `deploy/kind/stage${STAGE}.yaml`, fail the `[[ -f ... ]]`
    check with "deploy/kind/stage4.yaml not found", and be unable to bring a
    cluster up at all.

    Stages 5, 6 and 7 share the same reasoning and the same cluster config --
    nothing any of them installs needs a new host port, so the conditional
    covers all four: `if [[ "${STAGE}" -eq 4 || "${STAGE}" -eq 5 ||
    "${STAGE}" -eq 6 || "${STAGE}" -eq 7 ]]; then`. This test's regex
    matches that combined form specifically, not a generic `-eq 4` fragment, so an edit that split it
    back into separate `if` blocks, or that dropped any one disjunct while
    leaving the others, reddens here rather than passing on a coincidental
    substring match.
    """
    assert not (REPO_ROOT / "deploy/kind/stage4.yaml").exists(), (
        "deploy/kind/stage4.yaml exists. Stage 4 was deliberately built on "
        "stage 3's cluster config because extraPortMappings is create-time "
        "only. If a recreate has since been authorised, delete this test in "
        "the same commit that adds the file, and say who authorised it."
    )
    assert not (REPO_ROOT / "deploy/kind/stage5.yaml").exists(), (
        "deploy/kind/stage5.yaml exists. Stage 5 was deliberately built on "
        "stage 3's cluster config too (nothing it installs needs a new host "
        "port). If a recreate has since been authorised, delete this test in "
        "the same commit that adds the file, and say who authorised it."
    )
    assert not (REPO_ROOT / "deploy/kind/stage6.yaml").exists(), (
        "deploy/kind/stage6.yaml exists. Stage 6 was deliberately built on "
        "stage 3's cluster config too -- Backstage is reached by "
        "port-forward, not a kind host port. If a recreate has since been "
        "authorised, delete this test in the same commit that adds the file, "
        "and say who authorised it."
    )
    assert not (REPO_ROOT / "deploy/kind/stage7.yaml").exists(), (
        "deploy/kind/stage7.yaml exists. Stage 7 was deliberately built on "
        "stage 3's cluster config too -- Kyverno is reached by nothing from "
        "the host. If a recreate has since been authorised, delete this test "
        "in the same commit that adds the file, and say who authorised it."
    )
    setup = _without_comments(SETUP_KIND)
    mapping = re.search(
        r'if \[\[ "\$\{STAGE\}" -eq 4 \|\| "\$\{STAGE\}" -eq 5 '
        r'\|\| "\$\{STAGE\}" -eq 6 \|\| "\$\{STAGE\}" -eq 7 \]\]; then\s*\n'
        r'\s*KIND_CONFIG="\$ROOT/deploy/kind/stage3\.yaml"\s*\n'
        r"\s*fi",
        setup,
    )
    assert mapping, (
        "scripts/setup-kind.sh does not map `--stage 4`, `--stage 5`, "
        "`--stage 6` and `--stage 7` onto deploy/kind/stage3.yaml via one combined condition. "
        "Without that mapping KIND_CONFIG falls through to "
        "deploy/kind/stage${STAGE}.yaml, which must never exist for any of "
        "the four, so the affected `--stage N` cannot create a cluster at "
        "all -- it dies at the `[[ -f \"$KIND_CONFIG\" ]]` check. (A loose "
        "`\"stage3.yaml\" in setup` substring test would stay green through "
        "that deletion, because the filename appears elsewhere too -- bind "
        "to the actual mapping instead.)"
    )


# --- No Prometheus exporter in the collector config below stage 4 ---------


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
        "collector naming a Prometheus that cluster does not have"
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
        "stage-4-only regions -- the Prometheus metrics exporter -- or no "
        "stage does"
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
def test_below_stage_4_the_collector_config_names_no_prometheus_exporter(config):
    """No Prometheus exporter below stage 4.

    The stage-4-only region is the `otlp_http/prometheus` exporter and its
    entry in the `metrics` pipeline. Remove or misplace either marker pair and a
    `--stage 1|2|3` cluster deploys a collector exporting metrics to
    `prometheus.observability.svc.cluster.local:9090`, a Service that does
    not exist.

    **Why this is not `"prometheus" not in stripped`.** The metrics pipeline
    legitimately keeps its `prometheus/spire` RECEIVER below stage 4 (SPIRE is
    scraped at every stage), so the broad word would false-positive on a
    correct config. The assertion is the exporter id, and then the same
    declared-ness walk the traces pipeline already gets.
    """
    stripped = subprocess.run(
        ["sed", _stage4_strip_expression(), str(config)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "otlp_http/prometheus" not in stripped, (
        f"stripping the stage-4-only regions of {config.name} leaves "
        "`otlp_http/prometheus` behind, so a stage-1/2/3 cluster would deploy "
        "a collector exporting metrics to a Prometheus it does not have. "
        "Both the exporter's declaration and its entry in the `metrics` "
        "pipeline's exporter list must sit between `# BEGIN stage-4-only` and "
        "`# END stage-4-only` -- which is why that list is written "
        "one-per-line."
    )
    parsed = yaml.safe_load(stripped)
    metrics = parsed["service"]["pipelines"]["metrics"]
    assert "debug" in metrics["exporters"], (
        f"the stripped {config.name}'s metrics pipeline exports nowhere at "
        "all. The strip took too much: below stage 4 the collector must keep "
        f"exporting metrics the way a pre-stage-4 cluster does. Exporters "
        f"left: {metrics['exporters']}"
    )
    assert "prometheus/spire" in metrics["receivers"], (
        f"the stripped {config.name}'s metrics pipeline lost the "
        "`prometheus/spire` receiver, which is not stage-4-only -- SPIRE is "
        "scraped at every stage"
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
    """The same structural check as the test above, one message over.

    The `Stage 4 (Observable) is up: ...` line is the last thing an operator
    reads, and it is where they learn what they now have. Restating an
    install list in prose is fragile -- a block that grows a new release
    can silently outpace a hand-written summary -- so the list is derived
    instead: every `helm upgrade --install <release>` between the
    `STAGE -ge 4` guard and that block's closing `fi` must be named in the
    message.

    Release names are matched case-insensitively, because the message spells
    them as products and the script as Helm releases; a release whose name is genuinely absent from the message
    fails regardless of case.

    **"Installs" covers more than Helm.** A workload that is not a chart (a
    component built, pushed and applied by `scripts/rebuild-and-deploy.sh`)
    is invisible to a helm-only derivation, so components deployed through
    `rebuild-and-deploy.sh` *inside this block* are derived too (there are
    none today). Only inside it: the unconditional
    `rebuild-and-deploy.sh payments-api ...` line deploys the stage-1
    workloads at every stage, and requiring the stage-4 message to name those
    would be requiring it to restate the whole system.
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
    **Nesting is the subtler case**: merely counting BEGINs and ENDs and
    zipping them would let `BEGIN, BEGIN, END, END` pass, while `sed` actually
    pairs `(BEGIN1 ... END1)` and leaves `BEGIN2` deleted and `END2` stranded
    in the output -- a different deletion than the file intends. Walking the
    markers in order and tracking depth is what actually models `sed`'s range
    behaviour, so that is what this does.
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


# --- The SHIPPED config must declare what its pipelines name ---------------


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_the_shipped_collector_config_declares_every_component_it_names(config):
    """Every component a pipeline names is declared, in the config as shipped.

    The strip tests check the stripped output; this checks the stage-4 config
    itself. A connector, if one is added, must be used on both sides.
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
