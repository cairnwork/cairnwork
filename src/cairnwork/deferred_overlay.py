"""How a test commit's files are laid over a candidate (``overlay-v1``, #229), split out of
deferred_tests (its size cap). A test commit may also delete test files (#798): the paths it
deletes travel as ``TestFiles.deleted`` and as the optional receipt binding key
``deleted_test_paths``; the overlay removes them, so the executed tree has no such file."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.refusal_types import _shown_detail

_REGULAR = ("100644", "100755")
MAX_DELETED_TEST_PATHS = (
    8  # a receipt binding carries at most this many (#870); the receiver reads it too
)
OVERLAY_RULE = "overlay-v1"  # how the tests are laid over a candidate (#229); bound in receipts


class TestCommitRejected(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.has_detail = bool(detail)
        super().__init__(f"{code}: {_shown_detail(detail)}" if detail else code)


# #1833: one fixed sentence per code, shown after the withheld count. It depends on the code alone,
# never on the input, so it cannot carry text of the tests; add a code here only with such a text.
FIXED_HINTS = {
    "TESTS_DO_NOT_FAIL_ON_BASE": (
        "a test class whose base is imported from another file is not read unless it is named"
        " Test* or the base is named *TestCase: rename such a class to Test*"
    ),
}


def author_reason(exc: BaseException, hidden: bool) -> str:
    """What the author reads for ``exc`` (#1688). A run with non-public tests (``hidden``, the
    #1186 signal: a test commit) shows a test-commit refusal as its code and a count: the detail
    (a test path, a message, a git error) is withheld, since it can carry text of the tests. Any
    other exception, and every visible run, is ``str(exc)`` byte for byte.
    A code in FIXED_HINTS adds its fixed sentence (#1833)."""
    if hidden and isinstance(exc, TestCommitRejected) and exc.has_detail:
        hint = FIXED_HINTS.get(exc.code)
        return f"{exc.code}: 1 refusal (detail withheld)" + (f"; {hint}" if hint else "")
    return str(exc)


class TestFiles(tuple):  # noqa: SLOT001  (a tuple of the added or changed paths, as before)
    """The test paths the verifiers and P2P see (added or changed), carrying the deleted ones."""

    deleted: tuple[str, ...] = ()
    # #1034: per verifier, the node ids the base run recorded (full ids, parametrisation kept)
    base_nodes: dict[str, tuple[str, ...]] = {}
    base_skips: dict[str, dict[str, tuple[str, str]]] = {}

    def __new__(
        cls,
        paths: Iterable[str] = (),
        deleted: Iterable[str] = (),
        base_nodes: dict[str, tuple[str, ...]] | None = None,
        base_skips: dict[str, dict[str, tuple[str, str]]] | None = None,
    ) -> TestFiles:
        self = super().__new__(cls, paths)
        self.deleted = tuple(deleted)
        self.base_nodes = dict(base_nodes or {})
        self.base_skips = {key: dict(value) for key, value in (base_skips or {}).items()}
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
