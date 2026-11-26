"""#1819: what the verifier account was running when reap() counted it (diagnostic text only).

reap() counts the account's processes but never saw their names, so a refusal said only "survivors".
One extra bounded ps, taken before the kill, gives each process's allow-listed name prefix,
state and age. Every word comes from survivor_argv.prefix() (allow-list, delimiters replaced,
cut) or from a strict pattern, so nothing a candidate chose is shown. A failure here yields
no listing and never changes the count or the verdict.

Limits, said so the reader is not misled: the listing and the count come from two separate reads,
so when they disagree (more rows than the count and the cut, fewer rows, nothing left after
filtering, or the 5-row cap hiding rows) the reasons carry ``listing=incomplete``; the count itself
is never changed. The caller's main run is found by identity (a row whose parent is no pid in the
account's ps output and not launchd). If main's own parent is a process of the account, or is
launchd, it is not recognised: nothing is dropped and ``main=unresolved`` is said. The whole
snapshot (ps, path lookup, signature checks) is held to BUDGET seconds.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from cairnwork import confined, survivor_argv, verifier_escape

REASON = "account-escaped"
UNRESOLVED, UNKNOWN = "main=unresolved", "exempt=unknown"
INCOMPLETE = "listing=incomplete"
BUDGET = 10.0  # seconds for the whole snapshot, however slow ps, the path lookup or codesign are
_MAX = survivor_argv.MAX_LISTED
_STAT = re.compile(r"[A-Za-z+<>]{1,5}")
_PPID_DIGITS = 10
_AGE = re.compile(r"[0-9]{1,3}(-[0-9]{2})?(:[0-9]{2}){1,2}")


def _plain_ppid(text: str) -> bool:
    return text.isascii() and text.isdigit() and len(text) <= _PPID_DIGITS


def _exempt(rows: list, real_paths: Callable, budget: float) -> tuple[set[str], bool]:
    """(pids of launchd children that reap()'s count exempts, whether some path was unknown).
    The lookup and every signature check together get ``budget`` seconds; past it the answer is
    "unknown" (nothing exempted) and the work is left behind."""
    launchd = [int(row[0]) for row in rows if row[1] == "1"]
    if not launchd or sys.platform != "darwin":
        return set(), False
    done: dict = {}

    def work() -> None:
        try:
            seen = real_paths(launchd) or {}
            found = {str(p) for p, rec in seen.items() if verifier_escape.is_exempt(rec[1], 1)}
            done["n"], done["found"] = len(seen), found
        except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
            return

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(budget, 0.0))
    if "found" not in done:
        return set(), True
    return done["found"], done["n"] < len(launchd)


def _without_main(rows: list, drop: int, pids: set[str]) -> tuple[list, bool]:
    """(rows without the caller's main run, whether that was certain). main is the harness's own
    collector: a row whose parent is not any pid ps showed (``pids``, before exempt rows were
    removed) and not launchd. Exactly ``drop`` such rows: they go. Any other number: nothing is
    guessed, every row stays and the caller says the choice is unresolved."""
    if drop <= 0:
        return rows, True
    out = [i for i, row in enumerate(rows) if row[1] not in pids and row[1] != "1"]
    if len(out) != drop:
        return rows, False
    return [row for i, row in enumerate(rows) if i not in out], True


def snapshot(
    user: str, timeout: float, real_paths: Callable | None = None, drop: int = 0
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(argv entries, reasons) for ``user``'s processes; ((), ()) when ps cannot be read.
    ``timeout`` is the time left to the caller; at most a quarter (and PS_TIMEOUT_SECONDS) is
    used for ps (the exempt-path lookup is a separate bounded call). ``drop`` rows that are the
    caller's main run are left out (see _without_main); when that is uncertain or a path is
    unknown, nothing is guessed and the reasons say so."""
    try:
        start = time.monotonic()
        timeout = min(timeout / 4, confined.PS_TIMEOUT_SECONDS, BUDGET / 2)
        flag = ["-x"] if sys.platform == "darwin" else []
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,stat=,etime=,comm="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
        if done.returncode not in (0, 1) or done.stderr.strip():
            return (), ()
        rows = []
        for line in done.stdout.split("\n"):
            parts = line.split(None, 4)
            if len(parts) == 5 and parts[0].isdigit() and _plain_ppid(parts[1]):
                rows.append(parts)
        names = {row[0]: row[4] for row in rows}
        left = BUDGET - (time.monotonic() - start)
        skip, unknown = _exempt(rows, real_paths, left) if real_paths else (set(), False)
        rows = [row for row in rows if row[0] not in skip]
        rows.sort(key=lambda row: row[1] == "1")  # launchd children last: likelier system daemons
        rows, certain = _without_main(rows, drop, set(names))
        entries = [_entry(r[2], r[3], r[4], r[1], names.get(r[1])) for r in rows[:_MAX]]
        notes = (
            *(() if certain else (UNRESOLVED,)),
            *((UNKNOWN,) if unknown else ()),
            *((INCOMPLETE,) if len(rows) > _MAX else ()),
        )
        return tuple(entries), (REASON, *notes)
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        return (), ()


def _entry(stat: str, age: str, name: str, ppid: str, parent: str | None) -> str:
    shown_stat = stat if _STAT.fullmatch(stat) else "?"
    shown_age = age.replace(":", ".") if _AGE.fullmatch(age) else "?"
    up = survivor_argv.prefix(parent) if parent else "-"  # the parent's name, filtered the same
    return f"{survivor_argv.prefix(name)} {shown_stat} {shown_age} ppid={ppid}/{up}"


def before_kill(
    user: str, before: int, deadline: float, real_paths: Callable | None = None, main: int = 0
) -> tuple[tuple, float]:
    """The snapshot (only if ``before`` > 0) and the caller's deadline moved later by the time it
    took: the diagnostic never uses up the budget the kill needs (#1824 review)."""
    if before <= 0:
        return ((), ()), deadline
    start = time.monotonic()
    seen = snapshot(user, max(deadline - start, 0.0), real_paths, main)
    return seen, deadline + min(time.monotonic() - start, BUDGET)


def result(count: int, seen: tuple, main: int) -> survivor_argv.Survivors:
    """``count`` with its listing and a fixed ``main=`` reason. The listing is cut to ``count``
    only when every choice behind it was certain (no ``main=unresolved``, no ``exempt=unknown``).
    Whenever the listing and the count disagree, in either direction, ``listing=incomplete`` says
    so; a failed ps (no reasons at all) stays a plain count."""
    argv, notes = seen
    if not count or not notes:
        return survivor_argv.Survivors(count)
    sure = UNRESOLVED not in notes and UNKNOWN not in notes
    gap = () if INCOMPLETE in notes or len(argv) == count else (INCOMPLETE,)
    return survivor_argv.Survivors(
        count, argv[:count] if sure else argv, (*notes, *gap, f"main={main}")
    )
