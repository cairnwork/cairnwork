"""Refuse own import-system edits that can change what the deferred tests import (#1065).

An own changed file with an in-process machinery edit (sys.modules, finders, importlib, __import__,
runpy, pytest_plugins, or any dynamic-access word, matched as bytes) is refused outright: such a
swap needs no file. A file with only a path redirect or program launch (sys.path, __path__, site,
PYTHONPATH, subprocess) is refused only when a file the candidate changed can shadow an imported
module (candidate_shadow.py), so an honest edit that inserts its own directory still verifies.
Residuals: shadow files written at run time (left to the blackbox and verifier checks), sourceless
signals (a ``.pyc`` without source is not scanned), sys.path outside the repo. Accepted false
refusals: honest code using importlib or dynamic access next to any signal; an added module some
file imports, or a changed one sharing a module name, next to a redirect.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cairnwork.candidate_shadow import find_shadow_file
from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.sys_path_names import DYNAMIC_ATTRS, DYNAMIC_NAMES
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected

_OID = re.compile(r"^[0-9a-f]{40}$")
_OWN_KINDS = frozenset({"ADD", "MODIFY"})
_REDIRECT, _MACHINERY = "redirect", "machinery"
# Fail closed: a signalling file counts as a pure redirect only if none of these appear (bytes,
# so a name hidden in a string such as getattr(sys, "meta" + "_path") still falls here).
_MACHINERY_WORDS = tuple(
    word.encode()
    for word in (
        "sys.modules", "meta_path", "path_hooks", "path_importer_cache", "importlib", "runpy",
        "pytest_plugins", *sorted(DYNAMIC_NAMES), *sorted(DYNAMIC_ATTRS),
    )
)  # fmt: skip


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
            signal = path.endswith(".py") and _blob_signal(repo, candidate, path)
            if signal:  # a base signal is no exemption: it may be a comment next to a redirect
                if signal == _MACHINERY:  # an in-process swap needs no file (#303 SWAP, finders)
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{path}: edits the import machinery in-process (sys.modules, finders, "
                        "importlib, __import__, runpy or pytest_plugins)",
                    )
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


def _blob_signal(repo: Path, commit: str, path: str) -> str:
    """Read and scan one committed blob: "" (none), _REDIRECT (a path or program launch, which
    needs a shadow file) or _MACHINERY (any machinery word, by bytes). Unreadable or unparseable
    source fails closed."""
    data = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
    signal = scan_sys_path_blob(data)
    if signal is None:
        raise ValueError(f"{commit}:{path} could not be parsed or decoded")
    if not signal:
        return ""
    return _MACHINERY if any(word in data for word in _MACHINERY_WORDS) else _REDIRECT
