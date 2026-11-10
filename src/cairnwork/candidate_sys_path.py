"""Refuse new import-path signals only when a reachable nested shadow file also exists (#1065).

Residuals are runtime-built shadow files and sys.path pointing outside the repo. A pre-existing
nested stdlib-named module (for example ``pkg/json.py``) plus a new sys.path edit is also refused;
that accepted false refusal keeps the rule simple and protects a shadow that tests can reach.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cairnwork.candidate_shadow import find_shadow_file
from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected

_OID = re.compile(r"^[0-9a-f]{40}$")


def refuse_new_sys_path_mutation(
    repo: Path,
    base: str,
    candidate: str,
    allowed: tuple[str, ...] | list[str],
    test_commit: str | None = None,
) -> None:
    """Refuse changed Python blobs with a new source-visible import-system signal. Only the
    candidate's own files (``allowed``) are read for signals; test trees contribute imported names
    for shadow detection only."""
    try:
        if not _OID.fullmatch(base) or not _OID.fullmatch(candidate):
            raise ValueError("base and candidate must be full 40-character object ids")
        if test_commit is not None and not _OID.fullmatch(test_commit):
            raise ValueError("test commit must be a full 40-character object id")
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
                shadow = find_shadow_file(repo, candidate, test_commit)
                if shadow:
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{entry.path}: newly adds a source-visible sys.path/import-machinery "
                        f"signal; {shadow} can shadow an imported module",
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
