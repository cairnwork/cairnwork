"""Directories pytest's prepend import mode can never put on sys.path for a task (#382).

pytest (prepend or append mode, checked on 9.1.1, ``_pytest.pathlib.import_path``) puts on
sys.path the parent of a test or conftest module's top package, or the module's own directory
when that is not a package. Either way the directory it inserts has no ``__init__.py`` file or a
name that is not an identifier (``resolve_package_path`` stops at both). A directory that keeps
a regular ``__init__.py`` and an identifier name through the run is therefore never inserted,
and nothing in it is importable under a top-level name: the shadow gate need not treat it as an
import root (verifier_spec). The ``__init__.py`` stays when nothing can remove it:

- above a test root, no allowed path may be an ``__init__`` in that directory (the gate already
  refuses a new one, and a tracked one when deletions are allowed; here even a modifiable one
  counts as a change, so the answer never depends on the deletion check at snapshot);
- inside a test root, the candidate may not change anything, and the test commit may only add
  or modify regular files (deferred_tests.test_paths), so it can add an ``__init__.py`` but
  never remove one.

That holds for pytest, not for the base's own code: a conftest or test that inserts its parent
directory, or runs a script with another ``sys.path``, puts a sealed directory in front all the
same (#385 review). So nothing is sealed when the base's Python code where tests, conftests and
the code they import live (the checkout root's files, every directory above a test root, which
holds the roots, and every import path) mentions one of ``_PATH_WORDS``; nor when the task's test
commit is in the repository and its code does. A plain text test: it fails closed on any mention,
and the base and the test commit are the target's own code, not the candidate's. A test commit
not fetched yet is not read (and never fetched for this).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.import_path_tree import normalize_import_paths

_REGULAR = ("100644", "100755")
# Ways Python code can put a directory on sys.path, or start Python with another sys.path.
_PATH_WORDS = (
    "sys.path",  # also sys.path_hooks
    "syspath",  # pytest's monkeypatch.syspath_prepend, pytester.syspathinsert
    "from sys import",
    "addsitedir",
    ".pth",
    "PYTHONPATH",
    "pytest_plugins",  # a plugin module may live anywhere
    "runpy",
    "sys.executable",
    "subprocess",
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
)


def sealed_packages(
    repo: Path | None,
    base_commit: str | None,
    roots: tuple[str, ...],
    above: tuple[str, ...],
    paths: tuple[str, ...],
    import_paths: tuple[str, ...] = (),
    test_commit: str | None = None,
) -> frozenset[str]:
    """The directories in ``above`` or under a test root that the base gives a regular file named
    exactly ``__init__.py`` (a case variant is not one on a case-sensitive host) and whose name is
    an identifier, minus those where an allowed path is (or is inside) an ``__init__`` of any
    spelling or suffix.
    Paths as git spells them. Empty without paths or when git cannot tell: every directory then
    counts as before."""
    if repo is None or base_commit is None or not paths:
        return frozenset()
    specs = (*(f"{d}/__init__.py" for d in above), *(f"{r}/" for r in roots))
    try:
        listing = run_git(repo, "ls-tree", "-r", "-z", base_commit, "--", *specs)
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return frozenset()
    touched = {
        "/".join(parts[:i]).casefold()
        for parts in (p.split("/") for p in paths)
        for i, part in enumerate(parts)
        if _is_init(part)
    }
    sealed = set()
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, _oid = meta.split()
        folder, _, leaf = path.rpartition("/")
        if (
            leaf == "__init__.py"
            and kind == "blob"
            and mode in _REGULAR
            and folder.rpartition("/")[2].isidentifier()
            and folder.casefold() not in touched
        ):
            sealed.add(folder)
    if not sealed:
        return frozenset()
    dirs = (*above, *normalize_import_paths(import_paths))
    commits = (base_commit, *(c for c in (test_commit,) if c and _present(repo, c)))
    if any(_may_edit_sys_path(repo, commit, dirs) for commit in commits):
        return frozenset()
    return frozenset(sealed)


def _present(repo: Path, commit: str) -> bool:
    """Whether the test commit is in the repository already (it is never fetched for this)."""
    try:
        run_git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return False
    return True


def _may_edit_sys_path(repo: Path, commit: str, dirs: tuple[str, ...]) -> bool:
    """Whether a ``.py`` file of commit at the checkout root or under one of dirs mentions one of
    ``_PATH_WORDS``. ``-a``: a file the repository's attributes call binary is searched too.
    ``git grep -q`` exits 1 for no match; anything else (a fatal 128, a timeout) counts as one."""
    words = [arg for word in _PATH_WORDS for arg in ("-e", word)]
    specs = (":(glob)*.py", *(f"{d}/*.py" for d in dirs))  # ``*`` crosses ``/`` here
    try:
        run_git(repo, "grep", "-q", "-a", "--no-textconv", "-F", *words, commit, "--", *specs)
    except GitCommandError as exc:
        cause = exc.__cause__
        return not (isinstance(cause, subprocess.CalledProcessError) and cause.returncode == 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return True
    return True


def _is_init(leaf: str) -> bool:
    """Any ``__init__`` spelling a candidate could add or change: every case and suffix."""
    return leaf.split(".", 1)[0].casefold() == "__init__"
