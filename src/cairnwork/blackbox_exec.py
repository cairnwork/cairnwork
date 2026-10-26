"""Run a declaration's blackbox checks on a fresh checkout of the candidate (#303, blackbox.py).

The same confinement as a verifier run (verifiers.py), reused rather than rebuilt: a fresh checkout
(the account's own copy under a verifier account), the scrubbed environment and per-run HOME/TMPDIR
of ``_prepare_run``, the Seatbelt profile, ``_execute`` (as the account, timeout, process-group
kill, reaping), then the checkout comparison and the forged-bytecode scan. What runs is the trusted
runner (blackbox_runner.py) from the read-only guard directory, given the inputs only. The owner
reads the runner's output from the run directory: no symlink, a regular file, capped.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import cairnwork_sandbox
from cairnwork import gitcmd, pytest_config
from cairnwork.account_checkout import checkout_differences, make_account_checkout
from cairnwork.account_checkout import remove_checkout as _remove_checkout
from cairnwork.blackbox import MAX_BYTES, Declaration
from cairnwork.blackbox_runner import _RUNNER_SOURCE, MODULE, OUTPUT_NAME, install_runner
from cairnwork.bytecode_roots import bytecode_findings
from cairnwork.confined import ATTEMPT_ENV
from cairnwork.import_path_tree import require_plain_dirs
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError, lock_wait_seconds
from cairnwork.verifier_exec import _execute, _write_new
from cairnwork.verifiers import DEFAULT_TIMEOUT_SECONDS, _prepare_run, _remove_scratch
from cairnwork.workspace import create_attempt_clone

RUNNER_SHA256 = hashlib.sha256(_RUNNER_SOURCE.encode()).hexdigest()


@dataclass(frozen=True)
class BlackboxRun:
    report: bytes | None  # the runner's output; None when missing, not a regular file or too big
    returncode: int | None
    timed_out: bool
    survivors: int
    mutated: tuple[str, ...]
    forged: tuple[str, ...]
    log_path: Path
    checkout: str  # the checkout's resolved path, for the module-origin check
    profile_sha256: str | None
    account: bool  # ran as a verifier account (another uid): the only mode that can pass


def run_blackbox(
    declaration: Declaration, repo: Path, commit: str, run_root: Path, run_id: str, *,
    attempt_id: str = "", account: VerifierAccount | None = None, python: str | None = None,
    base_commit: str | None = None, import_paths: tuple[str, ...] = (),
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> BlackboxRun:  # fmt: skip
    """All of the declaration's checks in one run; errors creating the checkout propagate."""
    settings = pytest_config.base_settings(repo, base_commit)
    paths = (*import_paths, *settings.pythonpath)
    if paths:
        require_plain_dirs(repo, commit, paths)
    args = (declaration, repo, commit, run_id, attempt_id, timeout, paths)
    if account is None:
        return _run(*args, Path(run_root), python or sys.executable, None)
    with account.exclusive(lock_wait_seconds(1, timeout)):
        python = python or account.python
        if not account.available(python):
            raise VerifierAccountError(f"account {account.user} is not usable")
        account.require_idle()
        return _run(*args, account.run_root, python, account)


def _run(declaration, repo, commit, run_id, attempt_id, timeout, paths, run_root, python, account):
    clone_id = f"{run_id}-vbb"  # recovery finds it with the verifiers' (recovery._run_dirs)
    work = Path(run_root) / f"{clone_id}.work"
    work.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(mode=0o700)
    with gitcmd.marked(ATTEMPT_ENV, attempt_id) if attempt_id else nullcontext():
        try:
            if account is None:
                checkout = create_attempt_clone(repo, commit, work, clone_id).path
            else:  # the account's own copy, as for a verifier (#240)
                os.chmod(work, 0o3770)
                checkout = make_account_checkout(account, python, repo, commit, work, clone_id)
            return _run_in(declaration, repo, commit, checkout, python, timeout, attempt_id,
                           account, paths)  # fmt: skip
        finally:
            _remove_checkout(work, clone_id, account)


def _run_in(declaration, repo, commit, checkout, python, timeout, attempt_id, account, paths):
    private = Path(tempfile.mkdtemp(prefix=f"{checkout.name}.", dir=checkout.parent.parent))
    log_path, ini, profile_path = private / "blackbox.log", private / "unused.ini", private / "p.sb"
    run_dir, home, tmp, env, _, _ = _prepare_run(private, ini, b"")
    out = run_dir / OUTPUT_NAME
    real = os.path.realpath(checkout)
    spec = declaration.runner_input([os.path.join(real, p) for p in paths] + [real], str(out))
    install_runner(private / "guard", spec)
    if account is not None:
        account.share_run(private, ini, run_dir, home, tmp)
    if attempt_id:
        env[ATTEMPT_ENV] = attempt_id
    before = os.lstat(checkout)
    backend = cairnwork_sandbox.select()
    profile = None
    if backend is not None:
        profile = backend.policy(
            read_roots=(*pytest_config.interpreter_roots(python), private / "guard"),
            write_roots=(checkout.parent, run_dir),
            mask_home_credentials=account is None,
            allow_ipc=False,  # #311: no named IPC (semaphores, shm, SysV) in the blackbox run
        )
        _write_new(profile_path, profile.text.encode())
        argv = [python, "-P", "-m", MODULE]
        returncode, timed_out, survivors = _execute(
            argv, profile_path, checkout, env, timeout, log_path, account, backend
        )
    else:
        _write_new(log_path, b"no sandbox backend available: blackbox not executed\n")
        returncode, timed_out, survivors = None, False, 0
    mutated = checkout_differences(repo, commit, checkout, before, account, python)
    forged = bytecode_findings(tmp, checkout, run_dir, (), account, python)
    report = _read(out) if profile is not None else None
    _remove_scratch((home, tmp), account)
    return BlackboxRun(
        report, returncode, timed_out, survivors, tuple(mutated), tuple(forged), log_path, real,
        profile.sha256 if profile else None, account is not None,
    )  # fmt: skip


def _read(path: Path) -> bytes | None:
    """The candidate controls run_dir: no symlink, no FIFO (O_NONBLOCK), a capped read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        raw = handle.read(MAX_BYTES + 1)
    return raw if len(raw) <= MAX_BYTES else None


def account_can_read(account: VerifierAccount, path: Path) -> bool:
    """Can the verifier account open path for reading (mode, ACLs and every parent directory)?
    Asked unconfined, as the account, before any candidate code runs; an error counts as yes.
    Under the account lock: a process started outside it would count as a survivor in another
    holder's run (#247)."""
    probe = 'if test -r "$1"; then echo readable; else echo private; fi'
    argv = account.wrap(["/bin/sh", "-c", probe, "sh", str(path)], None)
    try:
        with account.exclusive(lock_wait_seconds(1, 60)):
            done = subprocess.run(argv, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError, VerifierAccountError):
        return True
    return done.returncode != 0 or done.stdout != b"private\n"  # only a clean "private"
