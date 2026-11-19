"""Opt-in local relay command. Runtime construction is injected at the process boundary."""

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


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cairnwork-local-relay")
    ap.add_argument("--state", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--clone", required=True)
    ap.add_argument("--gh", default=str(Path.home() / ".config/nexus-agent-bot/bin/nexus-agent-gh"))
    ap.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    ap.add_argument("command", choices=("run-once",))
    return ap


def main(
    argv: list[str] | None = None,
    *,
    run_once: Callable[[argparse.Namespace, JsonlLedger], dict[str, Any]] | None = None,
) -> int:
    args = parser().parse_args(argv)
    if os.environ.get(FLAG) != "1":
        return _emit({"error": "RELAY_DISABLED", "detail": f"set {FLAG}=1 to enable"}, 2)
    if run_once is None:
        return _emit(
            {"error": "RUNTIME_NOT_CONFIGURED", "detail": "relay dependencies must be injected"},
            3,
        )
    try:
        result = run_once(args, JsonlLedger(Path(args.ledger).expanduser()))
    except Exception as exc:  # noqa: BLE001 - an unconfigured or failed runtime is never success
        return _emit({"error": "RUN_FAILED", "detail": f"{type(exc).__name__}: {exc}"}, 1)
    return _emit(result, 0)


def _emit(value: dict[str, Any], code: int) -> int:
    print(json.dumps(value, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
