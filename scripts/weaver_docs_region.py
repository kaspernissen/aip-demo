#!/usr/bin/env python3
"""Keep the generated region of docs/telemetry-contract.md current.

    weaver_docs_region.py --check DOC GENERATED_DIR   exit 1 with a diff on mismatch
    weaver_docs_region.py --write DOC GENERATED_DIR   replace the region

The region sits between the BEGIN and END markers; its content is the file
`attributes.md` that `weaver registry generate` wrote into GENERATED_DIR.
"""
import difflib
import sys
from pathlib import Path

BEGIN = "<!-- BEGIN weaver-generated -->"
END = "<!-- END weaver-generated -->"


def split(doc: str):
    b, e = doc.find(BEGIN), doc.find(END)
    if b < 0 or e < 0 or e < b:
        sys.exit(f"markers not found or out of order: {BEGIN} ... {END}")
    return doc[: b + len(BEGIN)], doc[b + len(BEGIN) : e], doc[e:]


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("--check", "--write"):
        sys.exit(__doc__)
    mode, doc_path, gen_dir = argv[1], Path(argv[2]), Path(argv[3])
    generated = (gen_dir / "attributes.md").read_text().strip("\n")
    doc = doc_path.read_text()
    head, current, tail = split(doc)
    wanted = f"\n\n{generated}\n\n"
    if mode == "--write":
        doc_path.write_text(head + wanted + tail)
        return 0
    if current == wanted:
        return 0
    sys.stderr.write("".join(difflib.unified_diff(
        current.splitlines(True), wanted.splitlines(True),
        "committed region", "generated region")))
    sys.stderr.write("\nrun: python3 scripts/weaver_docs_region.py --write "
                     "docs/telemetry-contract.md .demo/weaver-docs\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
