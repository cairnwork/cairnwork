"""Whether a base module or package ``__init__`` may extend its own ``__path__`` (#525 follow-up).

pkgutil.extend_path, pkg_resources.declare_namespace or a hand-set ``__path__`` keep a module or
regular package open: Python then also looks for its submodules in other directories of its
name, an installed one included, so it does not end namespace_shadow's walk. The file is read
as git stores it and matched as bytes, in any suffix: source, bytecode and extension modules
all keep these names as text. A file git cannot read counts as open, so more paths are refused.
A ``__path__`` change spelled without these names, or made by another file, is not seen.
"""

from __future__ import annotations

from pathlib import Path

from cairnwork.gitcmd import OID, GitCommandError, run_git_bytes

_OPEN_WORDS = (b"__path__", b"declare_namespace")


def opens_its_path(repo: Path, blob: str) -> bool:
    """Whether the blob's bytes name ``__path__`` or ``declare_namespace``, or cannot be read."""
    if not OID.fullmatch(blob):
        return True
    try:
        data = run_git_bytes(repo, "cat-file", "blob", blob)
    except GitCommandError:
        return True
    return any(word in data for word in _OPEN_WORDS)
