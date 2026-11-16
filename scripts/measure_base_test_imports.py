"""Read-only measurement for #951: among recent commits that touch test files, how often does the
same commit change an existing module that a base test file (the parent commit) imports, and is
that module a shared test helper (under tests/) or something else. Uses ``git log``,
``git diff-tree``, ``git grep`` and ``git rev-parse`` only; samples at most --limit commits per
repo, from a fixed ref.

    uv run python scripts/measure_base_test_imports.py REPO [REPO ...] [--limit 300] \
        [--ref origin/main]

A git failure (bad repo path, bad ref) prints the git error to stderr and exits non-zero.
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
DEFAULT_REF = "origin/main"
# pytest/rootdir conventions that make a path importable without them
ROOT_DIRS = ("src", "tests", "test")


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str, ok: tuple[int, ...] = (0,)) -> str:
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if done.returncode not in ok:
        detail = done.stderr.strip() or f"exit {done.returncode}"
        raise GitError(f"git {args[0]} failed in {repo}: {detail}")
    return done.stdout


def resolve(repo: str, ref: str) -> str:
    try:
        return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    except GitError as exc:
        raise GitError(f"cannot resolve ref {ref!r}: {exc}") from exc


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix == ".py" and (
        p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py"
    )


def in_tests_dir(path: str) -> bool:
    return any(part in ("tests", "test") for part in PurePosixPath(path).parts[:-1])


def sample(repo: str, sha: str, limit: int) -> list[str]:
    out = git(repo, "log", "--no-merges", "--format=%H", sha, "--", "tests", "test", "*test_*.py")
    return out.split()[:limit]


def changed(repo: str, sha: str) -> list[tuple[str, str]]:
    # --root: a root commit diffs against the empty tree; others diff against their parent.
    out = git(repo, "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", sha)
    rows = [line.split("\t") for line in out.splitlines() if "\t" in line]
    return [(r[0][0], r[-1]) for r in rows]


def classify(path: str) -> str:
    return "helper_under_tests" if in_tests_dir(path) else "outside_tests"


def importable_names(path: str) -> set[str]:
    """Dotted names under which the file at repo-relative ``path`` can be imported.

    The full path always counts (a/b/c.py -> a.b.c, a/b/__init__.py -> a.b). A leading
    src/tests/test directory is also dropped, because pytest puts those on sys.path. Other
    packages never match, so
    ``import package_a.colors`` does not count as ``package_b/colors.py``. This is a lower bound.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    names = {".".join(parts)}
    if len(parts) > 1 and parts[0] in ROOT_DIRS:
        names.add(".".join(parts[1:]))
    return {n for n in names if n}


def has_parent(repo: str, sha: str) -> bool:
    return git(repo, "rev-parse", "--verify", "-q", f"{sha}^", ok=(0, 1)).strip() != ""


def imported_by_base_test(repo: str, sha: str, path: str) -> bool:
    if not has_parent(repo, sha):
        return False
    names = importable_names(path)
    if not names:
        return False
    name = "(" + "|".join(re.escape(n) for n in sorted(names)) + ")"
    end = "([^A-Za-z0-9_]|$)"  # POSIX classes: macOS git grep has no backslash-s or -b
    dot = "[A-Za-z0-9_.]+"
    pattern = (
        f"^[[:space:]]*(import[[:space:]]+({dot}[[:space:]]*,[[:space:]]*)*{name}{end}"
        f"|from[[:space:]]+{name}{end})"
    )
    # exit 1 means no match, which is a normal answer here; any other failure raises
    out = git(repo, "grep", "-l", "-E", pattern, f"{sha}^", "--", "*.py", ok=(0, 1))
    return any(is_test_file(line.split(":", 1)[-1]) for line in out.splitlines())


def measure(repo: str, sha: str, limit: int, deadline: float) -> Counter:
    stats: Counter = Counter()
    for commit in sample(repo, sha, limit):
        if time.monotonic() > deadline:
            stats["stopped_by_deadline"] += 1
            break
        stats["commits"] += 1
        rows = changed(repo, commit)
        mods = [p for st, p in rows if st in "MDR" and p.endswith(".py") and not is_test_file(p)]
        hits = {p for p in mods[:MAX_MODULES_PER_COMMIT] if imported_by_base_test(repo, commit, p)}
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


def report(repo: str, ref: str, sha: str, stats: Counter) -> None:
    n = stats["commits"] or 1
    print(f"== {repo}")
    print(f"ref: {ref} -> {sha}")
    for key in sorted(stats):
        pct = f" ({100 * stats[key] / n:.1f}%)" if key != "commits" else ""
        print(f"{key}: {stats[key]}{pct}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help="commit to sample from, resolved per repo and printed; pass a full sha to reproduce",
    )
    args = ap.parse_args(argv)
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        for repo in args.repos:
            sha = resolve(repo, args.ref)
            report(repo, args.ref, sha, measure(repo, sha, args.limit, deadline))
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
