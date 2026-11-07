"""#960: can the base change what pytest collects, so that "pytest skips this directory" is not
a fact about the base? Read from git objects only (nothing is checked out or run).

The default-skipped directories (``.*``, ``build`` ...) are exempt from the P2P targets only while
pytest really skips them. Code or options the base gives pytest can undo that in more spellings
than a reader can list (an ``-o`` in addopts, a hook a conftest defines, imports, assigns or
registers, a plugin it loads, a write to ``config``), so this asks only whether the base gives
pytest any such input at all. The caller then refuses (the existing ``P2P_BASE_UNTARGETABLE``)
instead of exempting, when any of these holds:

* its addopts are not empty;
* it tracks a ``conftest.py`` anywhere (any case: APFS finds ``CONFTEST.PY`` as ``conftest.py``);
* a root ``pyproject.toml``, ``setup.cfg`` or ``setup.py`` names a ``pytest11`` entry point.

Plugins already installed in the verifier's interpreter are not the base's files and are not
seen here (#986 asks pytest itself).
"""

from __future__ import annotations

from pathlib import Path

from cairnwork.gitcmd import run_git_bytes

_PROJECT_FILES = frozenset({b"pyproject.toml", b"setup.cfg", b"setup.py"})


def changes_collection(repo: Path, base_commit: str, addopts: str) -> bool:
    """True when the base gives pytest options, conftest code or a plugin (see the module text)."""
    if addopts.strip():
        return True
    rows = run_git_bytes(repo, "ls-tree", "-r", "-z", base_commit, "--").split(b"\0")
    for row in filter(None, rows):
        meta, _, path = row.partition(b"\t")
        if path.rsplit(b"/", 1)[-1].lower() == b"conftest.py":
            return True
        if path.lower() in _PROJECT_FILES:
            _, kind, oid = meta.split()
            if kind != b"blob":  # a submodule there cannot be read: no exemption
                return True
            if b"pytest11" in run_git_bytes(repo, "cat-file", "blob", oid.decode("ascii")):
                return True
    return False
