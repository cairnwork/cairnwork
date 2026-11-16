"""Read-only measurement for #951: among recent commits that touch test files, how often does the
same commit change an existing module that a base test file (the parent commit) imports, and is
that module a shared test helper (under tests/) or something else. Uses ``git log`` and
``git grep`` only; samples at most --limit commits per repo.

    uv run python scripts/measure_base_test_imports.py REPO [REPO ...] [--limit 300]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import PurePosixPath

DEADLINE_SECONDS = 540
MAX_MODULES_PER_COMMIT = 25


def git(repo: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    return done.stdout if done.returncode in (0, 1) else ""


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix == ".py" and (
        p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py"
    )


def in_tests_dir(path: str) -> bool:
    return any(part in ("tests", "test") for part in PurePosixPath(path).parts[:-1])


def sample(repo: str, limit: int) -> list[str]:
    out = git(repo, "log", "--no-merges", "--format=%H", "--", "tests", "test", "*test_*.py")
    return out.split()[:limit]


def changed(repo: str, sha: str) -> list[tuple[str, str]]:
    out = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "-M", f"{sha}^", sha)
    rows = [line.split("\t") for line in out.splitlines() if "\t" in line]
    return [(r[0][0], r[-1]) for r in rows]


def classify(path: str) -> str:
    return "helper_under_tests" if in_tests_dir(path) else "outside_tests"


def imported_by_base_test(repo: str, parent: str, module: str) -> bool:
    name = re.escape(module)
    end = "([^A-Za-z0-9_]|$)"  # POSIX classes: macOS git grep has no backslash-s or -b
    dot = "[A-Za-z0-9_.]+"
    pattern = (
        f"^[[:space:]]*(import[[:space:]]+({dot}[[:space:]]*,[[:space:]]*)*({dot}[.])?{name}{end}"
        f"|from[[:space:]]+({dot}[.])?{name}{end})"
    )
    out = git(repo, "grep", "-l", "-E", pattern, parent, "--", "*.py")
    return any(is_test_file(line.split(":", 1)[-1]) for line in out.splitlines())


def measure(repo: str, limit: int, deadline: float) -> Counter:
    stats: Counter = Counter()
    for sha in sample(repo, limit):
        if time.monotonic() > deadline:
            stats["stopped_by_deadline"] += 1
            break
        stats["commits"] += 1
        rows = changed(repo, sha)
        mods = [p for st, p in rows if st in "MDR" and p.endswith(".py") and not is_test_file(p)]
        hits = {
            p
            for p in mods[:MAX_MODULES_PER_COMMIT]
            if imported_by_base_test(repo, f"{sha}^", PurePosixPath(p).stem)
        }
        pure = all(in_tests_dir(p) or is_test_file(p) for _, p in rows)  # what a task commit is
        stats["pure_test_commits"] += pure
        stats["pure_test_commits_with_imported_changed_module"] += pure and bool(hits)
        stats["with_changed_nontest_module"] += bool(mods)
        stats["with_imported_changed_module"] += bool(hits)
        kinds = {classify(p) for p in hits}
        for kind in kinds:
            stats[f"commits_{kind}"] += 1
        stats["commits_outside_tests_only"] += kinds == {"outside_tests"}
        stats["commits_helper_only"] += kinds == {"helper_under_tests"}
    return stats


def report(repo: str, stats: Counter) -> None:
    n = stats["commits"] or 1
    print(f"== {repo}")
    for key in sorted(stats):
        pct = f" ({100 * stats[key] / n:.1f}%)" if key != "commits" else ""
        print(f"{key}: {stats[key]}{pct}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--limit", type=int, default=300)
    args = ap.parse_args()
    deadline = time.monotonic() + DEADLINE_SECONDS
    for repo in args.repos:
        report(repo, measure(repo, args.limit, deadline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
