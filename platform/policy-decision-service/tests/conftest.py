"""Refuse to run at all without the generated ExtMCP stubs.

`ext_mcp_pb2.py` / `ext_mcp_pb2_grpc.py` are git-ignored and generated from
the vendored proto by `./gen-stubs.sh`. Their absence is a broken build and
must look like one.

Deliberately *not* a `pytest.importorskip` or a `skipif`: a skip does not fail
a run. This project has already been burned by exactly that shape -- deleting a
live network policy turned four e2e tests into skips and left the suite green.
Raising from a conftest fails the whole session with a usage error, which is
the loudest thing pytest can do, and prints the one command that fixes it.
"""

_HOW = (
    "\n\n"
    "The generated ExtMCP gRPC stubs are missing.\n"
    "Generate them from the vendored proto, then re-run:\n"
    "\n"
    "    cd platform/policy-decision-service && ./gen-stubs.sh\n"
    "\n"
    "They are git-ignored on purpose: proto/ext_mcp.proto is the single\n"
    "source of truth, and committing the stubs would let the image\n"
    "and the repo disagree. The Dockerfile runs the same script at build time.\n"
)

try:
    import ext_mcp_pb2  # noqa: F401
    import ext_mcp_pb2_grpc  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised by deleting the stubs
    raise RuntimeError(str(exc) + _HOW) from exc
