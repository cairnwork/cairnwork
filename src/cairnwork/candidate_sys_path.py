"""Static sys.path/import-machinery checks for the files a candidate changes (#1065).

This catches source-visible mutation only; obfuscated or runtime-built forms remain an OPEN
residual. A same-process observer in experimental/import_origin does not change verdicts. Because
the scanner reports booleans, a modified file already signaling in base is not refused even if
the candidate retains that signal; added files are refused whenever they signal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected


def refuse_new_sys_path_mutation(
    repo: Path, base: str, candidate: str, allowed: tuple[str, ...] | list[str]
) -> None:
    """Refuse changed Python blobs with a new source-visible import-system signal. Only the
    candidate's own files (``allowed``) are read: on the worker path the judged commit also
    carries the deferred tests, which ``sys_path_scan`` already reads as the test commit."""
    try:
        changes = read_change_set(repo, base, candidate)
        own = set(allowed)
        for entry in changes.entries:
            if entry.change_type not in {"ADD", "MODIFY"} or not entry.path.endswith(".py"):
                continue
            if entry.path not in own:
                continue
            candidate_signal = _blob_signal(repo, candidate, entry.path)
            base_signal = (
                _blob_signal(repo, base, entry.path) if entry.change_type == "MODIFY" else False
            )
            if candidate_signal and not base_signal:
                raise CandidateRejected(
                    "CANDIDATE_SYS_PATH_MUTATION",
                    f"{entry.path}: newly adds a source-visible sys.path/import-machinery signal",
                )
    except CandidateRejected:
        raise
    except (
        ChangeSetError,
        GitCommandError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        # A failed scan cannot establish verifier integrity, so it must not lead to VERIFIED.
        detail = f"cannot scan candidate import paths: {exc}"
        raise CandidateRejected("UNREADABLE_CHANGE", detail) from exc


def _blob_signal(repo: Path, commit: str, path: str) -> bool:
    """Read and scan one committed blob; unreadable or unparseable source fails closed."""
    data = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
    signal = scan_sys_path_blob(data)
    if signal is None:
        raise ValueError(f"{commit}:{path} could not be parsed or decoded")
    return signal
