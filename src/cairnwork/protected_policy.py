"""#1585: the one resolved protected-path policy (operator file or the built-in default).

The operator names a policy file in ``CAIRNWORK_PROTECTED_POLICY_FILE``. Anything doubtful means the
full default list: no variable, no file, a symlink, a file or directory the operator does not own or
that others can write, a file inside any git checkout (a candidate commit must not supply it),
malformed JSON, an unknown key, or a waiver outside ``WAIVABLE``. A refusal never becomes a warning.
``WAIVABLE`` is empty until a repository owner has recorded consent (#1585 step 3), so today every
file is rejected and the resolved policy equals the built-in list.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

POLICY_ENV = "CAIRNWORK_PROTECTED_POLICY_FILE"
WAIVABLE: frozenset[str] = frozenset()  # repo-specific entries a policy may waive (step 3)
MAX_BYTES = 65536


@dataclass(frozen=True)
class ResolvedPolicy:
    paths: tuple[str, ...]  # the full default list
    waived: dict[str, frozenset[str]]  # repo -> entries that repo waived (validated)
    source: str  # "default" or the file that was read

    def paths_for(self, repo: str | None) -> tuple[str, ...]:
        waived = self.waived.get(repo, frozenset()) if repo else frozenset()
        return tuple(p for p in self.paths if p not in waived)


def _operator_only(path: Path) -> bool:
    """A regular file the operator owns, not writable by group or other, in such a directory,
    outside every git checkout."""
    info = path.lstat()
    parent = path.parent.lstat()
    for item in (info, parent):
        if item.st_uid != os.geteuid() or item.st_mode & 0o022:
            return False
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        return False
    return not any((p / ".git").exists() for p in (path.parent, *path.parent.parents))


def _parse(raw: bytes, waivable: frozenset[str]) -> dict[str, frozenset[str]] | None:
    doc = json.loads(raw)
    if not isinstance(doc, dict) or set(doc) != {"version", "repos"} or doc["version"] != 1:
        return None
    repos = doc["repos"]
    if not isinstance(repos, dict):
        return None
    out: dict[str, frozenset[str]] = {}
    for repo, body in repos.items():
        if not isinstance(repo, str) or not isinstance(body, dict) or set(body) != {"waive"}:
            return None
        waive = body["waive"]
        if not isinstance(waive, list) or not all(isinstance(w, str) for w in waive):
            return None
        if not set(waive) <= waivable:
            return None
        out[repo] = frozenset(waive)
    return out


def resolve(
    defaults: tuple[str, ...], env_path: str | None, *, waivable: frozenset[str] = WAIVABLE
) -> ResolvedPolicy:
    """The policy for this process; ``defaults`` when there is any doubt."""
    fallback = ResolvedPolicy(defaults, {}, "default")
    if not env_path or not os.path.isabs(env_path):
        return fallback
    try:
        path = Path(env_path)
        if not _operator_only(path):
            return fallback
        waived = _parse(path.read_bytes(), waivable)
    except (OSError, ValueError, RecursionError):
        return fallback
    return fallback if waived is None else ResolvedPolicy(defaults, waived, str(path))
