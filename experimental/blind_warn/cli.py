"""CLI for the blind-test warning line: prints one JSON object; always exits 0 after parsing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experimental.blind_warn.judge import DEFAULT_MODEL, run_blind_warn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blind_warn", description=__doc__)
    parser.add_argument("--ticket", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate-diff", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = run_blind_warn(
        args.ticket, args.base, args.candidate_diff, args.state, model=args.model
    )
    text = json.dumps(result, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
