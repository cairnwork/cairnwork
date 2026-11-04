"""CLI: `python -m experimental.acceptance_casebook add|list`. Exit 0 ok/DUPLICATE, 2 refusal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experimental.acceptance_casebook.store import (
    CasebookError,
    add_case,
    eligible_cases,
    list_cases,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acceptance_casebook", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "list"):
        p = sub.add_parser(name)
        p.add_argument("--casebook", type=Path, required=True)
        p.add_argument("--json", action="store_true")
    lst = sub.choices["list"]
    lst.add_argument("--notary-db", type=Path, help="re-check each case's original evidence")
    lst.add_argument("--eligible", action="store_true", help="only confirmed, re-checked cases")
    add = sub.choices["add"]
    add.add_argument("--notary-db", type=Path, required=True)
    add.add_argument("--task-id", required=True)
    add.add_argument("--attempt-id", required=True)
    add.add_argument("--candidate", required=True)
    add.add_argument("--base", required=True)
    add.add_argument("--version", required=True)
    add.add_argument("--kind", required=True)
    add.add_argument("--found-by", required=True)
    add.add_argument("--evidence-file", required=True, help="JSON: candidate, task_id, ...")
    return parser


def _out(args: argparse.Namespace, payload: dict[str, object], text: str) -> None:
    print(json.dumps(payload, sort_keys=True) if args.json else text)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "list":
            if args.eligible and args.notary_db is None:
                parser_error = "--eligible needs --notary-db"
                _out(args, {"refused": "CASE_INVALID"}, f"CASE_INVALID: {parser_error}")
                return 2
            db = args.notary_db
            cases = (
                eligible_cases(args.casebook, db)
                if args.eligible
                else list_cases(args.casebook, db)
            )
            _out(args, {"cases": cases}, "\n".join(json.dumps(c, sort_keys=True) for c in cases))
            return 0
        case = {
            "candidate": args.candidate,
            "base": args.base,
            "version": args.version,
            "task_id": args.task_id,
            "attempt_id": args.attempt_id,
            "kind": args.kind,
            "found_by": args.found_by,
            "evidence_file": args.evidence_file,
        }
        result = add_case(args.casebook, args.notary_db, case)
        _out(args, {"result": result}, result)
        return 0
    except CasebookError as exc:
        _out(args, {"refused": exc.code}, str(exc))
        return 2
