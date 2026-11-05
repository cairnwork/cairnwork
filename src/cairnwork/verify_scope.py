"""The cairnwork-verify scope, narrowed to what the candidate changed (#866 case A2).

On the verify path the candidate exists before the task is checked, so an allowed file the
candidate leaves untouched can be dropped before the shadow rules run: listing an unchanged
module named like a stdlib module no longer refuses the task. The change set comes from git
(``read_change_set``), never from the caller. Anything else keeps the list exactly as given, so
every existing refusal still fires: a malformed or unreadable candidate, duplicates, or no
allowed file changed. A change outside the list is still OUT_OF_SCOPE at the candidate check."""

from __future__ import annotations

from pathlib import Path

from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError


def changed_scope(
    repo: Path, base: str, candidate: str, allowed: tuple[str, ...]
) -> tuple[str, ...]:
    if len(set(allowed)) != len(allowed):
        return allowed  # check_task refuses duplicates (BAD_SCOPE)
    try:
        changed = set(read_change_set(repo, base, candidate).paths)
    except (ChangeSetError, GitCommandError, OSError, ValueError):
        return allowed
    narrowed = tuple(path for path in allowed if path in changed)
    return narrowed or allowed
