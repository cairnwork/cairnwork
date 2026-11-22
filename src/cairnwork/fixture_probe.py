"""#986 step 4a: the base's fixture names from a collect-only run (fixture_record.py).

The recorder is written into the run's private guard directory (read-only to the sandbox, first
on PYTHONPATH) under a per-run random module name, so a checkout file cannot take that name and
load ahead of it. Owner runs only. Any failure, a timeout, a missing sandbox or a record whose
recorder entry is not this run's own module and file gives None (the #916 rule), never fewer names.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from cairnwork.collected_names import collected_names

PREFIX = "_cairnwork_fixture_record_"
RECORDER = Path(__file__).with_name("fixture_record.py")
_MAX_BYTES = 4 * 1024 * 1024
_MODULE_FILE = "probe_module"  # in private/, which the sandbox cannot write: the run's module name
_NONCE_FILE = "probe_nonce"  # likewise: the run's nonce (the recorder gets it on an inherited pipe)


@dataclass
class Probe:
    """One run's recorder: argv additions, and what the reader must check. Empty when inactive."""

    argv: tuple[str, ...] = ()
    module: str = ""
    record_dir: Path | None = None
    plugin_file: Path | None = None
    settings_digest: str = ""
    base_sha: str = ""
    nonce: str = ""
    read_fd: int = -1  # the parent's copy of the nonce pipe's read end; -1 once closed

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return (self.read_fd,) if self.read_fd >= 0 else ()

    def close(self) -> None:
        fd, self.read_fd = self.read_fd, -1  # never retry a close
        if fd >= 0:
            os.close(fd)

    def read(self, returncode: object, timed_out: bool) -> frozenset[str] | None:
        if self.record_dir is None or self.plugin_file is None or timed_out:
            return None
        try:
            if not self.nonce or not _recorded_by(
                self.record_dir, self.module, self.plugin_file, self.nonce
            ):
                return None
            return collected_names(
                self.record_dir,
                exit_code=returncode,
                settings_digest=self.settings_digest,
                base_sha=self.base_sha,
            )
        except Exception:  # noqa: BLE001 - any fault means no names (the #916 rule)
            return None


def start(spec, ctx, private: Path, env: dict, ini_text: bytes, candidate_commit: str) -> Probe:
    """Install the recorder under a fresh module name; point the run's env at a new record dir."""
    if not spec.record_fixtures or ctx.account is not None:
        return Probe()
    read_fd = -1
    try:
        module = f"{PREFIX}{secrets.token_hex(8)}"
        nonce = secrets.token_hex(16)
        read_fd = _nonce_pipe(nonce)
        plugin_file = _install(private / "guard", module)
        record_dir = private / "run" / "fixture_record"
        record_dir.mkdir()
        digest = hashlib.sha256(ini_text).hexdigest()
        base = ctx.base_commit or candidate_commit
        _write_new(private / _MODULE_FILE, module.encode())
        _write_new(private / _NONCE_FILE, nonce.encode())
        env.update(
            CAIRNWORK_FIXTURE_NONCE_FD=str(read_fd),
            CAIRNWORK_FIXTURE_RECORD_DIR=str(record_dir),
            CAIRNWORK_SETTINGS_DIGEST=digest,
            CAIRNWORK_BASE_SHA=base,
        )
        return Probe(("-p", module), module, record_dir, plugin_file, digest, base, nonce, read_fd)
    except Exception:  # noqa: BLE001 - no recorder means no names
        if read_fd >= 0:
            os.close(read_fd)
        return Probe()


def finish(spec, ctx, candidate_commit: str, private: Path, ini_text: bytes, run):
    """After the run and before its private dir is dropped: the names, or None."""
    if not spec.record_fixtures or ctx.account is not None:
        return None
    try:
        module = (private / _MODULE_FILE).read_text(encoding="utf-8")
        nonce = (private / _NONCE_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    record_dir = private / "run" / "fixture_record"
    plugin_file = private / "guard" / f"{module}.py"
    digest = hashlib.sha256(ini_text).hexdigest()
    base = ctx.base_commit or candidate_commit
    probe = Probe(("-p", module), module, record_dir, plugin_file, digest, base, nonce)
    return probe.read(run.returncode, run.timed_out)


def _nonce_pipe(nonce: str) -> int:
    """A pipe already holding the nonce, write end closed; returns the read end for pass_fds."""
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, nonce.encode())
    except BaseException:
        os.close(read_end)
        raise
    finally:
        os.close(write_end)
    return read_end


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _install(guard: Path, module: str) -> Path:
    """Copy the recorder's source into the guard dir with O_EXCL|O_NOFOLLOW, as install_plugin."""
    path = guard / f"{module}.py"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "wb") as handle:
        handle.write(RECORDER.read_bytes())
    os.chmod(path, 0o640)
    return path


def _recorded_by(record_dir: Path, module: str, plugin_file: Path, nonce: str) -> bool:
    """True when record.json carries this run's nonce and lists its recorder module and file.

    The nonce is what the run cannot learn (the recorder reads it from an inherited pipe at
    import, before checkout code); the module and file check ties it to the recorder copy."""
    record = _load(record_dir / "record.json")
    if not isinstance(record, dict) or record.get("nonce") != nonce:
        return False
    entries = record.get("plugin_modules") if isinstance(record, dict) else None
    if not isinstance(entries, list):
        return False
    want = os.path.realpath(plugin_file)
    return any(
        isinstance(entry, dict)
        and entry.get("name") == module
        and isinstance(entry.get("file"), str)
        and os.path.realpath(entry["file"]) == want
        for entry in entries
    )


def _load(path: Path):
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            return None
        return json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - any read or parse fault means no record
        return None
