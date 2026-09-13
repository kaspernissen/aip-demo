"""M4 Task 1, plan amendment A1: guards what actually shipped for stage 4,
not the `deploy/kind/stage4.yaml` the original Steps 1-3 were written to
create.

The human partner declined a cluster recreate on 2026-09-12 -- Dashboards'
fifth host port mapping can only be added at cluster CREATION
(`extraPortMappings`), the same create-time rule that already governs
`disableDefaultCNI` (see tests/unit/test_stage2_cilium_config.py). So stage 4
runs on stage 3's cluster config unchanged, and OpenSearch Dashboards is
reached by port-forward via `scripts/start-demo.sh` (plan amendment A2)
instead. The two guards below pin exactly those two facts:

1. No `stage4.yaml` exists, and `setup-kind.sh` still has something to bring
   a stage-4 cluster up FROM (stage3.yaml).
2. The one document an operator reads when a port-forward is dead
   (`smoke-test.sh`'s own failure message) names the script that owns every
   forward, not a `kubectl port-forward` line assembled by hand -- which is
   exactly the pattern rulings R10 and R20 came from.

Both were mutation-tested before being trusted (see the Task 1 report for the
two mutations and their failures): reverting the remedy text to a bare
`kubectl port-forward` line turns the second test red, and creating
`deploy/kind/stage4.yaml` turns the first test red.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_stage_4_reuses_the_stage_3_cluster_config():
    """Amendment A1: no stage4.yaml exists, deliberately.

    The human partner declined a cluster recreate on 2026-09-12, so stage 4 runs
    on stage 3's cluster and reaches OpenSearch Dashboards by port-forward. A
    stage4.yaml appearing later means someone recreated the cluster without
    asking -- which is a standing constraint, not a preference.
    """
    assert not (REPO_ROOT / "deploy/kind/stage4.yaml").exists(), (
        "deploy/kind/stage4.yaml exists. Stage 4 was deliberately built on "
        "stage 3's cluster config (plan amendment A1) because extraPortMappings "
        "is create-time only and the recreate was declined. If a recreate has "
        "since been authorised, delete this test in the same commit that adds "
        "the file, and say who authorised it."
    )
    setup = (REPO_ROOT / "scripts/setup-kind.sh").read_text()
    assert "stage3.yaml" in setup, (
        "setup-kind.sh no longer references stage3.yaml, so --stage 4 has "
        "nothing to bring a cluster up from"
    )


def test_the_port_forward_remedy_points_at_the_script():
    """Step A2: smoke-test.sh tells the operator to run start-demo.sh.

    Mutation-check: change the remedy text back to a bare kubectl line and
    confirm this goes red rather than skipped.
    """
    smoke = (REPO_ROOT / "scripts/smoke-test.sh").read_text()
    assert "start-demo.sh" in smoke, (
        "smoke-test.sh's port-forward failure message must name "
        "./scripts/start-demo.sh -- assembling a kubectl port-forward by hand is "
        "what rulings R10/R20 came from"
    )
    assert (REPO_ROOT / "scripts/start-demo.sh").exists(), (
        "scripts/start-demo.sh is referenced but missing"
    )
