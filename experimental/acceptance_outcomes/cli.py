"""CLI: `python -m experimental.acceptance_outcomes record|event|report`. Exit 0 ok, 2 refusal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experimental.acceptance_outcomes.ledger import add_event, record_call
from experimental.acceptance_outcomes.report import build_report, format_report
from experimental.acceptance_outcomes.store import OutcomesError

EVENT_ARGS = ("start", "end", "source", "finder", "root", "finding_id", "confirmer", "outcome",
              "evidence")  # fmt: skip


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acceptance_outcomes", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("record", "event", "report"):
        p = sub.add_parser(name)
        p.add_argument("--ledger", type=Path, required=True)
    rec = sub.choices["record"]
    rec.add_argument("--result", type=Path, required=True, help="JSON a verify call printed")
    for opt in ("repo", "base", "candidate", "version"):
        rec.add_argument(f"--{opt}", required=True)
    rec.add_argument("--test-commit", default="")
    rec.add_argument("--notary-db", type=Path)
    rec.add_argument("--notary-lost", default="", metavar="REASON",
                     help="Core result whose notary database is gone (no --notary-db)")  # fmt: skip
    rec.add_argument("--call-ref", default="", help="tells apart identical outputs of two calls")
    ev = sub.choices["event"]
    ev.add_argument("--call", required=True)
    ev.add_argument("--type", required=True)
    for opt in EVENT_ARGS:
        ev.add_argument(f"--{opt.replace('_', '-')}")
    ev.add_argument("--clones", default="", help="comma-separated declared clones")
    ev.add_argument("--checks-done", action="store_true")
    rep = sub.choices["report"]
    rep.add_argument("--cases", action="store_true")
    rep.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "report":
            report = build_report(args.ledger)
            print(
                json.dumps(report, sort_keys=True)
                if args.json
                else format_report(report, cases=args.cases)
            )
            return 0
        if args.command == "record":
            try:
                result = json.loads(args.result.read_text())
            except (OSError, ValueError) as exc:
                raise OutcomesError("OUTCOME_INVALID", f"result unreadable: {exc}") from exc
            if not isinstance(result, dict):
                raise OutcomesError("OUTCOME_INVALID", "result must be a JSON object")
            inputs = {k: getattr(args, k) for k in ("repo", "base", "candidate", "version")}
            inputs["test_commit"], inputs["call_ref"] = args.test_commit, args.call_ref
            print(record_call(args.ledger, result, inputs, args.notary_db, args.notary_lost))
            return 0
        event = {k: getattr(args, k) for k in EVENT_ARGS if getattr(args, k) is not None}
        event["type"], event["clones"] = args.type, [c for c in args.clones.split(",") if c]
        if args.type == "closed":
            event["checks_done"] = args.checks_done
        print(add_event(args.ledger, args.call, event))
        return 0
    except OutcomesError as exc:
        print(str(exc))
        return 2
