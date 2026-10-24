#!/usr/bin/env bash
# Rebuild every venv and regenerate every span dump the write-up cites
# (docs/research/genai-instrumentation-options.md).
# Token-free and cluster-free: the model is common/fake_anthropic.py behind a
# MockTransport, and the MCP server is a local stdio stub.
#
#   VENVS=/some/scratch/dir ./reproduce.sh
#
# Needs `uv`. Does not touch agents/settlement-agent/requirements.txt.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
REQ="$REPO/agents/settlement-agent/requirements.txt"
VENVS="${VENVS:-$HERE/.venvs}"
mkdir -p "$VENVS"

mk() {  # mk NAME [extra pip args...]  -- settlement-agent's pins plus extras
  local name=$1; shift
  [ -x "$VENVS/$name/bin/python" ] || uv venv -q -p 3.13 "$VENVS/$name"
  VIRTUAL_ENV="$VENVS/$name" uv pip install -q -r "$REQ" pyyaml "$@"
}

mk pai                                   # the repo as pinned: pydantic-ai-slim 2.42.0, anthropic 1.4.0
mk oi openinference-instrumentation-anthropic==3.1.0 openinference-instrumentation-pydantic-ai==0.1.30
VIRTUAL_ENV="$VENVS/oi" uv pip install -q anthropic==1.13.0
mk ollm opentelemetry-instrumentation-anthropic==0.62.4
mk otelgenai opentelemetry-instrumentation-genai-anthropic==1.3b0

# pydantic-ai 2.60.0: the repo's pins with the pydantic-ai family left free.
grep -v '^#' "$REQ" | grep -v -E '^(pydantic-ai-slim|pydantic-graph|anthropic|genai-prices|httpx2|httpcore2|logfire-api|griffelib)' \
  | grep -v '^\s*$' > "$VENVS/req-pai260.txt"
[ -x "$VENVS/pai260/bin/python" ] || uv venv -q -p 3.13 "$VENVS/pai260"
VIRTUAL_ENV="$VENVS/pai260" uv pip install -q -r "$VENVS/req-pai260.txt" 'pydantic-ai-slim[anthropic]==2.60.0' pyyaml

# Strands: its own dependency set (anthropic<1.0, mcp<2.2 -- both conflict with the repo's pins).
[ -x "$VENVS/strands/bin/python" ] || uv venv -q -p 3.13 "$VENVS/strands"
VIRTUAL_ENV="$VENVS/strands" uv pip install -q 'strands-agents[anthropic,otel]==1.62.0' pyyaml

PY="$VENVS/pai/bin/python" bash "$HERE/pydantic_ai_audit/run-all.sh"
rm -rf "$HERE/pydantic_ai_audit/out-2.60.0"
"$VENVS/pai260/bin/python" "$HERE/pydantic_ai_audit/run.py" --out "$HERE/pydantic_ai_audit/out-2.60.0" > /dev/null
PY_OI="$VENVS/oi/bin/python" PY_OLLM="$VENVS/ollm/bin/python" PY_OTEL="$VENVS/otelgenai/bin/python" \
  bash "$HERE/pydantic_ai_audit/run-instrumentors.sh" > /dev/null
rm -rf "$HERE/strands_prototype/out"
for mode in legacy latest latest-attrs; do
  "$VENVS/strands/bin/python" "$HERE/strands_prototype/run.py" --semconv "$mode" > /dev/null
  "$VENVS/pai/bin/python" "$HERE/common/compare.py" "$HERE/semconv/spans-flat-9ffc7e0.json" \
    "$HERE/strands_prototype/out/$mode"/*.spans.json > "$HERE/strands_prototype/out/$mode/semconv-diff.json"
done
echo "done"
