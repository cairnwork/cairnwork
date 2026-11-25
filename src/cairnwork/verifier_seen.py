"""#1819: what the verifier account was running when reap() counted it (diagnostic text only).

reap() counts the account's processes but never saw their names, so a refusal said only "survivors".
One extra bounded ps, taken before the kill, gives each process's allow-listed name prefix,
state and age. Every word comes from survivor_argv.prefix() (allow-list, delimiters replaced,
cut) or from a strict pattern, so nothing a candidate chose is shown. A failure here yields
no listing and never changes the count or the verdict.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cairnwork import survivor_argv

REASON = "account-escaped"
_STAT = re.compile(r"[A-Za-z+<>]{1,5}")
_AGE = re.compile(r"[0-9]{1,2}(-[0-9]{2})?(:[0-9]{2}){1,2}")


def snapshot(user: str, timeout: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(argv entries, reasons) for ``user``'s processes; ((), ()) when ps cannot be read."""
    try:
        flag = ["-x"] if sys.platform == "darwin" else []
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,stat=,etime=,comm="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
        if done.returncode not in (0, 1) or done.stderr.strip():
            return (), ()
        entries = []
        for line in done.stdout.split("\n"):
            parts = line.split(None, 4)
            if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit():
                entries.append(_entry(parts[2], parts[3], parts[4]))
        return tuple(entries[: survivor_argv.MAX_LISTED]), ((REASON,) if entries else ())
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        return (), ()


def _entry(stat: str, age: str, name: str) -> str:
    shown_stat = stat if _STAT.fullmatch(stat) else "?"
    shown_age = age.replace(":", ".") if _AGE.fullmatch(age) else "?"
    return f"{survivor_argv.prefix(name)} {shown_stat} {shown_age}"
