"""Host-side fixture trees for the bwrap credential-mask tests, kept off the private trees (#391).

A Linux run is to see /tmp and /run as private, empty trees. A canary under pytest's ``tmp_path``
(under /tmp on Linux) would then be absent inside the run, and a mask witness that skips an
unreadable path would pass without testing anything. These trees live outside the private trees
instead (/var/tmp, or /var/lib on Linux since /var/tmp is private too, #471 step 2b), and are
world-traversable (1777), so the account-path reachability walk (which runs from ``/``) can pass
too. A real-run witness also puts a plain
control file beside its canaries (``reach_env``), which must stay readable inside the run.
"""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = "/var/tmp"  # noqa: S108 - a fresh mkdtemp directory per test, removed afterwards
LINUX_BASE = "/var/lib/cw-fixtures"  # /var/tmp is a private tree on Linux too since #471 step 2b
REACH = "CW-REACH-OK\n"
# Candidate-side check, pasted into a witness's test function body: every control file listed in
# CW_REACH must be readable with its text, so a fixture the run cannot see fails the witness.
REACH_CHECK = (
    "    reach = [p for p in os.environ['CW_REACH'].split(':') if p]\n"
    "    assert reach, 'no reach control listed'\n"
    "    for path in reach:\n"
    f"        assert open(path).read() == {REACH!r}, 'fixture not reachable: ' + path\n"
)


def _base() -> str:
    """Linux: a 1777 directory outside the private trees (made with sudo); else BASE."""
    if sys.platform != "linux":
        return BASE
    made = subprocess.run(  # noqa: S603
        ["sudo", "-n", "install", "-d", "-m", "1777", LINUX_BASE],  # noqa: S607
        capture_output=True,
        timeout=30,
        check=False,
    )
    return LINUX_BASE if made.returncode == 0 else BASE


@contextlib.contextmanager
def world_dir(prefix: str):
    """A fresh 0755 directory under ``BASE``, removed afterwards (0700 subdirs reopened first; a
    link to a directory is not followed, so its target's mode is left alone)."""
    base = Path(tempfile.mkdtemp(dir=_base(), prefix=prefix))
    os.chmod(base, 0o755)
    try:
        yield base
    finally:
        for root, dirs, _ in os.walk(base):
            for d in dirs:
                if not os.path.islink(path := os.path.join(root, d)):
                    with contextlib.suppress(OSError):
                        os.chmod(path, 0o700)
        shutil.rmtree(base, ignore_errors=True)


def world_base(request, prefix: str) -> Path:
    """``world_dir`` for the rest of the test, removed by the test's finalizer."""
    stack = contextlib.ExitStack()
    request.addfinalizer(stack.close)
    return stack.enter_context(world_dir(prefix))


def reach_env(monkeypatch, backend, *where: Path) -> tuple[tuple[str, ...], str]:
    """Under bwrap, a plain control file in each of ``where``, listed in CW_REACH: (the env name to
    pass into the run, the candidate code that checks it). Seatbelt denies every path outside the
    run's roots, so it gets none."""
    if getattr(backend, "name", None) != "bwrap":
        return (), ""
    controls = [d / "cw-reach-control.txt" for d in where]
    for control in controls:
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text(REACH)
    monkeypatch.setenv("CW_REACH", ":".join(str(c) for c in controls))
    return ("CW_REACH",), REACH_CHECK
