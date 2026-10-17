"""No source file still writes an old, un-prefixed name (spec §3).

Every telemetry name this project owns lives in the `northstar.` namespace
and is defined in telemetry/registry/. The rename wave (M9) changed every
writer and reader at once, with no compatibility period, so a quoted legacy
name anywhere in code, config or a test is a regression.

Deliberate scope, recorded so a reader can judge it:
  * `*.md` is excluded: prose is checked by the docs tests, not here.
  * `telemetry/` is not searched: the registry names retired names in its
    `deprecated`/note fields where it must.
  * this file is excluded (it names the patterns).
  * tests/unit/test_telemetry_contract_attribute_counts.py is excluded: its
    REQUIRED_ATTRIBUTE_SECTIONS markers must equal the headings in
    docs/telemetry-contract.md, whose prose is rewritten by M9 Task 4. Task 4
    changes the headings and the markers together and removes this exclusion.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LEGACY = re.compile(
    r"""['"`](policy|review)\.[a-z_]+(\.[a-z_]+)*['"`]"""
    r"""|['"`]claim\.(submit|wait)['"`]"""
    r"""|['"`]northstar\.policy\.number['"`]"""
)
PATHS = ["agents", "apps", "platform", "tools", "systems", "scripts", "deploy", "tests"]
EXCLUDES = [":!**/node_modules/**", ":!*.md", ":!tests/unit/test_no_legacy_names.py",
            ":!tests/unit/test_telemetry_contract_attribute_counts.py"]


def _grep(pattern, paths):
    return subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", *paths, *EXCLUDES],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout


def test_no_source_writes_a_legacy_name():
    out = _grep(LEGACY.pattern, PATHS)
    assert out == "", out


def test_backstage_plugin_no_longer_writes_k8s_resource_name():
    # The autonomy-contract span names the flag with northstar.contract.flag.name;
    # `k8s.resource.name` was a semconv name carrying a non-Kubernetes meaning.
    out = _grep(
        r"""['"`]k8s\.resource\.name['"`]""",
        ["platform/backstage/packages/backend/src/plugins/autonomyContract"],
    )
    assert out == "", out


def test_approval_histogram_uses_registry_label_keys():
    # The histogram's attribute keys were `decision` and `autonomy.level`;
    # they are northstar.review.decision and northstar.policy.autonomy.level.
    # `"decision":` is only matched in approval_metrics.py: elsewhere it is the
    # reviewer HTTP body's JSON key, which is not telemetry.
    out = _grep(r"""['"`]autonomy\.level['"`]""", PATHS)
    out += _grep(r"""['"`]decision['"`] *:""", ["apps/reviewer-ui/approval_metrics.py"])
    assert out == "", out
