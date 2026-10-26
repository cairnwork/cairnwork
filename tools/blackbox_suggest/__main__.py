"""CLI: python -m tools.blackbox_suggest --project ROOT TEST_FILE... (from a cairnwork checkout).

Reads the test files, their conftest files and the project's modules statically (nothing is
imported or run) and prints a JSON report of every acceptance obligation: its draft blackbox
cases or the reason it cannot be declared. --markdown writes a reviewable list, --declaration a
draft declaration file (mode 0600, new file only) of what this cairnwork version can declare.
Exit 0 on success, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ALL_KINDS, KINDS_NOW, analyze_file, default_roots
from .render import declaration, markdown, report, write_declaration


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.blackbox_suggest", description=__doc__)
    ap.add_argument("--project", required=True, type=Path, help="the project's checkout root")
    ap.add_argument(
        "--source-root",
        action="append",
        type=Path,
        default=None,
        help="where project modules live (default: ROOT/src and ROOT)",
    )
    ap.add_argument(
        "--kinds",
        default=",".join(sorted(KINDS_NOW)),
        help=f"declarable kinds to count as protected (any of {sorted(ALL_KINDS)})",
    )
    ap.add_argument("--json", type=Path, help="write the report here instead of stdout")
    ap.add_argument("--markdown", type=Path, help="also write a reviewable markdown list")
    ap.add_argument("--declaration", type=Path, help="also write a draft declaration (0600)")
    ap.add_argument("tests", nargs="+", type=Path)
    args = ap.parse_args(argv)
    kinds = {k for k in args.kinds.split(",") if k}
    if not kinds <= ALL_KINDS:
        ap.error(f"unknown kinds: {sorted(kinds - ALL_KINDS)}")
    root = args.project
    roots = args.source_root or default_roots(root)
    obligations = []
    for test in args.tests:
        if not test.is_file() or root.resolve() not in test.resolve().parents:
            ap.error(f"{test}: not a file inside --project")
        obligations += analyze_file(test, root, roots)
    data = report(obligations, kinds)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.markdown:
        args.markdown.write_text(markdown(data), encoding="utf-8")
    if args.declaration:
        draft = declaration(obligations)
        if draft is None:
            print("no declarable obligation: no declaration written", file=sys.stderr)
        else:
            write_declaration(args.declaration, draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
