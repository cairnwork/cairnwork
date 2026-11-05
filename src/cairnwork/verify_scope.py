"""The cairnwork-verify task as check_task sees it: the shadow rules judge only the listed files
the candidate changed (#866 case A2).

On the verify path the candidate exists before the task is checked, so a listed file the
candidate leaves untouched cannot become a shadowing module and is dropped from what check_task
sees: listing an unchanged module named like a stdlib module no longer refuses the task. Only
check_task gets the narrowed copy; the task itself keeps the list as given, so P2P selection,
the receipt and the candidate's scope check use the full list. The change set comes from git
(``read_change_set``), never from the caller. The list is kept unchanged, so every existing
refusal fires as before, when it is malformed (not 1 to 10 entries, duplicates, a path the
protected-path rules refuse), when the candidate is malformed or git cannot read the change,
or when no listed file changed."""

from __future__ import annotations

from dataclasses import replace

from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError
from cairnwork.pipeline import TaskSpec
from cairnwork.protected_paths import path_refusal


def narrowed(task: TaskSpec, candidate: str) -> TaskSpec:
    allowed = task.allowed_files
    if not isinstance(allowed, (tuple, list)) or not 1 <= len(allowed) <= 10:
        return task
    if any(not isinstance(p, str) or path_refusal(p) for p in allowed) or len(set(allowed)) < len(
        allowed
    ):
        return task  # check_task refuses these on the full list (BAD_SCOPE, ..._PROTECTED)
    try:
        changed = set(read_change_set(task.repo, task.base_commit, candidate).paths)
    except (ChangeSetError, GitCommandError, OSError, ValueError, TypeError):
        return task
    kept = tuple(path for path in allowed if path in changed)
    return replace(task, allowed_files=kept) if kept else task
