"""Refuse new import-path signals only when a reachable nested shadow file also exists (#1065).

Residuals: runtime-built or obfuscated signals and shadow files, sourceless signals (a ``.pyc``
without source is not scanned), and sys.path pointing outside the repo. Accepted false refusals
with a new signal: a pre-existing nested stdlib-named module (``pkg/json.py``), an added module
some tree file imports, or two tree files with one imported module name.
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
_OWN_KINDS = frozenset({"ADD", "MODIFY"})


def refuse_new_sys_path_mutation(
    repo: Path,
    base: str,
    candidate: str,
    allowed: tuple[str, ...] | list[str],
    test_commit: str | None = None,
) -> None:
    """Refuse a changed own Python file with a source-visible import-system signal when a file the
    candidate added or modified can shadow an imported module. Only ``allowed`` files are read for
    signals; test trees contribute imported names for shadow detection only."""
    try:
        if not _OID.fullmatch(base) or not _OID.fullmatch(candidate):
            raise ValueError("base and candidate must be full 40-character object ids")
        if test_commit is not None and not _OID.fullmatch(test_commit):
            raise ValueError("test commit must be a full 40-character object id")
        changes = read_change_set(repo, base, candidate)
        own = set(allowed)  # on the worker path the judged commit also adds the hidden tests
        changed = {
            e.path: e.change_type
            for e in changes.entries
            if e.change_type in _OWN_KINDS and e.path in own
        }
        for path in changed:
            if path.endswith(".py") and _blob_signal(repo, candidate, path):
                # a base signal is no exemption: it may be a comment word next to a new redirect
                shadow = find_shadow_file(repo, candidate, test_commit, changed)
                if shadow:
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{path}: has a source-visible sys.path/import-machinery signal; "
                        f"{shadow} can shadow an imported module",
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
