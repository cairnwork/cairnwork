"""PAUSE's entry point (#476):

    python -m experimental.pause.cli run --task task.json --checkout <run_root>/checkout ...

Without ``--planner-config`` (the flag off, the default) the arguments go unchanged to
``python -m experimental.local_worker.cli run``: the same episode, the same output, no cloud
request and no PAUSE receipt (AC1). ``--planner-config`` is refused until steps 2 and 3 wire the
planner, the gate and the resume."""

from __future__ import annotations

import sys
from collections.abc import Callable

from experimental.local_worker import cli as worker_cli

FLAG = "--planner-config"
USAGE = (
    "usage: python -m experimental.pause.cli run [--planner-config FILE] <local_worker run args>"
)


def main(
    argv: list[str] | None = None, worker_main: Callable[[list[str]], int] | None = None
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["run"]:
        print(USAGE, file=sys.stderr)
        return 2
    if any(a == FLAG or a.startswith(FLAG + "=") for a in args[1:]):
        print("PAUSE_REFUSED: the planner is not wired yet (#476 steps 2-3)", file=sys.stderr)
        return 2
    return (worker_main or worker_cli.main)(args)


if __name__ == "__main__":
    sys.exit(main())
