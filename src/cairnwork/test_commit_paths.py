"""Which paths a test commit may carry: the scope filter (#798) and no case variants (#488)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from cairnwork.gitcmd import GitCommandError, run_git

# pytest reads these on its own; a test commit carries tests, not configuration.
# Same names the candidate may not touch (verifiers.protected_verifier_inputs). __init__.py
# stays allowed: it runs only when the tests themselves import their package.
_SITE_NAMES = frozenset({"sitecustomize", "usercustomize"})  # also refused as package names
_NOT_TEST_FILES = frozenset(
    {
        "conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml", "pytest.toml",
        ".pytest.toml", ".pytest.ini", "setup.py", "sitecustomize.py", "usercustomize.py",
    }
)  # fmt: skip


def out_of_scope(path: str, status: str) -> bool:  # #798: conftest.py only added/modified
    posix, name = PurePosixPath(path), PurePosixPath(path).name.casefold()
    return (
        (name in _NOT_TEST_FILES and (posix.name != "conftest.py" or status not in ("A", "M")))
        or name.endswith(".pth")
        or any(part.casefold() in _SITE_NAMES for part in posix.parts)
    )


def case_variant_clash(
    repo: Path, base: str, added: list[str], deleted: list[str] | tuple[str, ...] = ()
) -> str | None:
    """Why an added path (or one of its directories) differs only in case from a path of the
    base tree or from another added path, else None. Compared case-folded whatever the local
    filesystem, so the verdict never depends on the host; on a case-folding disk both would
    land in one file and the clone check would blame the base after a worker had run. An
    unreadable base tree is a clash too (refused, never passed). The refusal code is right
    here, but the test commit is still consumed (#488 remainder)."""
    try:
        raw = run_git(repo, "ls-tree", "-r", "-z", "--name-only", "--full-tree", base)
    except GitCommandError as exc:
        return f"cannot read the base tree to compare case: {exc}"
    spelled: dict[str, set[str]] = {}
    gone = set(deleted)  # a path the test commit deletes is not in the tree it leaves
    for name in filter(None, raw.split("\0")):
        if name in gone:
            continue
        parts = name.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            spelled.setdefault(prefix.casefold(), set()).add(prefix)
    new: dict[str, set[str]] = {}  # the added paths' own directories and files, among themselves
    for path in added:
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            same = spelled.get(prefix.casefold(), set())
            if same and prefix not in same:
                return (
                    f"test commit adds {path!r}, which differs only in case from"
                    f" {sorted(same)[0]!r} in the base tree"
                )
            twin = new.setdefault(prefix.casefold(), set())
            if twin and prefix not in twin:
                return (
                    f"test commit adds {path!r} and {sorted(twin)[0]!r}, which differ only in case"
                )
            twin.add(prefix)
    return None
