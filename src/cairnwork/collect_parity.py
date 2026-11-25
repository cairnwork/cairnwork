"""#1768: the file set pytest collects against the file set Cairnwork counts, on the base.

Step 1 (this file so far): a pytest config below the root that sets a test-naming key. The
verifier's pinned ini never reads it, so a run from the root cannot see it; it is refused on its own
narrow rule. Anything that cannot be read as a config is refused too, never passed.
"""

from __future__ import annotations

from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.pytest_config_files import _CANDIDATES, PytestConfigError, _section

NAMING_KEYS = ("python_files", "python_classes", "python_functions")
NESTED = "P2P_BASE_NESTED_NAMING"
_FORMS = {(name, section): form for name, section, form in _CANDIDATES}
_NAMES = frozenset(name for name, _ in _FORMS)


def nested_naming(task) -> None:
    """Raise UNCOMPARABLE ``P2P_BASE_NESTED_NAMING`` when a config file below the base's root sets
    ``python_files``, ``python_classes`` or ``python_functions`` (or cannot be read)."""
    try:
        listing = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    except GitCommandError as exc:
        raise _refuse("the base tree cannot be listed") from exc
    for entry in filter(None, listing.decode("utf-8", "surrogateescape").split("\0")):
        meta, _, path = entry.partition("\t")
        if "/" not in path or path.rsplit("/", 1)[1] not in _NAMES:
            continue
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise _refuse(f"{_shown(path)} is not a regular file")
        try:
            text = run_git(task.repo, "cat-file", "blob", oid)
            keys = _keys(path.rsplit("/", 1)[1], text)
        except (GitCommandError, UnicodeDecodeError, PytestConfigError) as exc:
            raise _refuse(f"{_shown(path)} cannot be read") from exc
        if keys:
            raise _refuse(f"{_shown(path)} sets {', '.join(keys)}")


def _keys(name: str, text: str) -> list[str]:
    found: set[str] = set()
    for (file, section), form in _FORMS.items():
        if file == name:
            table = _section(text, section, form, name)
            found |= {k for k in NAMING_KEYS if table and k in table}
    return sorted(found)


def _shown(path: str) -> str:
    return path.encode("ascii", "backslashreplace").decode()[:80]  # a path is candidate text


def _refuse(why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", NESTED, f"{why} below the root"))
