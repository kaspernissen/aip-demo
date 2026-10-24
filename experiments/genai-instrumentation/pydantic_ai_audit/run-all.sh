#!/usr/bin/env bash
# Run the PydanticAI audit in the three configurations the write-up compares,
# then diff the shipped one (v5, content on) against the semconv.
#
#   PY=/path/to/venv/bin/python ./run-all.sh
#
# The venv is settlement-agent's own pins:
#   uv venv -p 3.13 .venv && VIRTUAL_ENV=.venv uv pip install -r agents/settlement-agent/requirements.txt pyyaml
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python}"
OUT="${OUT:-$HERE/out}"
rm -rf "$OUT"
"$PY" "$HERE/run.py" --version 5 --content on --out "$OUT"
"$PY" "$HERE/run.py" --version 6 --content on --out "$OUT" > /dev/null
"$PY" "$HERE/run.py" --version 5 --content off --out "$OUT" > /dev/null
"$PY" "$HERE/../common/compare.py" "$HERE/../semconv/spans-flat-9ffc7e0.json" \
  "$OUT"/v5-content-on/*.spans.json > "$OUT/v5-content-on/semconv-diff.json"
echo "diff: $OUT/v5-content-on/semconv-diff.json"
