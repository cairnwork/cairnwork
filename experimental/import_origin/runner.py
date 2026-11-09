"""Run the real pytest target under Cairnwork's sandbox and collect its audit pipe."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import cairnwork_sandbox
from cairnwork import confined, pytest_config, report_channel, report_guard
from cairnwork.confined import RUN_TOKEN_VAR
from cairnwork.pycache_guard import PYCACHE_DIR
from experimental.import_origin import audit_bootstrap
from experimental.second_observer.inputs import ShadowInput

MAX_CAPTURE = 4 << 20
ENV_KEEP = ("PATH", "LANG", "LC_ALL")


@dataclass(frozen=True)
class Run:
    returncode: int | None
    events: tuple[dict, ...]
    capture: str


def _drain(fd: int, rows: list[bytes], finished: threading.Event) -> None:
    total = 0
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        remaining = MAX_CAPTURE - total
        if remaining > 0:
            kept = chunk[:remaining]
            rows.append(kept)
            total += len(kept)
        if len(chunk) > remaining:
            finished.set()
    os.close(fd)


def _events(chunks: list[bytes]) -> tuple[dict, ...]:
    rows = []
    for line in b"".join(chunks).splitlines():
        try:
            item = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(item, dict) and isinstance(item.get("module"), str):
            rows.append(item)
    return tuple(rows)


def run_pytest(inp: ShadowInput, checkout: Path, out: Path, timeout: int = 120) -> Run:
    backend = cairnwork_sandbox.select()
    if backend is None:
        return Run(None, (), "complete")
    run_dir, supervisor = out / "run", out / "supervisor"
    home, tmp = run_dir / "home", run_dir / "tmp"
    for directory in (run_dir, supervisor, home, tmp):
        directory.mkdir(parents=True, exist_ok=True)
    guard = supervisor / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    guard.chmod(0o750)
    ini = supervisor / "pytest.ini"
    settings = pytest_config.verifier_settings(inp.repo, inp.base, inp.pythonpath)
    plugins = pytest_config.installed_plugins(inp.interpreter)
    ini.write_bytes(settings.ini_text(checkout, plugins))
    bootstrap = supervisor / "audit_bootstrap.py"
    shutil.copyfile(audit_bootstrap.__file__, bootstrap)
    bootstrap.chmod(0o444)
    read_fd, write_fd = os.pipe()
    chunks: list[bytes] = []
    truncated = threading.Event()
    reader = threading.Thread(target=_drain, args=(read_fd, chunks, truncated), daemon=True)
    reader.start()
    env = {key: os.environ[key] for key in ENV_KEEP if key in os.environ}
    env.update(
        HOME=str(home),
        TMPDIR=str(tmp),
        PATH=os.pathsep.join(filter(None, (str(Path(inp.interpreter).parent), env.get("PATH")))),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPYCACHEPREFIX=str(tmp / PYCACHE_DIR),
        HYPOTHESIS_STORAGE_DIRECTORY=str(tmp / "hypothesis"),
        PYTHONNOUSERSITE="1",
        PYTHON_DISABLE_REMOTE_DEBUG="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=str(guard),
    )
    env[RUN_TOKEN_VAR] = secrets.token_hex(16)
    env.update(getattr(backend, "run_env", ()))
    channel = report_channel.open_channel(run_dir)
    pytest_args = [*settings.args(plugins), "-p", report_guard.MODULE]
    argv = [
        inp.interpreter,
        "-P",
        "-B",
        str(bootstrap),
        str(write_fd),
        str(checkout),
        str(ini),
        f"{inp.file}::{inp.name}",
        str(guard),
        json.dumps(pytest_args),
    ]
    env.update(channel.env())
    profile = backend.policy(
        read_roots=(*pytest_config.interpreter_roots(inp.interpreter), supervisor),
        write_roots=(checkout.parent, run_dir),
        mask_home_credentials=True,
    )
    profile_path = supervisor / "profile.sb"
    profile_path.write_bytes(profile.text.encode())
    log = run_dir / "pytest.log"
    code = None
    try:
        code, timed_out, _survivors = confined.execute(
            backend.wrap(argv, profile_path),
            checkout,
            env,
            timeout,
            log,
            pass_fds=(*channel.pass_fds, write_fd),
        )
        if timed_out:
            code = None
    finally:
        os.close(write_fd)
        channel.close()
        reader.join(timeout=10)
    return Run(code, _events(chunks), "truncated" if truncated.is_set() else "complete")
