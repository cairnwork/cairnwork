"""#1819: what the verifier account was running when reap() counted it (diagnostic text only).

reap() counts the account's processes but never saw their names, so a refusal said only "survivors".
Before the kill there is exactly ONE raw read: a bounded ps whose text is kept unparsed; the kill's
deadline moves by that read's elapsed time (itself bounded by the ps timeout) and by nothing else.
After the kill finishes, result() parses it, filters launchd daemons the count exempts (path
lookup, signature checks), sorts, drops the caller's main run and builds the entries, inline and
within BUDGET seconds of its own; past it the reasons say ``listing=incomplete`` and
``exempt=unknown``. Every word shown comes from survivor_argv.prefix() (allow-list, delimiters
replaced, cut) or from a strict pattern, so nothing a candidate chose is shown. A failure here
yields no listing and never changes the count, the verdict or the kill's deadline.

Limits, said so the reader is not misled: the listing and the count come from two separate reads,
so when they disagree (more rows than the count and the cut, fewer rows, nothing left after
filtering, or the 5-row cap hiding rows) the reasons carry ``listing=incomplete``; the count itself
is never changed. The caller's main run is found by identity (a row whose parent is no pid in the
account's ps output and not launchd). If main's own parent is a process of the account, or is
launchd, it is not recognised: nothing is dropped and ``main=unresolved`` is said. The path lookup
runs after the kill, so a daemon that is already gone has no path record: ``exempt=unknown`` and
the listing is not cut to the count.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable

from cairnwork import confined, survivor_argv, verifier_escape

REASON = "account-escaped"
UNRESOLVED, UNKNOWN = "main=unresolved", "exempt=unknown"
INCOMPLETE = "listing=incomplete"
BUDGET = 10.0  # seconds: the pre-kill ps read, and apart from it the whole post-kill listing
_MAX = survivor_argv.MAX_LISTED
_STAT = re.compile(r"[A-Za-z+<>]{1,5}")
_PPID_DIGITS = 10
_AGE = re.compile(r"[0-9]{1,3}(-[0-9]{2})?(:[0-9]{2}){1,2}")


def _plain_ppid(text: str) -> bool:
    return text.isascii() and text.isdigit() and len(text) <= _PPID_DIGITS


def _parse(raw: str, stop: float) -> list | None:
    """The rows of one ps text, or None when ``stop`` (a time.monotonic value) passed meanwhile."""
    rows = []
    for line in raw.split("\n"):
        if time.monotonic() >= stop:
            return None
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[0].isdigit() and _plain_ppid(parts[1]):
            rows.append(parts)
    return rows


def _exempt(rows: list, real_paths: Callable, stop: float) -> tuple[set[str], bool]:
    """(pids of launchd children that reap()'s count exempts, whether some path was unknown).
    The time is checked before the lookup and before every signature check; past ``stop`` the
    answer is "unknown" (nothing exempted). Inline: no thread is left behind."""
    launchd = [int(row[0]) for row in rows if row[1] == "1"]
    if not launchd or sys.platform != "darwin":
        return set(), False
    try:
        if time.monotonic() >= stop:
            return set(), True
        seen = real_paths(launchd) or {}
        found = set()
        for pid, rec in seen.items():
            if time.monotonic() >= stop:
                return set(), True
            if verifier_escape.is_exempt(rec[1], 1):
                found.add(str(pid))
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        return set(), True
    return found, len(seen) < len(launchd)


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


def _entry(stat: str, age: str, name: str, ppid: str, parent: str | None) -> str:
    shown_stat = stat if _STAT.fullmatch(stat) else "?"
    shown_age = age.replace(":", ".") if _AGE.fullmatch(age) else "?"
    up = survivor_argv.prefix(parent) if parent else "-"  # the parent's name, filtered the same
    return f"{survivor_argv.prefix(name)} {shown_stat} {shown_age} ppid={ppid}/{up}"


def before_kill(user: str, before: int, deadline: float) -> tuple[str | None, float]:
    """(the raw text of ONE bounded ps, the caller's deadline moved later by the time it took).
    Only when ``before`` > 0. Nothing is parsed here: the read takes at most the ps timeout, so
    the diagnostic never uses up the budget the kill needs (#1824 review). None: ps unreadable."""
    if before <= 0:
        return None, deadline
    start, raw = time.monotonic(), None
    try:
        timeout = min(max(deadline - start, 0.0) / 4, confined.PS_TIMEOUT_SECONDS, BUDGET / 2)
        flag = ["-x"] if sys.platform == "darwin" else []
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,stat=,etime=,comm="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
        if done.returncode in (0, 1) and not done.stderr.strip():
            raw = done.stdout
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        raw = None
    return raw, deadline + min(max(time.monotonic() - start, 0.0), BUDGET)


def result(
    count: int, raw: str | None, real_paths: Callable | None, main: int, before: int
) -> survivor_argv.Survivors:
    """``count`` with its listing and a fixed ``main=`` reason, built after the kill from the raw
    ps text. The listing is cut to ``count`` only when every choice behind it was certain (no
    ``main=unresolved``, no ``exempt=unknown``). Whenever the listing and the count disagree, in
    either direction, ``listing=incomplete`` says so; a failed ps (no text) stays a plain count,
    and so does any failure here."""
    if not count or raw is None:
        return survivor_argv.Survivors(count)
    try:
        return _listing(count, raw, real_paths, main, main if before > main else 0)
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        return survivor_argv.Survivors(count)


def _listing(
    count: int, raw: str, real_paths: Callable | None, main: int, drop: int
) -> survivor_argv.Survivors:
    stop = time.monotonic() + BUDGET
    late = (REASON, UNKNOWN, INCOMPLETE, f"main={main}")  # out of time: said, nothing guessed
    rows = _parse(raw, stop)
    if rows is None:
        return survivor_argv.Survivors(count, (), late)
    names = {row[0]: row[4] for row in rows}
    skip, unknown = _exempt(rows, real_paths, stop) if real_paths else (set(), False)
    if time.monotonic() >= stop:
        return survivor_argv.Survivors(count, (), late)
    rows = [row for row in rows if row[0] not in skip]
    rows.sort(key=lambda row: row[1] == "1")  # launchd children last: likelier system daemons
    rows, certain = _without_main(rows, drop, set(names))
    entries = []
    for r in rows[:_MAX]:
        if time.monotonic() >= stop:
            return survivor_argv.Survivors(count, tuple(entries), late)
        entries.append(_entry(r[2], r[3], r[4], r[1], names.get(r[1])))
    sure = certain and not unknown
    notes = (
        *(() if certain else (UNRESOLVED,)),
        *((UNKNOWN,) if unknown else ()),
        *((INCOMPLETE,) if len(rows) > _MAX or len(entries) != count else ()),
    )
    return survivor_argv.Survivors(
        count, tuple(entries[:count] if sure else entries), (REASON, *notes, f"main={main}")
    )
