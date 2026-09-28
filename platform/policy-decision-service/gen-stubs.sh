#!/usr/bin/env bash
# Generate the ExtMCP gRPC stubs from the vendored proto.
#
# The generated `ext_mcp_pb2.py` / `ext_mcp_pb2_grpc.py` are git-ignored and
# produced by this script -- at dev time by hand, and at Docker
# build time by the Dockerfile, which runs this same file. Committing them would
# let the image and the repo disagree about a contract whose only source of
# truth is `proto/ext_mcp.proto` (vendored at agentgateway v1.5.0).
#
# The stubs land in this directory, flat, because protoc emits
# `import ext_mcp_pb2` inside `ext_mcp_pb2_grpc.py` -- so the directory holding
# them must be on `sys.path`. It is: `python -m pytest` and `python -m
# pds.server` are both run from here, and both put the working directory on the
# path.
#
# Their absence is a broken build, not a reason to skip tests: `tests/conftest.py`
# refuses to load without them and prints this command.
#
#   Usage: ./gen-stubs.sh [python]
#
# Defaults to this service's own `.venv/bin/python`, which carries the pinned
# grpcio-tools. Docker passes `python`, the image's interpreter.
set -euo pipefail

cd "$(dirname "$0")"
PYTHON="${1:-.venv/bin/python}"

"$PYTHON" -m grpc_tools.protoc \
  --proto_path=proto \
  --python_out=. \
  --grpc_python_out=. \
  proto/ext_mcp.proto

echo "generated: ext_mcp_pb2.py ext_mcp_pb2_grpc.py"
