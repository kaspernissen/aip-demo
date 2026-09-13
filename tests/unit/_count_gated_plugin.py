"""Counts `--collect-only` items gated behind `RUN_BROWSER_TESTS=1`.

Loaded with `-p _count_gated_plugin` by
`tests/unit/test_telemetry_contract_counts.py::test_the_contracts_gated_figure_is_what_pytest_would_actually_skip`,
which is the only caller. It prints one line and asserts nothing itself; the
test that reads that line requires it to be present, so a plugin that failed to
load is a failure rather than a count of zero.

Named with a leading underscore and no `test_` prefix so pytest's own
collection never picks it up as a test module.
"""
import collections
import pathlib


def pytest_collection_modifyitems(session, config, items):
    per_file = collections.Counter()
    total = 0
    for item in items:
        for mark in item.iter_markers(name="skipif"):
            if "RUN_BROWSER_TESTS" not in str(mark.kwargs.get("reason", "")):
                continue
            # The condition as pytest would evaluate it now -- this process runs
            # with RUN_BROWSER_TESTS removed from the environment, so a true
            # condition means "a bare `pytest tests/` would skip this".
            if mark.args and mark.args[0]:
                per_file[pathlib.Path(str(item.fspath)).name] += 1
                total += 1
            break
    print(f"GATED-COUNT {total} {dict(sorted(per_file.items()))}")
