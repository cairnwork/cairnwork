"""Run a probe in the verifier's sandbox (owner mode) and judge its output against ``expected``.

Same backend, read roots and write roots as ``second_observer.observe`` and
``verifiers._run_one_in``. The output file is created empty by the runner inside the run directory,
and read only after ``confined.execute`` returned (every process of the run is gone or counted as
a survivor). ``expected`` is a parameter: it is never written under the sandbox's roots."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cairnwork_sandbox
from cairnwork import confined, pytest_config
from cairnwork.confined import RUN_TOKEN_VAR
from experimental.oracle_observer import FAIL, MISMATCH, NO_OUTPUT, OUTPUT_INVALID, PASS
from experimental.oracle_observer.comparator import MAX_BYTES, Invalid, matches, parse_document
from experimental.oracle_observer.expected import Expected

RUNNER = Path(__file__).with_name("probe_runner.py")
ENV_KEEP = ("PATH", "LANG", "LC_ALL")


@dataclass(frozen=True)
class ProbeRun:
    """What the sandboxed run left: all of it is untrusted data except how it ended."""

    returncode: int | None
    timed_out: bool
    survivors: int
    output: bytes | None
    log: str = field(default="", repr=False)
    backend: str = ""


@dataclass(frozen=True)
class OracleVerdict:
    """PASS or FAIL plus one reason code: the only part that may leave the runner."""

    verdict: str
    code: str = ""


def _read_output(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as handle:
            return handle.read(MAX_BYTES + 2)
    except OSError:
        return None


def run_probe(
    probe: Path,
    checkout: Path,
    workdir: Path,
    *,
    interpreter: str = sys.executable,
    pythonpath: tuple[str, ...] = (),
    timeout: int = 120,
) -> ProbeRun:
    """Run ``probe`` against ``checkout`` under the sandbox; ``workdir`` is the runner's own."""
    backend = cairnwork_sandbox.select()
    if backend is None:
        raise RuntimeError("no sandbox backend available: the probe is not run unconfined")
    run_dir, supervisor = workdir / "run", workdir / "supervisor"
    home = run_dir / "home"
    for directory in (supervisor, run_dir, home):
        directory.mkdir(parents=True, exist_ok=True)
    runner, probe_copy, out = (
        supervisor / "probe_runner.py",
        supervisor / "probe.py",
        run_dir / "out.json",
    )
    shutil.copyfile(RUNNER, runner)
    shutil.copyfile(probe, probe_copy)
    for path in (runner, probe_copy):
        path.chmod(0o444)
    out.touch(mode=0o600)
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update(HOME=str(home), TMPDIR=str(home), **dict(getattr(backend, "run_env", ())))
    env[RUN_TOKEN_VAR] = secrets.token_hex(16)
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(interpreter).parent), env.get("PATH"))))
    entries = [str(checkout / rel) for rel in pythonpath]
    argv = [interpreter, "-I", "-B", str(runner), str(checkout), str(probe_copy), str(out)]
    argv.append(json.dumps(entries))
    profile = backend.policy(
        read_roots=(*pytest_config.interpreter_roots(interpreter), supervisor),
        write_roots=(checkout.parent, run_dir),
        mask_home_credentials=True,
    )
    profile_path = supervisor / "profile.sb"
    profile_path.write_bytes(profile.text.encode())
    log = run_dir / "probe.log"
    code, timed_out, survivors = confined.execute(
        backend.wrap(argv, profile_path), checkout, env, timeout, log
    )
    text = log.read_text(errors="replace") if log.exists() else ""
    return ProbeRun(code, timed_out, int(survivors), _read_output(out), text, backend.name)


def judge(run: ProbeRun, expected: Expected) -> OracleVerdict:
    """The oracle verdict of one probe run. Never PASS unless the run ended cleanly (exit 0, in
    time, nothing left running) and its one document equals ``expected``."""
    if run.timed_out or run.returncode != 0 or run.survivors or not run.output:
        return OracleVerdict(FAIL, NO_OUTPUT)
    try:
        document = parse_document(run.output)
    except Invalid:
        return OracleVerdict(FAIL, OUTPUT_INVALID)
    return OracleVerdict(PASS) if matches(document, expected) else OracleVerdict(FAIL, MISMATCH)
