"""How a test commit's files are laid over a candidate (``overlay-v1``, #229), split out of
deferred_tests (its size cap). A test commit may also delete test files (#798): the paths it
deletes travel as ``TestFiles.deleted`` and as the optional receipt binding key
``deleted_test_paths``; the overlay removes them, so the executed tree has no such file."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git

_REGULAR = ("100644", "100755")
MAX_DELETED_TEST_PATHS = (
    8  # a receipt binding carries at most this many (#870); the receiver reads it too
)
OVERLAY_RULE = "overlay-v1"  # how the tests are laid over a candidate (#229); bound in receipts


class TestCommitRejected(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class TestFiles(tuple):  # noqa: SLOT001  (a tuple of the added or changed paths, as before)
    """The test paths the verifiers and P2P see (added or changed), carrying the deleted ones."""

    deleted: tuple[str, ...] = ()

    def __new__(cls, paths: Iterable[str] = (), deleted: Iterable[str] = ()) -> TestFiles:
        self = super().__new__(cls, paths)
        self.deleted = tuple(deleted)
        return self


def deleted_of(test_files: tuple[str, ...]) -> tuple[str, ...]:
    return getattr(test_files, "deleted", ())


def overlay_tree(
    repo: Path, head: str, test_commit: str, paths: tuple[str, ...], index: Path,
    deleted: tuple[str, ...] = (),
) -> str:  # fmt: skip
    """``overlay-v1`` (#229): head's tree with each test path replaced or added by its regular
    file (100644/100755) in test_commit and each ``deleted`` path removed (#798: it must be absent
    from test_commit, and not also in ``paths``); symlinks, gitlinks and paths with ``..`` or
    ``.git`` are refused. Plumbing only, in the private index ``index`` (read-tree, update-index
    --cacheinfo or --force-remove, write-tree), so worktree, attributes and config play no part: the
    verifier and the receiver (cairnwork_attest.verify_binding) rebuild the same tree id."""
    try:
        run_git(repo, "read-tree", head, index_file=index)
        for path in (*paths, *deleted):
            parts = path.split("/")
            if {"", ".", ".."} & set(parts) or ".git" in {p.casefold() for p in parts}:
                raise TestCommitRejected("OVERLAY_UNSUPPORTED_ENTRY", repr(path))
        if set(paths) & set(deleted):
            raise TestCommitRejected("OVERLAY_UNSUPPORTED_ENTRY", "a path both laid and deleted")
        for path in deleted:
            kept = run_git(repo, "ls-tree", "-z", test_commit, "--", path).strip("\0").split()[1:2]
            if kept and kept != ["tree"]:  # a directory in its place is a file-to-directory swap
                raise TestCommitRejected(
                    "OVERLAY_UNSUPPORTED_ENTRY", f"deleted path in tests {path!r}"
                )
            run_git(repo, "update-index", "--force-remove", "--", path, index_file=index)
        for path in paths:
            meta, _, name = run_git(repo, "ls-tree", "-z", test_commit, "--", path).partition("\t")
            mode, kind, blob = (meta.split() + ["", "", ""])[:3]
            if name.rstrip("\0") != path or kind != "blob" or mode not in _REGULAR:
                raise TestCommitRejected("OVERLAY_UNSUPPORTED_ENTRY", f"{mode or '-'} {path!r}")
            run_git(
                repo, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}",
                index_file=index,
            )  # fmt: skip
        return run_git(repo, "write-tree", index_file=index).strip()
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_OVERLAY_FAILED", str(exc)) from exc


def stray_deletions(repo: Path, base: str, judged: str, deleted: tuple[str, ...]) -> list[str]:
    """Paths the judged commit deletes from base that the test commit did not delete (#816
    review). Core's deletion_policy is whole-contract, so before it is asked with ALLOW because of
    ``deleted``, the judged change may delete exactly those test files and nothing else."""
    out = run_git(
        repo,
        "diff-tree",
        "-r",
        "-z",
        "--no-renames",
        "--diff-filter=D",
        "--name-only",
        base,
        judged,
    )  # noqa: E501
    return sorted(set(filter(None, out.split("\0"))) - set(deleted))
