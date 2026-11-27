"""Wait helper for the crash drills in test_recovery.py."""

import re
import subprocess
import time

import pytest

_PYTEST_RUN = re.compile(r"(?:^|/)python[\d.]*\s(?:\S+\s)*?-m\s+pytest(?:\s|$)")


def is_pytest_command(command: str) -> bool:
    """True for ``python ... -m pytest ...``; a path that merely contains "pytest" is not one."""
    return _PYTEST_RUN.search(command) is not None


def wait_for_sandboxed_pytest(root_pid: int, timeout: float = 90.0) -> None:
    """Block until a pytest process descends from ``root_pid``; fail loudly after ``timeout``.

    A fixed sleep killed the driver before the sandboxed pytest existed under load (no
    descendant to settle), so wait for the process itself instead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=True
        ).stdout
        rows = [line.split(None, 2) for line in out.splitlines() if line.strip()]
        table = {int(r[0]): (int(r[1]), r[2] if len(r) > 2 else "") for r in rows}
        found = {root_pid}
        grew = True
        while grew:
            grew = False
            for pid, (ppid, _) in table.items():
                if ppid in found and pid not in found:
                    found.add(pid)
                    grew = True
        if any(is_pytest_command(table[pid][1]) for pid in found - {root_pid}):
            return
        time.sleep(0.2)
    pytest.fail(f"no sandboxed pytest process appeared under driver {root_pid} in {timeout}s")
