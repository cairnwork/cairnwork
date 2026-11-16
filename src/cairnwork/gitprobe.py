"""Environment probes that tell git infrastructure failures from candidate failures (#1298)."""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from cairnwork.gitcmd import NETWORK_TIMEOUT_SECONDS, GitCommandError, run_git


def storage_exhausted(*dirs: Path) -> bool:
    """True when a directory cannot take a small write (ENOSPC, EDQUOT, EROFS) or has under
    1 MiB free. Probes the filesystem; never reads git's text, which a candidate can shape."""
    for where in dirs:
        try:
            if shutil.disk_usage(where).free < 1 << 20:
                return True
            with tempfile.TemporaryFile(dir=where) as probe:
                probe.write(b"\0" * 4096)
                probe.flush()
                os.fsync(probe.fileno())
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EDQUOT, errno.EROFS):
                return True
    return False


DEFAULT_PROTOCOLS = ("https", "ssh")


def fetch_commit(origin: str | None, oid: str, *, protocols=DEFAULT_PROTOCOLS) -> Path:
    """Fetch an object in verifier-owned storage and return its bare repo path."""
    if not origin:
        raise OSError("no verifier-owned origin configured")
    root = Path(tempfile.mkdtemp(prefix="cairnwork-test-fetch-"))
    try:
        options = ["-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never"]
        for protocol in protocols:
            options.extend(("-c", f"protocol.{protocol}.allow=always"))
        try:
            run_git(
                root,
                "-c",
                "core.hooksPath=/dev/null",
                "init",
                "--bare",
                "--quiet",
                "--template=",
            )
        except GitCommandError as exc:
            raise OSError(f"Cairnwork could not initialize fetch scratch: {exc}") from exc
        try:
            run_git(
                root,
                *options,
                "ls-remote",
                "--quiet",
                origin,
                "HEAD",
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        except GitCommandError as exc:
            raise OSError(f"Cairnwork could not reach origin: {exc}") from exc
        try:
            run_git(
                root,
                *options,
                "fetch",
                "--quiet",
                "--no-tags",
                origin,
                oid,
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        except GitCommandError as exc:
            if storage_exhausted(Path(tempfile.gettempdir()), root):
                raise OSError(f"Cairnwork storage failure: {exc}") from exc
            if isinstance(exc.__cause__, subprocess.CalledProcessError):
                raise TestCommitUnavailable from exc
            raise OSError(f"Cairnwork could not run git: {exc}") from exc
        return root
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


class TestCommitUnavailable(Exception):
    """The reachable origin did not provide the requested object."""


def fetch_to_supplied(repo: Path, origin: str | None, oid: str, *, protocols=DEFAULT_PROTOCOLS):
    """Prove the object in scratch first, then copy it to the verifier's working repository."""
    scratch = fetch_commit(origin, oid, protocols=protocols)
    try:
        from cairnwork.gitcmd import commit_present

        if not commit_present(scratch, oid):
            raise TestCommitUnavailable
        try:
            run_git(repo, "fetch", "--quiet", "--no-tags", str(scratch), oid)
        except (GitCommandError, OSError) as exc:
            raise OSError(f"Cairnwork could not write verified test commit: {exc}") from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
