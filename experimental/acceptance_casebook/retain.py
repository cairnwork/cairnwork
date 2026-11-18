"""Opt-in retention of a refused acceptance run's original evidence."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from experimental.acceptance_casebook.origin import OriginError, read_origin
from experimental.acceptance_casebook.store import CasebookError

_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CasebookError("GIT_COMMAND_FAILED") from exc
    return result.stdout.strip()


def _validate_shas(repo: Path, clone: Path, base: str, candidate: str, version: str) -> str:
    if any(not _SHA.fullmatch(value) for value in (base, candidate, version)):
        raise CasebookError("CASE_INVALID", "SHAs must be full lowercase 40-hex values")
    for sha in (base, candidate):
        try:
            _git("-C", str(repo), "cat-file", "-e", "--end-of-options", f"{sha}^{{commit}}")
        except CasebookError as exc:
            raise CasebookError("CASE_INVALID", "commit is absent from repository") from exc
    try:
        return _git(
            "-C", str(clone), "rev-parse", "--verify", "--end-of-options", f"{version}^{{tree}}"
        )
    except CasebookError as exc:
        raise CasebookError("UNKNOWN_ACCEPTANCE_VERSION") from exc


def _record(db: Path, record_id: str, base: str, candidate: str) -> tuple[str, dict[str, Any]]:
    uri = f"file:{quote(str(db.resolve()))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT task_id FROM core_verifications WHERE attempt_id = ?", (record_id,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise CasebookError("RECORD_NOT_FOUND") from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise CasebookError("RECORD_NOT_FOUND")
    try:
        rec, origin = read_origin(db, rows[0][0], record_id)
    except OriginError as exc:
        raise CasebookError(exc.code, str(exc)) from exc
    if rec.base_commit != base or rec.candidate_commit != candidate:
        raise CasebookError("CASE_RECORD_MISMATCH", "record bindings differ from supplied commits")
    if not origin["refused"]:
        raise CasebookError("CASE_ORIGIN_MISMATCH", "record is not a Core refusal")
    return rows[0][0], origin


def _create_bundle(repo: Path, dest: Path, base: str, candidate: str) -> None:
    with tempfile.TemporaryDirectory(prefix="casebook-git-") as temporary:
        clone = Path(temporary) / "repo"
        _git(
            "clone",
            "--quiet",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(repo),
            str(clone),
        )
        for sha in (base, candidate):
            _git("-C", str(clone), "fetch", "--quiet", "--no-tags", "--", str(repo), sha)
        _git("-C", str(clone), "update-ref", "--", "refs/heads/casebook-base", base)
        _git("-C", str(clone), "update-ref", "--", "refs/heads/casebook-candidate", candidate)
        _git(
            "-C",
            str(clone),
            "bundle",
            "create",
            "--",
            str(dest),
            "refs/heads/casebook-base",
            "refs/heads/casebook-candidate",
        )
        _git("bundle", "verify", "--", str(dest))
        heads = _git("bundle", "list-heads", "--", str(dest)).splitlines()
        found = {line.split()[0] for line in heads if line.split()}
        if not {base, candidate} <= found:
            raise CasebookError("BUNDLE_INVALID")


def _copy_notary(source: Path, destination: Path, task_id: str, record_id: str) -> None:
    uri = f"file:{quote(str(source.resolve()))}?mode=ro"
    try:
        src = sqlite3.connect(uri, uri=True)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        original, origin = read_origin(source, task_id, record_id)
        copied, copied_origin = read_origin(destination, task_id, record_id)
    except (sqlite3.Error, OriginError) as exc:
        raise CasebookError("NOTARY_COPY_FAILED") from exc
    if origin["row_sha256"] != copied_origin["row_sha256"] or original != copied:
        raise CasebookError("NOTARY_COPY_FAILED")


def _manifest(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("notary.sqlite", "candidate.bundle", "version.json")}  # fmt: skip


def _rename_exclusive(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    at_fdcwd = -100
    if platform.system() == "Darwin":
        rename = libc.renameatx_np
        result = rename(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination), 4)
    elif platform.system() == "Linux":
        rename = libc.renameat2
        result = rename(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination), 1)
    else:
        raise CasebookError("ATOMIC_RENAME_UNAVAILABLE")
    if result:
        error = ctypes.get_errno()
        if error == errno.EEXIST:  # EXDEV and others are write failures, not an existing case
            raise CasebookError("ALREADY_RETAINED")
        raise CasebookError("RETAIN_WRITE_FAILED")


def retain(
    notary_db: Path,
    record_id: str,
    repo: Path,
    base: str,
    candidate: str,
    version: str,
    cairnwork_clone: Path,
    into: Path,
    retainer: dict[str, Any] | None,
) -> str:
    paths = (notary_db, repo, cairnwork_clone, into)
    if any(str(path).startswith("-") for path in paths):
        raise CasebookError("CASE_INVALID", "paths must not begin with '-' ")
    tree = _validate_shas(repo, cairnwork_clone, base, candidate, version)
    task_id, origin = _record(notary_db, record_id, base, candidate)
    cases = into / "cases"
    case = cases / origin["row_sha256"]
    if case.exists():
        raise CasebookError("ALREADY_RETAINED")
    created_cases = not cases.exists()
    cases.mkdir(parents=True, exist_ok=True)
    try:
        temporary = Path(tempfile.mkdtemp(prefix=".retain-", dir=cases))
    except OSError as exc:
        if created_cases:
            try:
                cases.rmdir()
            except OSError:
                pass
        raise CasebookError("RETAIN_WRITE_FAILED") from exc
    try:
        _copy_notary(notary_db, temporary / "notary.sqlite", task_id, record_id)
        _create_bundle(repo, temporary / "candidate.bundle", base, candidate)
        (temporary / "version.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "tree": tree,
                    "retained_at": datetime.now(UTC).isoformat(),
                    "retainer": retainer,
                },
                sort_keys=True,
            )
            + "\n"
        )
        (temporary / "MANIFEST.json").write_text(
            json.dumps(_manifest(temporary), sort_keys=True) + "\n"
        )
        _rename_exclusive(temporary, case)
    except CasebookError:
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            if created_cases:
                cases.rmdir()
        except OSError:
            pass
        raise
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            if created_cases:
                cases.rmdir()
        except OSError:
            pass
        raise CasebookError("RETAIN_WRITE_FAILED") from exc
    return str(case)
