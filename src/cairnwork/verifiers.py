"""Run a task's verifiers on a fresh checkout of the candidate (preserved behaviors #5, #14).

Verifiers come from the task, so they are untrusted text. Only one shape is
accepted: ``python3 -m pytest [-q] [-p no:cacheprovider] tests/<file>.py[::<name>]...``.
Anything else is rejected before it runs, and nothing goes through a shell.

Each run happens in a new clone of the candidate commit made from Cairnwork's repository (never
in the worker's clone), with a minimal environment (no GIT_*, no inherited credentials, HOME and
TMPDIR inside the run directory, bytecode and the pytest cache disabled) and a timeout. Bytecode a
test still writes (py_compile) goes to a per-run cache prefix in TMPDIR, which must hold nothing a
candidate could use to replace a protected input (pycache_guard.py), nor may any other place the
run can write (bytecode_roots.py, #276); the pytest process itself refuses to load bytecode from
files (bytecode_hook.py); hypothesis's store goes to TMPDIR as well (examples, an
ast-parsed constants cache, json: read as data, never imported). A
verifier passes only if it exits 0 and, at the end, the checkout matches the candidate tree: a
final comparison (treecompare.py) of paths, bytes, symlink targets and the owner-execute bit. It
does not cover the top-level .git (the verifier's scratch: never used by the owner afterwards),
files next to the checkout, times, ownership, link counts or other mode bits, and a change made
and undone during the run is not seen. Under a verifier account the checkout is the account's
own copy: a test may write, utime and chmod it, and it is compared, as in owner mode (#240,
account_checkout.py).
The pytest run must also leave a JUnit report showing at least one test run and none failed,
errored, skipped or xfailed, and every named target must own a test that ran: a module skipped
while collecting (``importorskip``, a module-level skip) counts as skipped (report_guard.py).
pytest's configuration is pinned to a Cairnwork-owned ini with rootdir and confcutdir at the
checkout, so no ini in the candidate or a parent directory applies, and plugin autoloading is
off; the ini holds only allow-listed keys from the task's base commit and plugins load by
explicit -p (pytest_config.py). Output streams to a log file with a size cap, the run's process
group is killed afterwards, and logs and reports live in a private directory, created with
O_EXCL|O_NOFOLLOW. A process that starts its own session escapes the group kill; only a sandbox
stops that. The observation hash binds the candidate commit, verifier id, full argv, exit code,
timeout, mutations, test result and log hash.

The run is confined with macOS Seatbelt (see cairnwork_sandbox): no network, reads only
from the system, the interpreter and the checkout, writes only to the checkout
and a per-run directory. Without a working sandbox-exec nothing runs and the
verifier fails (no unconfined fallback). Logs, the pytest ini and the profile
live outside what the child may write. The candidate's code still runs in the
same process as pytest, so it can forge the report inside the per-run
directory; a pass is evidence, not proof.

Known gap: Seatbelt does not stop a sandboxed process from reading other
processes' arguments and environment (sysctl KERN_PROCARGS2) when they run as
the same user, so tokens in other processes' environments are readable and could
be printed into the verifier log. A VerifierAccount closes that (another uid);
any process the account still runs after a verifier is killed and fails the run,
and none may run before the next one. Without one, a process that leaves the
run's process group is killed only if it still carries the run token in its
environment. Either way, never pass raw verifier logs to a worker or a model.
Python runs with ``-P`` so the checkout is never on ``sys.path`` before the real
pytest (a root ``pytest.py`` cannot replace it). Callers must keep tests,
pytest configuration and shadowing root-level modules out of the files a candidate
may change (``protected_verifier_inputs``), but that is not enough on its own:
candidate source code also runs inside the pytest process and can forge a
result.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

import cairnwork_sandbox
from cairnwork import gitcmd, pytest_config, report_channel, report_guard
from cairnwork.account_checkout import checkout_differences, make_account_checkout
from cairnwork.account_checkout import remove_checkout as _remove_checkout
from cairnwork.bytecode_roots import bytecode_findings
from cairnwork.confined import ATTEMPT_ENV, MAX_LOG_BYTES, RUN_TOKEN_VAR
from cairnwork.core_request import VerifierObservation
from cairnwork.import_path_tree import require_plain_dirs
from cairnwork.pycache_guard import PYCACHE_DIR
from cairnwork.reasons import why_not_passed
from cairnwork.run_scrub import close_private
from cairnwork.verifier_account import VerifierAccountError, lock_wait_seconds
from cairnwork.verifier_exec import (  # low-level run primitives, split out for the 400-line cap
    _execute,
    _file_sha256,
    _record_digest,
    _write_new,
)
from cairnwork.verifier_spec import (  # re-exported for existing importers
    VerifierRejected,
    VerifierSpec,
    parse_verifier,
    protected_verifier_inputs,
)
from cairnwork.verify_context import VerifyContext
from cairnwork.workspace import create_attempt_clone

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "VerifierRejected",
    "VerifierRun",
    "VerifierSpec",
    "parse_verifier",
    "protected_verifier_inputs",
    "run_verifiers",
]

DEFAULT_TIMEOUT_SECONDS = 600
# Kept here (not in verifier_exec) because tests monkeypatch verifiers._ENV_KEEP to inject env vars,
# and _prepare_run below must read the patched value.
_ENV_KEEP = ("PATH", "LANG", "LC_ALL")


@dataclass(frozen=True)
class VerifierRun:
    spec: VerifierSpec
    returncode: int | None
    timed_out: bool
    mutated: tuple[str, ...]
    log_path: Path
    observation: VerifierObservation
    outcomes: dict[str, str]
    survivors: int = 0
    ran: bool = False
    # a plain test failure: the guard reported a failed test and nothing about the run itself
    # is suspect (no timeout, mutation, survivor or oversized log); p2p.py may re-run only this
    plain_failure: bool = False
    report_too_big: bool = False  # the guard's report exceeded MAX_REPORT_BYTES: no verdict read
    recorded: bool = False  # the guard's record was there and well-formed (base_comparison, #234)
    collect_errors: tuple[str, ...] = ()  # collectors the guard saw fail (B2, #234)
    teardown_errors: tuple[str, ...] = ()  # tests whose teardown failed (#478, report_guard)
    why: str = ""  # in words, why the run did not pass ("" when it passed): reasons.py
    # facts about the sandbox policy used (e.g. "shm withheld: BWRAP_TMPFS_SIZE_UNSUPPORTED"); not
    # in the observation's artifact hash, so recording them changes no bound digest
    notes: tuple[str, ...] = ()


def run_verifiers(
    ctx: VerifyContext,
    candidate_commit: str,
    specs: tuple[VerifierSpec, ...],
    run_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    note: str = "",
) -> tuple[VerifierRun, ...]:
    """Run each spec on a fresh checkout of candidate_commit; a failing test is not an error.

    If a checkout cannot be created the error propagates and no observation is
    returned (fail closed). With an account, verifiers run as that dedicated user
    (verifier_account.py), in its run root and with its Python (or ``ctx.python``), one at a
    time. With a base commit, pytest settings come from that commit (pytest_config.py).
    ``import_paths`` (operator state config, #82) are checkout-relative dirs prepended to the
    verifier's import path so a src-layout project can import its own package. A non-empty note
    (the P2P selection rule, p2p.py) is bound into each observation's artifact hash.
    """
    settings = pytest_config.verifier_settings(ctx.repo, ctx.base_commit, ctx.import_paths)
    if settings.pythonpath:
        require_plain_dirs(ctx.repo, candidate_commit, settings.pythonpath)
    account = ctx.account
    if account is not None:
        ctx, python = replace(ctx, run_root=account.run_root), ctx.python or account.python
        with account.exclusive(lock_wait_seconds(len(specs), timeout)):
            if not account.available(python):
                raise VerifierAccountError(f"account {account.user} is not usable")
            account.require_idle()
            account.scrub_stale_runs()  # a killed run's private/run, before this run creates one
            batch = _Batch(ctx, python, candidate_commit, timeout, settings, note)
            return _run_all(batch, specs, run_id)
    python = ctx.python or sys.executable
    return _run_all(_Batch(ctx, python, candidate_commit, timeout, settings, note), specs, run_id)


@dataclass(frozen=True)
class _Batch:
    """One run_verifiers call: the same for each of its specs. Under an account ctx.run_root is
    the account's. python is the interpreter the runs use: ctx.python, else the account's, else
    sys.executable; read it here, not from ctx."""

    ctx: VerifyContext
    python: str
    candidate_commit: str
    timeout: int
    settings: pytest_config.PytestSettings
    note: str


def _run_all(batch: _Batch, specs, run_id) -> tuple[VerifierRun, ...]:
    ctx, python = batch.ctx, batch.python
    if not os.path.isabs(python):
        raise VerifierRejected(f"python must be an absolute path, got {python!r}")
    ids = [spec.verifier_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise VerifierRejected(f"duplicate verifier ids in {ids}")
    runs = []
    for index, spec in enumerate(specs):
        clone_id = f"{run_id}-v{index}"
        # The checkout sits alone in its own work directory, which the tests may write:
        # some create files next to their repository (Nexus-new: nexus-runtime-targets),
        # and that must stay inside this run. Under the account the checkout is its own copy, which
        # it may rename (sticky only protects the owner's names): _same_directory catches that.
        work = Path(ctx.run_root) / f"{clone_id}.work"
        work.parent.mkdir(parents=True, exist_ok=True)
        work.mkdir(mode=0o700)
        mark = gitcmd.marked(ATTEMPT_ENV, ctx.attempt_id) if ctx.attempt_id else nullcontext()
        with mark:
            try:
                if ctx.account is None:
                    checkout = create_attempt_clone(
                        ctx.repo, batch.candidate_commit, work, clone_id
                    ).path
                else:  # the account's own copy: may write, utime, chmod as the owner (#240)
                    os.chmod(work, 0o3770)
                    checkout = make_account_checkout(
                        ctx.account, python, ctx.repo, batch.candidate_commit, work, clone_id
                    )
                runs.append(_run_one(batch, spec, checkout))
            finally:
                _remove_checkout(work, clone_id, ctx.account)
    return tuple(runs)


def _prepare_run(private: Path, ini: Path, ini_text: bytes, channel=None):
    """Create the per-run directory (the only place besides the checkout the child may
    write), its HOME and TMPDIR, and the scrubbed environment."""
    run_dir = private / "run"
    home, tmp = run_dir / "home", run_dir / "tmp"
    for directory in (run_dir, home, tmp):
        directory.mkdir()
    guard = private / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)  # the trusted plugin, read-only to the sandbox
    guard.chmod(0o750)
    _write_new(ini, ini_text)
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env.update(
        HOME=str(home),
        TMPDIR=str(tmp),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPYCACHEPREFIX=str(tmp / PYCACHE_DIR),  # bytecode a test writes: pycache_guard.py
        # hypothesis keeps .hypothesis/ in the cwd (the checkout) unless told otherwise; no ignore
        # rule in the checkout comparison, so a hypothesis that ignores this still fails the run
        HYPOTHESIS_STORAGE_DIRECTORY=str(tmp / "hypothesis"),
        PYTHONNOUSERSITE="1",
        PYTHON_DISABLE_REMOTE_DEBUG="1",  # 3.14+: no debugger script injected (bytecode_hook.py)
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=str(guard),
    )
    env[RUN_TOKEN_VAR] = secrets.token_hex(16)
    verdict_path, verdict_nonce = run_dir / "verdict.json", secrets.token_hex(16)
    if channel is not None:  # owner run: descriptors, no nonce or path in the environment
        env.update(channel.env())
    else:  # verifier account: sudo closes descriptors, so path and nonce stay in the environment
        env[report_guard.PATH_ENV] = str(verdict_path)
        env[report_guard.NONCE_ENV] = verdict_nonce
    return run_dir, home, tmp, env, verdict_path, verdict_nonce


def _run_one(batch: _Batch, spec, checkout) -> VerifierRun:
    account, python = batch.ctx.account, batch.python
    plugins = pytest_config.installed_plugins(python)
    ini_text = batch.settings.ini_text(checkout, plugins)
    private = Path(tempfile.mkdtemp(prefix=f"{checkout.name}.", dir=checkout.parent.parent))
    channel = report_channel.open_channel(private) if account is None else None
    try:
        return _run_one_in(batch, spec, checkout, plugins, ini_text, private, channel)
    finally:  # close the channel, then drop private/run even if that close raises
        try:
            if channel is not None:
                channel.close()
        finally:
            close_private(private, account)


def _run_one_in(batch: _Batch, spec, checkout, plugins, ini_text, private, channel) -> VerifierRun:
    ctx, candidate_commit, timeout = batch.ctx, batch.candidate_commit, batch.timeout
    account, python, attempt_id = ctx.account, batch.python, ctx.attempt_id
    log_path, ini, profile_path = (
        private / "verifier.log",
        private / "pytest.ini",
        private / "profile.sb",
    )
    run_dir, home, tmp, env, verdict_path, verdict_nonce = _prepare_run(
        private, ini, ini_text, channel
    )
    if account is not None:
        account.share_run(private, ini, run_dir, home, tmp)
    if attempt_id:
        env[ATTEMPT_ENV] = attempt_id
    argv = [
        python, "-P", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(ini),
        f"--rootdir={checkout}", f"--confcutdir={checkout}", "-o", "addopts=",
        *batch.settings.args(plugins), "-p", report_guard.MODULE, *spec.targets,
    ]  # fmt: skip
    before = os.lstat(checkout)
    profile = None
    backend = cairnwork_sandbox.select()
    if backend is not None:
        profile = backend.policy(
            read_roots=(*pytest_config.interpreter_roots(python), ini, private / "guard"),
            write_roots=(checkout.parent, run_dir),
            mask_home_credentials=account is None,  # account uid can't enter the owner's home
        )
        _write_new(profile_path, profile.text.encode())
        fds = channel.pass_fds if channel else ()
        returncode, timed_out, survivors = _execute(
            argv, profile_path, checkout, env, timeout, log_path, account, backend, fds
        )
    else:
        _write_new(log_path, b"no sandbox backend available: verifier not executed\n")
        returncode, timed_out, survivors = None, False, 0
    mutated = checkout_differences(ctx.repo, candidate_commit, checkout, before, account, python)
    forged = bytecode_findings(tmp, checkout, run_dir, spec.targets, account, python)
    verdict = channel.read() if channel else report_guard.read_verdict(verdict_path, verdict_nonce)
    not_run = verdict.not_run(spec.targets)  # a target with no test that ran, a skipped module
    ran = profile is not None and verdict.ran and not not_run
    clean = not timed_out and not mutated and survivors == 0 and not forged
    too_big = log_path.stat().st_size > MAX_LOG_BYTES
    clean = clean and not too_big and not verdict.too_big
    passed = profile is not None and returncode == 0 and clean and ran
    plain_failure = (
        profile is not None and returncode == 1 and clean and "failed" in verdict.outcomes.values()
    )
    why = "" if passed else why_not_passed(
        ran_sandboxed=profile is not None, returncode=returncode, timed_out=timed_out,
        mutated=mutated, survivors=survivors, log_too_big=too_big, tests_ran=ran,
        report_too_big=verdict.too_big, forged=forged, not_run=not_run, renamed=verdict.renamed,
    )  # fmt: skip
    digest = _record_digest(
        candidate_commit=candidate_commit,
        verifier_id=spec.verifier_id,
        argv=argv,
        pytest_ini_sha256=hashlib.sha256(ini_text).hexdigest(),
        sandbox_profile_sha256=profile.sha256 if profile else None,
        returncode=returncode,
        timed_out=timed_out,
        mutated=mutated,
        **({"forged_bytecode": list(forged)} if forged else {}),
        tests_passed=ran,
        survivors=survivors,
        log_sha256=_file_sha256(log_path),
        **({"note": batch.note} if batch.note else {}),
    )
    observation = VerifierObservation(
        spec.verifier_id, f"{private.name}/verifier.log", f"sha256:{digest}", passed
    )
    return VerifierRun(
        spec, returncode, timed_out, mutated, log_path, observation, verdict.outcomes,
        survivors=survivors, ran=ran, plain_failure=plain_failure, why=why,
        report_too_big=verdict.too_big, notes=profile.notes if profile else (),
        recorded=verdict.recorded, collect_errors=verdict.collect_errors,
        teardown_errors=verdict.teardown_errors,
    )  # fmt: skip
