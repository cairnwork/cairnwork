"""Keep a post-verified hook in the runner's session and stop it on runner loss.

Descendants that call setsid() to enter a new session are not covered. Killing the
watchdog and runner at the same time is also not covered. On a normal hook exit the
watchdog never kills its own group (that would replace the hook's status; the runner cleans the
group); a descendant still forking when the 3 s scan ends, or a pid reused between listing and
kill, can survive. A hook that exits within its limit keeps its status; the post-exit scan is
bounded by 0.5 s and the runner allows 1 s for it.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time

EXIT_SCAN_SECONDS = 0.5


def _kill_session(*, kill_group: bool = True, budget: float = 3.0) -> None:
    """Kill processes in this session, then kill the watchdog's process group."""
    session_id = os.getpid()
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["/bin/ps", "-A", "-o", "pid=,stat="],  # absolute: not the hook's PATH
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is None or result.returncode != 0:
            time.sleep(0.05)
            continue
        pids: list[int] = []
        for row in result.stdout.splitlines():
            fields = row.split()
            if len(fields) != 2:
                continue
            try:
                pid = int(fields[0])
                stat = fields[1]
                if (
                    not stat.startswith("Z")
                    and pid > 0
                    and pid != session_id
                    and os.getsid(pid) == session_id
                ):
                    pids.append(pid)
            except (ValueError, OSError):  # gone, or another user's/session's (macOS EPERM)
                continue
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not pids:
            break
        time.sleep(0.05)
    if kill_group:
        os.killpg(0, signal.SIGKILL)


def main() -> int:
    """Run ARGV until it exits, the parent pipe closes, or its wall-clock deadline passes."""
    seconds = int(sys.argv[1])
    argv = sys.argv[3:]
    deadline = time.monotonic() + seconds + 5
    try:
        hook_env = os.environ.copy()  # what Python itself adds is not the hook's (#1125 review):
        hook_env.pop("__CF_USER_TEXT_ENCODING", None)  # macOS CoreFoundation
        hook_env.pop("LC_CTYPE", None)  # PEP 538 C-locale coercion; _env never passes it
        hook = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=hook_env)
        while True:
            code = hook.poll()
            if code is not None:
                # Preserve the hook's status; killing our own group would replace it with SIGKILL.
                _kill_session(kill_group=False, budget=EXIT_SCAN_SECONDS)
                return code if code >= 0 else 128 + -code
            if time.monotonic() >= deadline:
                _kill_session()
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)
            if readable and not os.read(sys.stdin.fileno(), 1):
                _kill_session()
    except BaseException as exc:
        try:  # the record keeps why (e.g. Exec format error) before the group goes
            os.write(2, f"hook_watch: {type(exc).__name__}: {exc}\n".encode(errors="replace"))
        finally:
            _kill_session()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
