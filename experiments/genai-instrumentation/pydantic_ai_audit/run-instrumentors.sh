#!/usr/bin/env bash
# Run the three Anthropic client instrumentors on the same offline harness,
# each alongside PydanticAI's own spans and each alone. One venv per library,
# each built from settlement-agent's requirements.txt plus that one package
# (all three resolve against the repo's pins without changing any):
#
#   PY_OI     + openinference-instrumentation-anthropic==3.1.0 openinference-instrumentation-pydantic-ai==0.1.30
#             + anthropic==1.13.0 (OpenInference refuses anthropic<1.8.0 at instrument()
#               time with a DependencyConflict and emits nothing; 1.13.0 is the last
#               release before anthropic's own native tracing arrived in 1.14.0)
#   PY_OLLM   + opentelemetry-instrumentation-anthropic==0.62.4   (OpenLLMetry / Traceloop)
#   PY_OTEL   + opentelemetry-instrumentation-genai-anthropic==1.3b0 (OpenTelemetry upstream)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/out-instrumentors}"
SPEC="$HERE/../semconv/spans-flat-9ffc7e0.json"
rm -rf "$OUT"
run() {
  local py=$1; shift
  "$py" "$HERE/run.py" --out "$OUT" "$@" > /dev/null
}
run "$PY_OI" --extra openinference
run "$PY_OI" --pydantic off --extra openinference
OPENINFERENCE_ENABLE_GENAI_SEMCONV=true \
  "$PY_OI" "$HERE/run.py" --out "$OUT/oi-genai-semconv-flag" --pydantic off --extra openinference > /dev/null
mv "$OUT/oi-genai-semconv-flag/pydantic-off-content-on+openinference" "$OUT/pydantic-off-content-on+openinference+genai-flag"
rmdir "$OUT/oi-genai-semconv-flag"
run "$PY_OI" --extra openinference-processor
run "$PY_OLLM" --extra openllmetry
run "$PY_OLLM" --pydantic off --extra openllmetry
run "$PY_OTEL" --extra otel-genai
run "$PY_OTEL" --pydantic off --extra otel-genai
for d in "$OUT"/*/; do
  "$PY_OI" "$HERE/../common/compare.py" "$SPEC" "$d"*.spans.json > "$d/semconv-diff.json"
  echo "== $(basename "$d")"; cat "$d/happy.tree.txt"
done
