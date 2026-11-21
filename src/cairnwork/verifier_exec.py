"""Low-level run primitives for a single verifier execution (split out of verifiers.py for the
400-line cap).

The sandboxed subprocess launch, the per-run directory/environment setup, and the small file/digest
helpers. Behaviour is unchanged — this is a move, not a logic change. ``verifiers.py`` orchestrates
these (``_run_one``); they never call back into it, so there is no import cycle.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from functools import partial
from pathlib import Path

from cairnwork import confined, deny_channel, pytest_config


def _same_directory(path: Path, before: os.stat_result) -> bool:
    try:
        after = os.lstat(path)
    except OSError:
        return False
    same = (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    return same and stat.S_ISDIR(after.st_mode)


def _execute(argv, profile_path, checkout, env, timeout, log_path, account, backend, pass_fds=()):
    """Run argv under the sandbox profile, as the verifier account when one is given.
    As if the verifier's venv were activated, its bin directory (argv[0] is its Python)
    leads PATH: tests that spawn plain ``python`` get the same interpreter as pytest."""
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(argv[0]).parent), env.get("PATH"))))
    # git refuses a repository another uid owns ("dubious ownership"); trust exactly this
    # checkout, nothing wider (under the account it is the account's own copy since #240).
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory")
    env["GIT_CONFIG_VALUE_0"] = os.path.realpath(checkout)
    if account is None:
        return confined.execute(
            backend.wrap(argv, profile_path), checkout, env, timeout, log_path, pass_fds=pass_fds
        )  # under an account sudo would close the descriptors: that path passes none
    os.chmod(profile_path, 0o640)
    returncode, timed_out, survivors = confined.execute(
        account.wrap(argv, profile_path, env),
        checkout,
        env,
        timeout,
        log_path,
        reap=account.reap,
    )
    return returncode, timed_out, survivors


def _run_confined(deny, profile, python, argv, profile_path, checkout, env, *rest):
    """Write the profile (tagged for a base run's deny channel, #797) and ``_execute`` under it;
    ``rest`` is _execute's (timeout, log_path, account, backend, pass_fds). Returns the profile
    used, _execute's three values and the deny evidence (None when not recorded)."""
    backend = rest[3]
    profile, tag = deny_channel.tagged(profile, backend.name, deny)
    _write_new(profile_path, profile.text.encode())
    canary = partial(_canary, python, profile_path, checkout, env, rest[2], backend)
    with deny_channel.watch(deny, backend.name, tag, canary) as seen:
        returncode, timed_out, survivors = _execute(argv, profile_path, checkout, env, *rest)
    return profile, returncode, timed_out, survivors, seen.evidence


def run_backend(
    backend,
    deny,
    python,
    argv,
    profile_path,
    checkout,
    run_dir,
    ini,
    private,
    env,
    timeout,
    log_path,
    account,
    channel,
):
    """Build and run the backend policy, or record the missing backend as a failed run."""
    if backend is None:
        _write_new(log_path, b"no sandbox backend available: verifier not executed\n")
        return None, None, False, 0, None
    profile = backend.policy(
        read_roots=(*pytest_config.interpreter_roots(python), ini, private / "guard"),
        write_roots=(checkout.parent, run_dir),
        mask_home_credentials=account is None,
    )
    return _run_confined(
        deny,
        profile,
        python,
        argv,
        profile_path,
        checkout,
        env,
        timeout,
        log_path,
        account,
        backend,
        channel.pass_fds if channel else (),
    )


def _canary(python, profile_path, checkout, env, account, backend, argv, log_path) -> str:
    """Run ``python -I -S -c <argv>`` like a verifier (same policy, uid and cwd); its output."""
    argv = [python, "-I", "-S", "-c", *argv]
    _execute(argv, profile_path, checkout, dict(env), 10, log_path, account, backend)
    return log_path.read_text(errors="replace") if log_path.exists() else ""


def _record_digest(**fields) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
