"""Name the processes that keep a takeover fence, for the operator (#244).

The names come from processes the attempt may control (``ps -o comm=`` on macOS prints argv[0]
verbatim; Linux ``/proc/<pid>/comm`` is set by the process too), and they go into the ledger's
refusal, the route ``detail`` and ``status``. So each is cut down to a short, safe word: the
part before the first whitespace, its basename, at most NAME_CHARS characters, and every
character outside [A-Za-z0-9._+-] replaced by "?". At most ``limit`` processes are named.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cairnwork.confined import PS_TIMEOUT_SECONDS

NAME_CHARS = 32
_UNSAFE = re.compile(r"[^A-Za-z0-9._+-]")


def describe(pids: tuple[int, ...], limit: int = 20) -> str:
    """e.g. ``123 ssh-agent, 456 python3``; a name that cannot be read is "?"; past ``limit``
    only the number of the others is given."""
    names = _names(pids[:limit]) if pids else {}
    shown = ", ".join(f"{pid} {safe_name(names.get(pid, ''))}" for pid in pids[:limit])
    return shown + (f" and {len(pids) - limit} more" if len(pids) > limit else "")


def safe_name(raw: str) -> str:
    words = raw.split(None, 1)
    base = words[0].rsplit("/", 1)[-1] if words else ""
    return _UNSAFE.sub("?", base[:NAME_CHARS]) or "?"


def _names(pids: tuple[int, ...]) -> dict[int, str]:
    """{pid: raw name} of those that could be read (unsafe: see safe_name)."""
    if sys.platform.startswith("linux"):
        found = {}
        for pid in pids:
            try:
                with open(f"/proc/{pid}/comm", errors="replace") as handle:
                    found[pid] = handle.read(4096)
            except OSError:
                continue
        return found
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, pids are integers
            ["/bin/ps", "-o", "pid=,comm=", "-p", ",".join(map(str, pids))],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    rows = (line.split(None, 1) for line in done.stdout.splitlines())
    return {int(r[0]): r[1] for r in rows if len(r) == 2 and r[0].isdigit()}
