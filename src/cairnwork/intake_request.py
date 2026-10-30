"""The intake's closed request schema: a submitted payload becomes a TaskSpec or is refused
(``IntakeRefused``); unknown or missing fields are refused, never ignored. intake.py (the state
directory, config and task lifecycle) uses it and re-exports both names. Also the stored request,
``requests/<task_id>.json``: written whole and synced before its task is bound (#466), and read
back so that only its own absence or a cut-short body is RequestUnreadable."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

from cairnwork.issue_task import RequestUnreadable
from cairnwork.ownership import OWNER, BindResult, OwnershipError, OwnershipLedger, new_task_id
from cairnwork.pipeline import TaskSpec

_HEX = frozenset("0123456789abcdefABCDEF")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW  # requests/ itself, never a symlink

FIELDS = frozenset(
    {
        "idempotency_key",
        "card_hash",
        "title",
        "instructions",
        "repo",
        "base_commit",
        "allowed_files",
        "verifiers",
    }
)


class IntakeRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def task_from_payload(
    payload: Any,
    test_roots: tuple[str, ...] = ("tests",),
    test_store=None,
    import_paths: tuple[str, ...] = (),
) -> TaskSpec:
    """A closed schema: unknown or missing fields are refused, never ignored."""
    if not isinstance(payload, dict) or not FIELDS <= set(payload) <= FIELDS | {"test_commit"}:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise IntakeRefused(
            "BAD_REQUEST", f"fields must be {sorted(FIELDS)} (+test_commit), got {got}"
        )
    if not isinstance(payload.get("test_commit", ""), str):
        raise IntakeRefused("BAD_REQUEST", "test_commit must be a string")
    texts = ("idempotency_key", "card_hash", "title", "instructions", "repo", "base_commit")
    if not all(isinstance(payload[name], str) for name in texts):
        raise IntakeRefused("BAD_REQUEST", "text fields must be strings")
    files, verifiers = payload["allowed_files"], payload["verifiers"]
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise IntakeRefused("BAD_REQUEST", "allowed_files must be a list of strings")
    if not isinstance(verifiers, list) or not all(_verifier_shape(v) for v in verifiers):
        raise IntakeRefused("BAD_REQUEST", "verifiers must be [[id, [argv...]], ...]")
    if not os.path.isabs(payload["repo"]):
        raise IntakeRefused("BAD_REQUEST", "repo must be an absolute path")
    return TaskSpec(
        idempotency_key=payload["idempotency_key"],
        card_hash=payload["card_hash"],
        title=payload["title"],
        instructions=payload["instructions"],
        repo=Path(payload["repo"]),
        base_commit=payload["base_commit"],
        allowed_files=tuple(files),
        verifiers=tuple((v[0], tuple(v[1])) for v in verifiers),
        test_commit=payload.get("test_commit", ""),
        test_roots=test_roots,
        import_paths=import_paths,
        test_store=test_store,
    )


def _verifier_shape(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], list)
        and all(isinstance(a, str) for a in value[1])
    )


def store_request(requests: Path, task_id: str, payload: dict[str, Any]) -> None:
    """Write the request for a task id nothing is bound to yet: a new file (O_EXCL, no symlink),
    synced, then requests/ synced so the name survives a crash too. On any failure the file is
    removed and the error raised: the caller binds nothing, so the next submit starts over."""
    data = json.dumps(payload, sort_keys=True).encode("ascii")  # ensure_ascii: ASCII only
    name, dir_fd = f"{task_id}.json", os.open(requests, _DIR_FLAGS)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def bind_stored(
    ledger: OwnershipLedger, task: TaskSpec, payload: dict[str, Any], requests: Path
) -> BindResult:
    """Store the request under a new task id, then bind that id (#466), so every bound task's
    request is on disk, whole and synced. The copy is dropped when nothing is bound to it: a
    refusal before the commit (OwnershipError) or a resend answered with the existing task. Any
    other failure of the bind may come after its commit, so the copy stays."""
    task_id = new_task_id()
    store_request(requests, task_id, payload)
    try:
        bound = ledger.bind(task.idempotency_key, task.request(), OWNER, task.card_hash, task_id)
    except OwnershipError:
        _drop(requests, task_id)
        raise
    if bound.duplicate:
        _drop(requests, task_id)
    return bound


def _drop(requests: Path, task_id: str) -> None:
    """Remove a stored request no task is bound to; best effort (an orphan file is harmless)."""
    with contextlib.suppress(OSError):
        (requests / f"{task_id}.json").unlink()


def read_request(requests: Path, task_id: str) -> Any:
    """The stored request's JSON. RequestUnreadable only when the file is absent from requests/
    (held open, so no later look decides it; #450) or is ASCII JSON cut short (_cut_short). Anything
    else raises as it is: requests/ missing or a symlink, a symlink or non-regular file at the path
    (O_NOFOLLOW), EACCES or EIO, bytes that are not ASCII (never written: damage, not a cut), or
    complete but malformed JSON (a JSONDecodeError, a ValueError: damage, not a cut)."""
    dir_fd = os.open(requests, _DIR_FLAGS)
    try:
        try:
            fd = os.open(
                f"{task_id}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd
            )
        except FileNotFoundError as exc:
            raise RequestUnreadable(f"{task_id}: missing") from exc
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "not a regular file", f"{task_id}.json")
            text = handle.read().decode("ascii")
    finally:
        os.close(dir_fd)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if _cut_short(text, exc):
            raise RequestUnreadable(f"{task_id}: cut short") from exc
        raise


def _cut_short(text: str, exc: json.JSONDecodeError) -> bool:
    """Did the decoder fail only because the input ended (a prefix of JSON, as a store cut off at
    any byte leaves)? Yes when it failed at the end of the text, in a string that never closes,
    or in a \\uXXXX escape that runs past the end. A failure before the end ("not json",
    '{"a":???', extra data) is malformed content: damage, not a cut."""
    if exc.pos >= len(text) or exc.msg.startswith("Unterminated string"):
        return True
    if not exc.msg.startswith("Invalid \\uXXXX escape"):
        return False
    start = text.rfind("\\", 0, exc.pos + 1)  # its backslash: pos names it or its "u" (by build)
    tail = text[start + 2 :]  # the cut left at most its four hex digits (3.12.3 errs on all four)
    return start >= max(exc.pos - 1, 0) and len(tail) <= 4 and all(c in _HEX for c in tail)


def check_requests(state: Path) -> None:
    """BAD_STATE when requests/ is missing or not a plain directory while the ledger names a task
    (#466): those tasks' requests are gone, which prepare() must not hide by making it anew. With
    no ledger or an empty one nothing is lost, so prepare() creates it. Read-only."""
    requests, ledger = state / "requests", state / "ownership.sqlite"
    if (requests.is_dir() and not requests.is_symlink()) or not ledger.exists():
        return
    try:
        uri = f"{ledger.absolute().as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            bound = conn.execute("SELECT 1 FROM task_ownership LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        bound = True  # a ledger we cannot read may name tasks: fail closed
    if bound:
        raise IntakeRefused("BAD_STATE", f"{requests} is missing or not a plain directory")
