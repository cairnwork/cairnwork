"""The real local-worker adapter used by the two-run PAUSE flow."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from experimental.local_worker import cli as worker_cli
from experimental.pause.flow import RunResult
from experimental.pause.git_env import hardened_git_env
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import GuardUnavailable

_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_LINE_RE = re.compile(
    r"LOCAL_WORKER_EPISODE ([A-Z0-9_]+) (ep_[A-Za-z0-9_-]+) (.+) line_sha256=([0-9a-f]{64})\Z"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=hardened_git_env(),
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _copy_access(source: Path, target: Path) -> None:
    """Set target permissions from R0 where paths match and from its root otherwise."""
    source_root = source.lstat()
    group = source_root.st_gid
    root_mode = stat.S_IMODE(source_root.st_mode)
    for dst in (target, *target.rglob("*")):
        rel = dst.relative_to(target)
        own = dst.lstat()
        is_dir = stat.S_ISDIR(own.st_mode)
        is_file = stat.S_ISREG(own.st_mode)
        os.chown(dst, -1, group, follow_symlinks=False)
        if is_dir or is_file:
            source_mode = _matching_mode(source, rel, is_dir)
            if source_mode is not None:
                mode = source_mode
            elif is_dir:
                mode = root_mode
            else:
                mode = (root_mode & 0o666) | (own.st_mode & 0o111)
            os.chmod(dst, mode, follow_symlinks=False)


def _matching_mode(source: Path, relative: Path, want_dir: bool) -> int | None:
    """Return R0's mode only when the path exists beneath real R0 directories."""
    source_entry = source
    source_stat = source.lstat()
    for index, part in enumerate(relative.parts):
        try:
            source_entry = source_entry / part
            source_stat = source_entry.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        is_last = index == len(relative.parts) - 1
        if not is_last and not stat.S_ISDIR(source_stat.st_mode):
            return None
    if (want_dir and stat.S_ISDIR(source_stat.st_mode)) or (
        not want_dir and stat.S_ISREG(source_stat.st_mode)
    ):
        return stat.S_IMODE(source_stat.st_mode)
    return None


def _prepare(r0_checkout: Path, checkout: Path, base: str) -> None:
    if not _SHA_RE.fullmatch(base):
        raise RuntimeError("parent base_sha must be 40 lowercase hex characters")
    checkout.parent.mkdir(
        mode=r0_checkout.parent.stat().st_mode & 0o777, parents=True, exist_ok=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(r0_checkout),
            str(checkout),
        ],
        check=True,
        capture_output=True,
        env=hardened_git_env(),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "checkout",
            "--detach",
            base,
        ],
        check=True,
        capture_output=True,
        env=hardened_git_env(),
    )
    _copy_access(r0_checkout, checkout)
    if _git(checkout, "status", "--porcelain", "--ignored"):
        raise RuntimeError("fresh R1 checkout is not clean")
    if _git(checkout, "rev-parse", "HEAD") != base:
        raise RuntimeError("fresh R1 checkout is not at parent base")


def _parse_worker_output(
    output: str, code: int, root: Path, checkout: Path, out: Path
) -> RunResult:
    if code != 0:
        return RunResult(code, output, root, checkout)
    lines = [line for line in output.splitlines() if line.startswith("LOCAL_WORKER_EPISODE ")]
    if len(lines) != 1:
        return RunResult(code, output, root, checkout)
    match = _LINE_RE.fullmatch(lines[0])
    if match is None:
        return RunResult(code, output, root, checkout)
    _exit_reason, eid, raw_corpus, wanted_sha = match.groups()
    corpus = Path(raw_corpus)
    try:
        if not corpus.resolve().is_relative_to(out.resolve()) or not corpus.is_file():
            return RunResult(code, output, root, checkout)
        records = []
        for line in corpus.read_bytes().splitlines(keepends=True):
            if hashlib.sha256(line).hexdigest() == wanted_sha:
                records.append(json.loads(line))
        transcript = out / "private" / eid / "transcript.json"
        if len(records) != 1 or not isinstance(records[0], dict):
            return RunResult(code, output, root, checkout)
        record = records[0]
        verification = record.get("verification")
        if (
            record.get("episode_id") != eid
            or not isinstance(verification, dict)
            or verification.get("exit_reason") != _exit_reason
            or not transcript.is_file()
        ):
            return RunResult(code, output, root, checkout)
        return RunResult(code, output, root, checkout, transcript, record, eid)
    except (OSError, ValueError, json.JSONDecodeError):
        return RunResult(code, output, root, checkout)


class WorkerRunner:
    """Invoke local_worker for R0 and create a fresh, base-pinned checkout for R1."""

    def __init__(
        self,
        worker_main: Callable[[list[str]], int] = worker_cli.main,
        runs_dir: Path | None = None,
        prepare: Callable[[Path, Path, str], None] = _prepare,
    ) -> None:
        self.worker_main = worker_main
        self.runs_dir = Path(runs_dir) if runs_dir is not None else None
        self.prepare = prepare

    def __call__(
        self,
        mode: str,
        args: list[str],
        *,
        parent: Path | None = None,
        cut: int | None = None,
        plan: str | None = None,
    ) -> RunResult:
        if mode not in ("run", "resume"):
            raise ValueError(f"unknown worker mode {mode!r}")
        parser_args = [mode, *args]
        if mode == "resume":
            parser_args.extend(
                [
                    "--parent-transcript",
                    str(parent or "placeholder"),
                    "--cut-turn",
                    str(cut if cut is not None else 0),
                    "--plan",
                    "placeholder",
                ]
            )
        parsed = worker_cli.parser().parse_known_args(parser_args)[0]
        if parsed.clean_run_root:
            raise ValueError("PAUSE cannot use --clean-run-root because it needs the R0 run root")
        r0_checkout = parsed.checkout.expanduser().resolve()
        out = parsed.out.expanduser().resolve()
        if mode == "run":
            return self._invoke(["run", *args], r0_checkout, out)
        return self._resume(args, parsed, parent, cut, plan, r0_checkout, out)

    def _invoke(self, argv: list[str], checkout: Path, out: Path) -> RunResult:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = self.worker_main(argv)
        return _parse_worker_output(stream.getvalue(), code, checkout.parent, checkout, out)

    def _resume(
        self,
        args: list[str],
        parsed: Any,
        parent: Path | None,
        cut: int | None,
        plan: str | None,
        r0_checkout: Path,
        out: Path,
    ) -> RunResult:
        if parent is None or cut is None or plan is None:
            raise RuntimeError("resume requires parent transcript, cut, and plan")
        try:
            parent_data = json.loads(parent.read_text(encoding="utf-8"))
            base = parent_data["record"]["base_sha"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("parent transcript has no base_sha") from exc
        if type(base) is not str or not _SHA_RE.fullmatch(base):
            raise RuntimeError("parent base_sha must be 40 lowercase hex characters")
        runs_dir = self.runs_dir or parsed.runs.expanduser()
        root = runs_dir / uuid.uuid4().hex
        if root.exists():
            raise RuntimeError("fresh R1 run root already exists")
        root.mkdir(parents=True, mode=r0_checkout.parent.stat().st_mode & 0o777)
        checkout_root_stat = r0_checkout.lstat()
        os.chown(root, -1, checkout_root_stat.st_gid)
        os.chmod(root, stat.S_IMODE(checkout_root_stat.st_mode))
        checkout = root / "checkout"
        self.prepare(r0_checkout, checkout, base)
        try:
            before = TreeGuard(checkout, bind_times=True).digest()
            git_config_sha256 = _git_config_sha256(checkout)
        except (GuardUnavailable, OSError):
            return RunResult(2, "", root, checkout, guard_before_plan=None)
        plan_dir = out / "pause-plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / f"{hashlib.sha256(plan.encode()).hexdigest()}.txt"
        _write_plan(plan_path, plan)
        argv = [
            "resume",
            *args,
            "--checkout",
            str(checkout),
            "--parent-transcript",
            str(parent),
            "--cut-turn",
            str(cut),
            "--plan",
            str(plan_path),
        ]
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            try:
                arrival = TreeGuard(checkout, bind_times=True).digest()
            except GuardUnavailable:
                return RunResult(2, "", root, checkout, guard_before_plan=before)
            if before != arrival:
                return RunResult(
                    2,
                    "",
                    root,
                    checkout,
                    guard_before_plan=before,
                    guard_at_plan_arrival=arrival,
                )
            code = self.worker_main(argv)
        result = _parse_worker_output(stream.getvalue(), code, root, checkout, out)
        return RunResult(
            result.code,
            result.output,
            root,
            checkout,
            result.transcript,
            result.record,
            result.episode_id,
            before,
            arrival,
            base,
            git_config_sha256,
        )


def _write_plan(path: Path, plan: str) -> None:
    """Create a private plan once; an existing digest path must contain identical text."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != plan or path.stat().st_mode & 0o777 != 0o600:
            raise RuntimeError("existing plan file does not match its digest") from None
        return
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(plan)


def _git_config_sha256(checkout: Path) -> str:
    """Hash a regular, non-symlink .git/config or fail closed."""
    config = checkout / ".git" / "config"
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise GuardUnavailable("Git config is not a regular file")
    return hashlib.sha256(config.read_bytes()).hexdigest()
