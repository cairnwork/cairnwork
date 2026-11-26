"""Small pieces of the verify run's orphan cleanup (#1796, #1802): what a marker and the
filesystem may be trusted for. Strict rule throughout: what cannot be read or seen is not gone."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

from cairnwork import proc_start
from cairnwork.intake_request import IntakeRefused
from cairnwork.run_tags import EVIDENCE_MARK
from cairnwork.run_tags import RUN_TAGS as _RUN_TAGS
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError
from cairnwork.workspace import ATTEMPT_ID


def path_absent(path: Path) -> bool:
    """True only when lstat says nothing is there (no such file, or a parent that is not a
    directory: nothing can exist below it). Any other error is "cannot see", never "gone"."""
    try:
        os.lstat(path)
    except OSError as exc:
        return isinstance(exc, (FileNotFoundError, NotADirectoryError))
    return False


def _mode(path: Path, follow: bool = False) -> int:
    """The file mode, 0 when nothing is there. Only "no such file" (or a parent that is not a
    directory) means absent: any other error (EACCES, EIO...) is raised, "cannot see" is never
    read as "gone". Every existence and kind test in the cleanup goes through here."""
    try:
        return (os.stat if follow else os.lstat)(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 0


def _exists(path: Path) -> bool:
    return _mode(path) != 0


def _is_link(path: Path) -> bool:
    return stat.S_ISLNK(_mode(path))


def _is_dir(path: Path) -> bool:
    return stat.S_ISDIR(_mode(path, True))


def _is_file(path: Path) -> bool:
    return stat.S_ISREG(_mode(path, True))


def write_start_mark(fd: int) -> None:
    """Note in a run's held marker when it started (boot mark, "" off Linux), as DISPATCHED does."""
    with suppress(OSError):  # no mark: cleanup keeps the strict rule
        os.write(fd, proc_start.boot_mark().encode())


def read_start_mark(fd: int) -> str:
    """The marker's start mark, or "" (strict rule) unless it is the one form ``boot_mark()``
    writes (proc_start's rule), from this boot, and not later than now: the file is not trusted,
    a mark from the future hides survivors."""
    try:
        text = os.pread(fd, 256, 0).decode("utf-8", "replace")
    except OSError:
        return ""
    mine, now = (proc_start._dispatched(m, "/proc") for m in (text, proc_start.boot_mark()))
    return text if mine is not None and now is not None and mine <= now else ""


def list_markers(markers: Path) -> list[Path]:
    """The attempt markers (plain files named as an attempt) in ``markers``, sorted; [] when the
    directory is absent or not a directory. Raises OSError when it, or anything on the way to it,
    cannot be searched or listed: not seeing the markers is not having none."""
    if not _is_dir(markers):
        return []
    return sorted(p for p in markers.iterdir() if ATTEMPT_ID.fullmatch(p.name) and not _is_link(p))


def leftovers(run_root: Path, attempt_id: str) -> list[str]:
    """Names (only) still under ``run_root`` after the attempt's removal: clones, verifier
    directories and snapshot indexes. Looked for on disk again; the removers are not trusted."""
    return _leftover_names(run_root, attempt_id)


def clear_account(intake: Any, attempt_id: str) -> list[str]:
    """Remove the attempt's directories under the verifier account's own run root and name what
    is still there. With no account configured there is nothing to look at; an account whose
    root cannot be checked is reported as a leftover, never as clean."""
    try:
        account = intake.verifier_account()
        if account is None:
            return []
        _remove_account_leftovers(account, attempt_id)
        if path_absent(account.run_root):
            return []
        if not _is_dir(account.run_root):
            return ["verifier-account-root-unreadable"]
        return leftovers(account.run_root, attempt_id)
    except (
        IntakeRefused,
        VerifierAccountError,
        OSError,
        ValueError,
    ):  # an untrusted or unreadable account root: kept, retried later
        return ["verifier-account-root-unreadable"]


def _run_attempt(attempt_id) -> bool:
    """An id a run makes (pipeline.py, verify_candidate.py: ``a`` + 16 hex). Only such an id
    names directories or working directories here: the journal is a file, do not trust it for
    paths, and a looser id could name another entry of the run root (``a1-q-v0``)."""
    return isinstance(attempt_id, str) and _RUN_ATTEMPT_ID.fullmatch(attempt_id) is not None


_RUN_ATTEMPT_ID = re.compile(r"a[0-9a-f]{16}")


def _run_dirs(root: Path, attempt_id: str) -> tuple[list[Path], list[Path], list[Path]]:
    """The attempt's run directories under ``root``: verifier checkouts (``.work``), private
    run dirs (``.priv``...; a verify log stays as evidence, only its run/ goes) and clones.
    One walk for both sides; raises OSError if ``root`` is unlistable."""
    # #1190: second-seed base runs made before the "s" tag used "-b2"
    patterns = (f"{attempt_id}-[{_RUN_TAGS}]-v*", f"{attempt_id}-b2-v*")

    # symlinks are listed too (dangling or not): they are unlinked, never followed (_remove_inside)
    def real(p: Path) -> bool:
        return _is_link(p) or _is_dir(p)

    listed = os.listdir(root)  # Path.glob would answer [] on 3.14

    def glob(pattern: str) -> list[Path]:
        return [root / n for n in listed if fnmatch.fnmatchcase(n, pattern)]

    works = [w for pattern in patterns for w in glob(f"{pattern}.work") if real(w)]
    privates = []
    for private in (p for pattern in patterns for p in glob(f"{pattern}.*")):  # P2P too
        if private.suffix == ".work" or not real(private):
            continue
        # a base-test or P2P run is not Core evidence and would show the tests: all of it goes
        keep_log = not _is_link(private) and (
            f"{attempt_id}-v-" in private.name or _is_file(private / EVIDENCE_MARK)
        )  # #1698: run directories no longer carry the run id; the Core run's has a mark
        privates.append(private / "run" if keep_log else private)
    clones = sorted(
        p for pattern in patterns for p in glob(pattern) if real(p) and "." not in p.name
    )
    return works, privates, clones


def _index_names(attempt_id: str) -> list[str]:
    """Index files: one list for the remover and the re-scan, so they cannot drift."""
    return [f"{attempt_id}.index", f"{attempt_id}.tests.index"]


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # the re-scan reports what is still there


def _remove_inside(root: Path, target: Path) -> None:
    """Delete ``target`` only if it is strictly inside ``root``. A symlink is unlinked itself,
    never followed: a name the dead run (or its account) swapped must not aim rmtree elsewhere."""
    if _is_link(target):
        _unlink(target)
        return
    real = Path(os.path.realpath(target))
    if Path(os.path.realpath(root)) in real.parents:
        shutil.rmtree(target, ignore_errors=True)


def _remove_account_leftovers(account: VerifierAccount, attempt_id: str) -> None:
    """Remove the attempt's run directories under the account's OWN run root (owned by the
    account, so only ``account.remove`` can delete them); verify logs stay as evidence."""
    if not _run_attempt(attempt_id):  # the journal is a file: do not trust it for paths
        return
    with suppress(OSError):  # not listable or searchable: the re-scan names it
        _remove_account_in(account, attempt_id)


def _remove_account_in(account: VerifierAccount, attempt_id: str) -> None:
    root = account.run_root
    if not _is_dir(root):
        return
    works, privates, clones = _run_dirs(root, attempt_id)
    for target in works + privates + clones:
        with suppress(VerifierAccountError):  # the re-scan reports what is still there
            account.remove(target)
        # the verifier checkout and its work dir are the OWNER's (verifiers.py makes them), so
        # the account's rm cannot remove them: the owner finishes what the account could not
        _remove_inside(root, target)


UNSEARCHABLE = "run-root-unsearchable"  # named in place of what could not be looked for


def _leftover_names(root: Path, attempt_id: str) -> list[str]:
    """Names (only) of the attempt's directories still under ``root`` after cleanup: anything
    that can hold laid-over hidden tests. A verify run's private dir is evidence and does not
    count, its ``run/`` scratch does. Re-scans from disk; it trusts nothing the removers said."""
    if not _run_attempt(attempt_id):
        return []
    root = Path(root)
    if (seen := _searchable(root)) is not True:
        return [] if seen is None else [UNSEARCHABLE]
    try:
        works, privates, clones = _run_dirs(root, attempt_id)
        own = [root / attempt_id] if _exists(root / attempt_id) else []
        indexes = [root / name for name in _index_names(attempt_id)]
        names = {
            p.name if p.name != "run" else p.parent.name + "/run"
            for p in works + privates + clones + own + indexes
            if _exists(p)  # a dangling symlink is still a leftover
        }
    except OSError:  # the root or a directory below it could not be listed or searched
        return [UNSEARCHABLE]
    return sorted(names) if _searchable(root) else [UNSEARCHABLE]  # still searchable after the scan


def _searchable(root: Path) -> bool | None:
    """None: no such directory. False: cannot be listed and searched ("cannot see"). True."""
    try:
        os.listdir(root)
        return os.access(root, os.X_OK)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return False
