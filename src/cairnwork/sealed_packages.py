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
the package under test live (the checkout root's files and every directory above a test root,
which holds the roots) mentions one of ``_PATH_WORDS``. A plain text test: it fails closed on
any mention, and the base is the target's own code, not the candidate's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git

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
    if sealed and _base_may_edit_sys_path(repo, base_commit, above):
        return frozenset()
    return frozenset(sealed)


def _base_may_edit_sys_path(repo: Path, base_commit: str, above: tuple[str, ...]) -> bool:
    """Whether a ``.py`` file of the base at the checkout root or under a directory above a test
    root mentions one of ``_PATH_WORDS``. ``git grep -q`` exits 1 for no match; anything else
    (a fatal 128, a timeout) counts as a match."""
    words = [arg for word in _PATH_WORDS for arg in ("-e", word)]
    specs = (":(glob)*.py", *(f"{d}/*.py" for d in above))  # ``*`` crosses ``/`` here
    try:
        run_git(repo, "grep", "-q", "-I", "-F", *words, base_commit, "--", *specs)
    except GitCommandError as exc:
        cause = exc.__cause__
        return not (isinstance(cause, subprocess.CalledProcessError) and cause.returncode == 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return True
    return True


def _is_init(leaf: str) -> bool:
    """Any ``__init__`` spelling a candidate could add or change: every case and suffix."""
    return leaf.split(".", 1)[0].casefold() == "__init__"
