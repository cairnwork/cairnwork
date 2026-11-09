"""Experimental adapter from Cairnwork's AgyWorker argv to local_worker."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

RUNS = Path("/Users/Shared/cairnwork-worker/local-runs")
OUT = Path.home() / ".local/state/cairnwork-88/episodes"
CANARY = OUT / "canary/local-worker-canary-current.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
EDIT_LINE = re.compile(r"(?m)^- Edit only these files: (.+)$")


def parse_args(argv: list[str]) -> tuple[str, str, int] | None:
    if len(argv) != 9 or argv[0] != "-p" or argv[2] != "--model":
        return None
    if argv[4:8] != ["--mode", "accept-edits", "--sandbox", "--print-timeout"]:
        return None
    if not argv[3] or not argv[1] or not argv[-1].endswith("s"):
        return None
    try:
        timeout = int(argv[-1][:-1])
    except ValueError:
        return None
    if timeout < 61:
        return None
    match = EDIT_LINE.search(argv[1])
    if not match:
        return None
    files = match.group(1).split(", ")
    if not files or any(not _safe_relative(p) for p in files):
        return None
    return argv[1], argv[3], timeout


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\n" not in value


def _run(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _allowed(prompt: str) -> list[str] | None:
    match = EDIT_LINE.search(prompt)
    if not match:
        return None
    paths = match.group(1).split(", ")
    return paths if paths and all(_safe_relative(p) for p in paths) else None


def _copy_checkout(cwd: Path, checkout: Path) -> bool:
    result = _run(
        ["/usr/bin/git", "clone", "--quiet", "--no-hardlinks", str(cwd), str(checkout)],
        cwd=cwd,
    )
    return result.returncode == 0


def _prepare_run_root(run_root: Path) -> bool:
    for name in ("tmp", "q"):
        (run_root / name).mkdir(mode=0o770)
    venv = run_root / "venv"
    created = _run(["uv", "venv", "-q", "--python", sys.executable, str(venv)], cwd=run_root)
    if created.returncode:
        return False
    installed = _run(
        ["uv", "pip", "install", "-q", "--python", str(venv / "bin/python"), "pytest"],
        cwd=run_root,
    )
    return installed.returncode == 0


def _visible_tests(repo: Path) -> list[str]:
    return ["tests"] if (repo / "tests").is_dir() else ["."]


def _safe_copy_back(checkout: Path, cwd: Path, paths: list[str]) -> bool:
    for rel in paths:
        source, target = checkout / rel, cwd / rel
        if not _safe_relative(rel) or source.is_symlink() or target.is_symlink():
            return False
        if any(p.is_symlink() for p in source.parents if p != checkout.parent) or any(
            p.is_symlink() for p in target.parents if p != cwd.parent
        ):
            return False
        if not source.exists():
            if target.exists():
                target.unlink()
            continue
        if not source.is_file():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target, follow_symlinks=False)
    return True


def _snapshot(checkout: Path, paths: list[str]) -> dict[str, bytes | None] | None:
    snapshot: dict[str, bytes | None] = {}
    for rel in paths:
        path = checkout / rel
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != checkout.parent):
            return None
        if path.exists() and not path.is_file():
            return None
        snapshot[rel] = path.read_bytes() if path.exists() else None
    return snapshot


def _has_changes(checkout: Path, before: dict[str, bytes | None]) -> bool:
    for rel, original in before.items():
        path = checkout / rel
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return False
        current = path.read_bytes() if path.exists() else None
        if current != original:
            return True
    return False


def execute(prompt: str, model: str, timeout: int, cwd: Path) -> int:
    allowed = _allowed(prompt)
    if allowed is None:
        return 64
    run_root = RUNS / secrets.token_hex(16)
    checkout = run_root / "checkout"
    run_root.mkdir(parents=True, mode=0o700)
    try:
        if not _prepare_run_root(run_root) or not _copy_checkout(cwd, checkout):
            return 1
        before = _snapshot(checkout, allowed)
        if before is None:
            return 1
        task_path = run_root / "task.json"
        task_path.write_text(
            json.dumps(
                {
                    "task_id": f"agy-{secrets.token_hex(8)}",
                    "repo": "cairnwork",
                    "prompt": prompt,
                    "public_tests": _visible_tests(checkout),
                }
            ),
            encoding="utf-8",
        )
        cli = [
            sys.executable,
            "-m",
            "experimental.local_worker.cli",
            "run",
            "--task",
            str(task_path),
            "--checkout",
            str(checkout),
            "--model",
            model,
            "--context-cap",
            "65536",
            "--canary-report",
            str(CANARY),
            "--max-wall-seconds",
            str(timeout - 60),
            "--runs",
            str(RUNS),
        ]
        for path in allowed:
            cli.extend(("--write-path", path))
        child_env = dict(os.environ)
        child_env["CAIRNWORK_ATTEMPT"] = cwd.name
        child_env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(REPO_ROOT), child_env.get("PYTHONPATH", "")))
        )
        child = subprocess.Popen(
            cli,
            cwd=cwd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        try:
            output, _ = child.communicate(timeout=timeout - 10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            output, _ = child.communicate()
            print(output, end="")
            return 1
        print(output, end="")
        completed = child.returncode == 0 and "LOCAL_WORKER_EPISODE COMPLETED " in output
        if not completed or not _has_changes(checkout, before):
            return 1
        if not _safe_copy_back(checkout, cwd, allowed):
            return 1
        return 0
    finally:
        if run_root.exists():
            shutil.rmtree(run_root)


def main(argv: list[str] | None = None) -> int:
    parsed = parse_args(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        print("agy_shim: unsupported AgyWorker arguments", file=sys.stderr)
        return 64
    prompt, model, timeout = parsed
    return execute(prompt, model, timeout, Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
