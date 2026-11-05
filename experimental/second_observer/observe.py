"""Run the observer in the verifier's sandbox and read its result (AC4).

Same backend, same read roots and the same write roots as ``verifiers._run_one_in``: the
interpreter, the run directory and the checkout's parent. The environment is the verifier's
minimal one. The result file is written by the trusted runner inside run_dir; the candidate can
reach it (same process), so every row is marked same_process."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import cairnwork_sandbox
from cairnwork import confined, pytest_config
from cairnwork.confined import RUN_TOKEN_VAR
from experimental.second_observer import FAIL, NA, OBS_ERROR, PASS, TIMEOUT
from experimental.second_observer.inputs import ShadowInput

RUNNER = Path(__file__).with_name("runner.py")
MAX_RESULT_BYTES = 1 << 20
ENV_KEEP = ("PATH", "LANG", "LC_ALL")
STATUSES = frozenset({PASS, FAIL, NA, OBS_ERROR})


@dataclass(frozen=True)
class Observation:
    status: str
    detail: str = ""
    returncode: int | None = None
    sys_path: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()


def _read_result(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_RESULT_BYTES + 1)
        data = json.loads(raw) if len(raw) <= MAX_RESULT_BYTES else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("status") in STATUSES else None


def run_observer(
    inp: ShadowInput, checkout: Path, shadow: Path, timeout: int = 120, *, sandbox: bool = True
) -> Observation:
    """Observe one target in ``checkout``. ``sandbox=False`` exists only for the AC4 negative test
    (proof that the sandbox, not the host, denies the network); the CLI never passes it."""
    backend = cairnwork_sandbox.select()
    if backend is None and sandbox:
        return Observation(OBS_ERROR, "no sandbox backend available: observer not executed")
    run_dir, supervisor = shadow / "run", shadow / "supervisor"
    home = run_dir / "home"
    for directory in (supervisor, run_dir, home):
        directory.mkdir(parents=True, exist_ok=True)
    runner = supervisor / "runner.py"
    shutil.copyfile(RUNNER, runner)
    runner.chmod(0o444)
    result = run_dir / "result.json"
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update(HOME=str(home), TMPDIR=str(home))
    env[RUN_TOKEN_VAR] = secrets.token_hex(16)
    argv = [
        inp.interpreter,
        "-I",
        "-B",
        str(runner),
        str(checkout),
        inp.file,
        inp.name,
        str(result),
    ]
    argv.append(json.dumps([str(checkout / rel) for rel in inp.pythonpath]))
    if backend is not None:
        env.update(getattr(backend, "run_env", ()))
    env["PATH"] = os.pathsep.join(
        filter(None, (str(Path(inp.interpreter).parent), env.get("PATH")))
    )
    log = run_dir / "observer.log"
    if sandbox and backend is not None:
        # Same arguments as verifiers._run_one_in (src/cairnwork/verifiers.py:310-314): the
        # interpreter roots, write roots (checkout.parent, run_dir), owner mode (home masked).
        profile = backend.policy(
            read_roots=(*pytest_config.interpreter_roots(inp.interpreter), supervisor),
            write_roots=(checkout.parent, run_dir),
            mask_home_credentials=True,
        )
        profile_path = supervisor / "profile.sb"
        profile_path.write_bytes(profile.text.encode())
        argv = backend.wrap(argv, profile_path)
    code, timed_out, survivors = confined.execute(argv, checkout, env, timeout, log)
    return _observation(_read_result(result), code, timed_out, survivors)


def _observation(data, code, timed_out, survivors) -> Observation:
    if timed_out:
        return Observation(TIMEOUT, "observer timed out", code)
    if survivors:
        return Observation(OBS_ERROR, "a process escaped the run", code)
    if data is None:
        return Observation(OBS_ERROR, "no result from the observer", code)
    return Observation(
        data["status"],
        str(data.get("detail", ""))[:300],
        code,
        tuple(str(p) for p in data.get("sys_path", ())),
        tuple(sorted((str(k), str(v)) for k, v in dict(data.get("env", {})).items())),
    )
