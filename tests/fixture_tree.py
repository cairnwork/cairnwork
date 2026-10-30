"""Host-side fixture trees for the bwrap credential-mask tests, kept off /tmp and /run (#391).

A Linux run is to see /tmp and /run as private, empty trees. A canary under pytest's ``tmp_path``
(under /tmp on Linux) would then be absent inside the run, and a mask witness that skips an
unreadable path would pass without testing anything. These trees live under /var/tmp instead:
outside both trees, and world-traversable (1777) on Linux and macOS, so the account-path
reachability walk (which runs from ``/``) can pass too. A real-run witness also puts a plain
control file beside its canaries (``reach_env``), which must stay readable inside the run.
"""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

BASE = "/var/tmp"  # noqa: S108 - a fresh mkdtemp directory per test, removed afterwards
REACH = "CW-REACH-OK\n"
# Candidate-side check, pasted into a witness's test function body: every control file listed in
# CW_REACH must be readable with its text, so a fixture the run cannot see fails the witness.
REACH_CHECK = (
    "    reach = [p for p in os.environ['CW_REACH'].split(':') if p]\n"
    "    assert reach, 'no reach control listed'\n"
    "    for path in reach:\n"
    f"        assert open(path).read() == {REACH!r}, 'fixture not reachable: ' + path\n"
)


@contextlib.contextmanager
def world_dir(prefix: str):
    """A fresh 0755 directory under ``BASE``, removed afterwards (0700 subdirs reopened first)."""
    base = Path(tempfile.mkdtemp(dir=BASE, prefix=prefix))
    os.chmod(base, 0o755)
    try:
        yield base
    finally:
        for root, dirs, _ in os.walk(base):
            for d in dirs:
                with contextlib.suppress(OSError):
                    os.chmod(os.path.join(root, d), 0o700)
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
