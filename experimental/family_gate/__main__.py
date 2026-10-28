"""``python -m experimental.family_gate ... run-once``: ``cairnwork-issues run-once`` with the gate.

    python -m experimental.family_gate --state DIR --repo OWNER/NAME --clone PATH [--gh BIN]
        [--worker-model M] [--sidecar DIR] [--enable] run-once

The same start checks, the same ``issues.lock`` and the same take and settle as
``cairnwork.issue_runner.main``. Off by default: without ``--enable`` (or
``CAIRNWORK_FAMILY_GATE=on``) the pass is exactly the plain runner's, with no extra GitHub call
and no sidecar. On, the gate runs inside the lock before ``take``, so a plain runner on the same
state cannot take an Issue in between (it gets BUSY). ``--worker-model`` defaults to the state's
``model``. When the gate cannot run (sidecar unwritable or unreadable, config refused, the queue
listing failed), nothing is refused and ``take`` does not run this pass (settle still does).
Exit 1 on any error, as the plain runner.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import cast

from cairnwork import issue_runner
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.issue_github import GitHub
from cairnwork.issue_publish import require_bot_gh
from cairnwork.lineage import verify_origin
from experimental.family_gate.gate import DEFAULT_SIDECAR, Sidecar, run_gate

ENV = "CAIRNWORK_FAMILY_GATE"


def enabled(flag: bool) -> bool:
    return flag or os.environ.get(ENV, "").strip().lower() == "on"


def one_pass(intake: Intake, github: GitHub, clone: Path, gate: dict | None) -> dict:
    """take and settle as the plain runner does; with ``gate`` the gate runs before take."""
    if gate is None:
        return {"take": issue_runner.take(intake, github, clone),
                "settle": issue_runner.settle(intake, github, clone)}  # fmt: skip
    result: dict = {}
    try:
        model = gate["worker_model"] or intake.config()["model"]
        gated, result["gate"] = run_gate(intake, github, gate["sidecar"], model)
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        IntakeRefused,
    ) as exc:  # no record, no take
        result["gate"] = {"error": f"GATE_NOT_RUN: {type(exc).__name__}: {exc}"}
        result["take"] = []
    else:
        result["take"] = issue_runner.take(intake, cast(GitHub, gated), clone)
    result["settle"] = issue_runner.settle(intake, github, clone)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m experimental.family_gate")
    parser.add_argument("--state", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--clone", required=True)
    parser.add_argument("--gh", default=issue_runner.DEFAULT_GH)
    parser.add_argument("--worker-model", default="")
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR))
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("command", choices=["run-once"])
    args = parser.parse_args(argv)
    intake = Intake(Path(args.state).resolve())
    github = GitHub(args.gh, args.repo)
    clone = Path(args.clone).resolve()
    gate = None
    if enabled(args.enable):
        sidecar = Sidecar(Path(args.sidecar).expanduser().resolve(), args.repo)
        gate = {"worker_model": args.worker_model, "sidecar": sidecar}
    try:  # the plain runner's start checks, in its order
        if problem := intake.clone_problem(clone):
            raise RuntimeError(problem)
        verify_origin(clone, github.repo)
        require_bot_gh(args.gh, github.repo, clone)
    except (RuntimeError, IntakeRefused) as exc:
        print(json.dumps({"error": str(exc).split(":")[0], "detail": str(exc)}))
        return 3
    intake.prepare()
    fd = os.open(intake.state / "issues.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"error": "BUSY", "detail": "another run-once is working"}))
            return 2
        intake.remember_gh(args.gh)
        result = one_pass(intake, github, clone, gate)
    finally:
        os.close(fd)
    print(json.dumps(result, sort_keys=True))
    gate_failed = "error" in result.get("gate", {}) or "errors" in result.get("gate", {})
    errors = [r for key in ("take", "settle") for r in result[key] if "error" in r]
    return 1 if errors or gate_failed else 0


if __name__ == "__main__":
    sys.exit(main())
