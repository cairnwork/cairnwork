"""Reclaim old, reconstructible contents of local worker run roots."""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from experimental.local_worker.run_root_cleanup import CLAIM, eligible

RECLAIM_DIRS = frozenset({"checkout", "venv", "tmp", "q", ".cache", "__pycache__", ".pytest_cache"})


@dataclass(frozen=True)
class RootResult:
    root: str
    outcome: str
    bytes_reclaimed: int = 0


@dataclass(frozen=True)
class Report:
    roots: tuple[RootResult, ...]

    @property
    def bytes_reclaimed(self) -> int:
        return sum(row.bytes_reclaimed for row in self.roots)

    @property
    def reclaimed(self) -> int:
        return sum(row.outcome == "reclaimed" for row in self.roots)


def _lsof_in_use(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "-t", "+D", str(root)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return bool(result.stdout.strip()) or bool(result.returncode and result.stderr.strip())
    except (OSError, subprocess.SubprocessError):
        return True


def _latest_mtime(fd: int) -> float:
    latest = 0.0
    with os.scandir(fd) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            latest = max(latest, info.st_mtime)
            if entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
                try:
                    latest = max(latest, _latest_mtime(child))
                finally:
                    os.close(child)
    return latest


def _tree_size(fd: int) -> int:
    total = 0
    with os.scandir(fd) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            total += info.st_size
            if entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
                try:
                    total += _tree_size(child)
                finally:
                    os.close(child)
    return total


def _plan(root: Path, cutoff: float, now: float) -> RootResult | list[tuple[str, int]]:
    """Open the root, decide skip or the rebuildable dirs (name, size); the fd is closed on return
    so the later in-use probe cannot see this process's own handle."""
    try:
        fd = os.open(root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError:
        return RootResult(root.name, "unavailable")
    try:
        with os.scandir(fd) as entries:
            names = [entry.name for entry in entries]
        if CLAIM in names:
            return RootResult(root.name, "claimed")
        if now - _latest_mtime(fd) < cutoff:
            return RootResult(root.name, "recent")
        candidates: list[tuple[str, int]] = []
        for name in names:
            if name not in RECLAIM_DIRS:
                continue
            if not stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                continue
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
            try:
                candidates.append((name, _tree_size(child)))
            finally:
                os.close(child)
        return candidates
    finally:
        os.close(fd)


def _delete(root: Path, candidates: list[tuple[str, int]]) -> int:
    fd = os.open(root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    done = 0
    try:
        for name, size in candidates:
            shutil.rmtree(name, dir_fd=fd)  # never follows a link
            done += size
    finally:
        os.close(fd)
    return done


def _reclaim_root(
    root: Path,
    runs: Path,
    cutoff: float,
    now: float,
    in_use: Callable[[Path], bool],
    dry_run: bool,
) -> RootResult:
    if not eligible(root, runs):
        return RootResult(root.name, "ineligible")
    try:
        plan = _plan(root, cutoff, now)
        if isinstance(plan, RootResult):
            return plan
        if not plan:  # nothing rebuildable left: no lsof, no work
            return RootResult(root.name, "nothing")
        if in_use(root):
            return RootResult(root.name, "in_use")
        if dry_run:
            return RootResult(root.name, "would_reclaim", sum(size for _, size in plan))
        return RootResult(root.name, "reclaimed", _delete(root, plan))
    except OSError:
        return RootResult(root.name, "error")
    except Exception as exc:  # noqa: BLE001  injected probes must fail closed per root
        return RootResult(root.name, f"error ({type(exc).__name__})")


def reclaim_stale(
    runs: Path,
    older_than_hours: float,
    now: float | None = None,
    in_use: Callable[[Path], bool] = _lsof_in_use,
    exclude: Path | None = None,
    dry_run: bool = False,
) -> Report:
    """Reclaim only old eligible roots; all failures are reported and isolated per root."""
    try:
        runs = runs.expanduser().absolute()
        if (
            not math.isfinite(older_than_hours)
            or older_than_hours <= 0
            or os.path.realpath(runs) != str(runs)
        ):
            return Report(())
        current = time.time() if now is None else now
        if not math.isfinite(current):
            return Report(())
        cutoff = older_than_hours * 3600
        rows = []
        for root in runs.iterdir():
            if exclude is not None and root == exclude:
                rows.append(RootResult(root.name, "excluded"))
            else:
                rows.append(_reclaim_root(root, runs, cutoff, current, in_use, dry_run))
        return Report(tuple(rows))
    except OSError:
        return Report(())
    except Exception:  # noqa: BLE001  all top-level faults are contained by maintenance
        return Report(())


def add_cli_option(parser: object) -> None:
    parser.add_argument("--reclaim-older-than-hours", type=float, default=48)


def reclaim_for_episode(runs: Path, hours: float, root: Path) -> None:
    # Best-effort: reclamation can never change this episode's outcome.
    with contextlib.suppress(Exception):
        report = reclaim_stale(runs, hours, exclude=root)
        print(f"LOCAL_WORKER_RECLAIM {report.reclaimed} {report.bytes_reclaimed}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Reclaim old local worker run roots.")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--older-than-hours", type=float, default=48)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = reclaim_stale(args.runs, args.older_than_hours, dry_run=args.dry_run)
    for row in report.roots:
        print(f"{row.root!r} {row.outcome} {row.bytes_reclaimed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
