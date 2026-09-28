"""The vendored proto's published sha256, recomputed rather than trusted.

`proto/ext_mcp.proto` is the single source of truth for the ExtMCP wire
contract: `gen-stubs.sh` generates `ext_mcp_pb2*.py` from it at dev time and
the Dockerfile runs the same script at build time, so every message this
service parses and every result it returns is shaped by this one file. Its
header block publishes the upstream tag it was fetched at and the sha256 of
the upstream bytes.

Without this test, nothing recomputes that digest. An edit to the vendored
proto -- a field renumbered, a field made non-optional, a message removed --
would be invisible to every suite in this repository: the stubs would
regenerate happily from the edited file, the service would keep passing its
own tests against its own new shape, and the published pin would go on
claiming agentgateway v1.5.0. In a repo that guards the published test
counts, the attribute inventory and the SPIFFE ID for exactly this reason,
this would be the one published constant with nothing behind it.

**What this test does and does not prove.** It proves the bytes below the
header still hash to the digest the header publishes -- i.e. that nobody
edited the contract without also re-stating what it is. It cannot prove the
digest matches what upstream serves at tag v1.5.0: that needs the network, and
a test that reaches out to raw.githubusercontent.com would fail offline for a
reason that has nothing to do with this repository. Refreshing the pin is the
documented manual step in the header itself, and the review that accompanies
it is where upstream's bytes get compared. So a coordinated edit to both the
proto and the digest still passes here -- and is exactly the reviewable event
this guard exists to force, instead of a silent one.
"""
import hashlib
import pathlib
import re

PROTO_PATH = pathlib.Path(__file__).resolve().parents[1] / "proto" / "ext_mcp.proto"

# The digest line in the header block, e.g.
#   // sha256:  4ba750cc...
# Read from the file rather than restated here: a constant asserted against a
# copy of itself asserts nothing, and this repo has the same rule everywhere
# else (tests/e2e/test_policy_denial.py loads rules.py by path rather than
# copying its threshold).
PUBLISHED_DIGEST_RE = re.compile(rb"^//\s*sha256:\s*([0-9a-f]{64})\s*$", re.MULTILINE)


def _published_digest(raw: bytes) -> str:
    match = PUBLISHED_DIGEST_RE.search(raw)
    assert match, (
        f"{PROTO_PATH} has no '// sha256:  <64 hex chars>' line in its header "
        "block. That line IS the pin -- deleting it does not make the vendored "
        "proto unpinned, it makes the pin unverifiable, so this is a failure "
        "rather than a skip. Restore it per the header's own refresh "
        "instructions."
    )
    return match.group(1).decode()


def _upstream_bytes(raw: bytes) -> bytes:
    """The file with its vendoring header removed.

    The header says the digest is "of the upstream bytes, i.e. this file with
    this comment block removed", and upstream's file starts at its `syntax =
    "proto3";` line. So: drop leading `//` comment lines and blank lines, and
    return everything from the first line that is neither. Derived from the
    file's own shape rather than from a hardcoded line number, so adding or
    removing a line of header prose does not move the goalposts.
    """
    lines = raw.split(b"\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith(b"//"):
            return b"\n".join(lines[index:])
    raise AssertionError(
        f"{PROTO_PATH} contains nothing but comments and blank lines -- there "
        "are no upstream bytes to hash"
    )


def test_the_vendored_protos_bytes_match_the_sha256_its_header_publishes():
    raw = PROTO_PATH.read_bytes()
    published = _published_digest(raw)
    actual = hashlib.sha256(_upstream_bytes(raw)).hexdigest()
    assert actual == published, (
        "proto/ext_mcp.proto's contents no longer match the sha256 its own "
        f"header publishes:\n  published: {published}\n  actual:    {actual}\n"
        "This file is VENDORED and marked DO NOT EDIT: it is the single source "
        "of truth for the ExtMCP wire contract that gen-stubs.sh and the "
        "Dockerfile both generate from. Either revert the edit, or "
        "-- if this is a deliberate refresh to a new upstream tag -- follow the "
        "header's own instructions: re-fetch the URL at the new tag, replace "
        "everything below the header, and update the tag, the fetch date and "
        "this digest."
    )
