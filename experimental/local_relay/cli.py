"""Opt-in local relay command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from experimental.local_relay.config import DEFAULT_LEDGER_PATH, FLAG
from experimental.local_relay.ledger import JsonlLedger
from experimental.local_relay.runtime import build_run_once, dry_route
from experimental.local_worker.run_root_cleanup import INSTALLED_RUNS


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cairnwork-local-relay")
    ap.add_argument("--state", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--clone", required=True)
    ap.add_argument("--gh", default=str(Path.home() / ".config/nexus-agent-bot/bin/nexus-agent-gh"))
    ap.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    ap.add_argument("--runs", type=Path, default=INSTALLED_RUNS)
    ap.add_argument("--fixture", type=Path, help="run one task described by fixture repo JSON")
    ap.add_argument("--canary-report", type=Path)
    ap.add_argument("--dry-route", action="store_true")
    ap.add_argument("command", choices=("run-once",))
    return ap


def main(
    argv: list[str] | None = None,
    *,
    run_once: Callable[[argparse.Namespace, JsonlLedger], dict[str, Any]] | None = None,
) -> int:
    args = parser().parse_args(argv)
    if args.dry_route:
        return _emit(dry_route(args), 0)
    if os.environ.get(FLAG) != "1":
        return _emit({"error": "RELAY_DISABLED", "detail": f"set {FLAG}=1 to enable"}, 2)
    try:
        ledger = JsonlLedger(Path(args.ledger).expanduser())
        result = (run_once or build_run_once(args))(args, ledger)
    except Exception as exc:  # noqa: BLE001 - an unconfigured or failed runtime is never success
        return _emit({"error": "RUN_FAILED", "detail": f"{type(exc).__name__}: {exc}"}, 1)
    return _emit(result, 0)


def _emit(value: dict[str, Any], code: int) -> int:
    print(json.dumps(value, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
