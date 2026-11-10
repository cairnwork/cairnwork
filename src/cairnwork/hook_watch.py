"""Keep a post-verified hook in the runner's process group and stop it on runner loss."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time


def main() -> int:
    """Run ARGV until it exits, the parent pipe closes, or its wall-clock deadline passes."""
    seconds = int(sys.argv[1])
    argv = sys.argv[3:]
    deadline = time.monotonic() + seconds + 5
    try:
        hook_env = os.environ.copy()
        hook_env.pop("__CF_USER_TEXT_ENCODING", None)
        hook = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=hook_env)
        while True:
            code = hook.poll()
            if code is not None:
                return code if code >= 0 else 128 + -code
            if time.monotonic() >= deadline:
                os.killpg(0, signal.SIGKILL)
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)
            if readable and not os.read(sys.stdin.fileno(), 1):
                os.killpg(0, signal.SIGKILL)
    except BaseException:
        os.killpg(0, signal.SIGKILL)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
