"""Judging a recorded deny stream (#797 step 1; the recorder is deny_channel.py): which lines are
the run's (its tag), the window between the canaries, and the fail-closed health and volume rules.
Pure: no process, no file of its own."""

from __future__ import annotations

import io
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from cairnwork.textclean import replace_hidden

TAG_PREFIX = "cwvtag-"
TAG = re.compile(r"cwvtag-[0-9a-f]{16}")
MAX_WINDOW_LINES = 2000  # whole stream, opening canary to stop (O3c: our loss from ~7000 lines/s)
BACKGROUND_PER_SECOND = 50  # added to MAX_WINDOW_LINES per second of window: long runs (step 1)
MAX_LINES_PER_SECOND = 1000  # any one-second bucket (O3c: 1018 foreign lines, nothing lost)
STALL_SECONDS = 10.0  # no line at all for this long inside the window (background ~7 lines/s)
MAX_LISTED = 8  # distinct substantive denials named in DENIED evidence
LINE = re.compile(
    r"(?:(\d+) duplicate reports? for )?Sandbox: (.+?)\((\d+)\) deny\(\d+\) (\S+) ?(.*)", re.S
)
# another process's profile may also report allows: a line, not a denial. Tried only after LINE,
# so a process name shaped like "x(1) allow(9)" cannot turn a deny line into an allow line.
ALLOW = re.compile(
    r"(?:(\d+) duplicate reports? for )?Sandbox: .+?\(\d+\) allow(?:\(\d+\))? (?!deny)\S+(?: .*)?",
    re.S,
)
POLICY = re.compile(r"(?:\d+ duplicate reports? for )?System Policy:")  # CI 11/05: with the prefix
# (operation, exact target) every sandboxed Python start hits (step 0); human-reviewed, exact
# paths only. Reads that can change behaviour (/private/etc/ssl, preferences) are never benign.
BENIGN_DENIES = frozenset(
    {
        ("system-info", "vfs.disk-space"),
        ("mach-lookup", "com.apple.system.opendirectoryd.libinfo"),
        ("mach-lookup", "com.apple.system.notification_center"),
        ("file-ioctl", 'path:/dev/dtracehelper ioctl-command:(_IO "h" 4)'),
        ("file-read-data", "/System/Volumes/Preboot/Cryptexes/OS"),
        ("file-read-data", "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld"),
        ("file-read-data", "/System/Volumes/Preboot/Cryptexes/Rosetta/System/Library/dyld"),
        ("network-outbound", "/private/var/run/syslog"),
    }
)


@dataclass(frozen=True)
class Evidence:
    state: str  # DENIED, CLEAN or UNAVAILABLE
    detail: str = ""  # DENIED: the first distinct "operation target"s; UNAVAILABLE: why
    window_lines: int = 0
    peak_per_second: int = 0

    def __str__(self) -> str:
        detail = f" {self.detail}" if self.detail else ""
        return f"{self.state}:{detail} (lines {self.window_lines}, peak {self.peak_per_second}/s)"


UNSUPPORTED = Evidence("UNAVAILABLE", "no deny evidence on this backend")


@dataclass
class Window:
    """What ``judge`` needs besides the stream: the run's tag and its canaries."""

    tag: str
    canaries: list[tuple[int, str]] = field(default_factory=list)  # (pid, "remote:*:<port>")
    problem: str = ""  # a recorder failure; the evidence is UNAVAILABLE with it
    retired: list[tuple[int, str]] = field(default_factory=list)  # canaries not seen in time
    inode: int = -1  # of the stream file when created: a replaced file is UNAVAILABLE
    offset: int = 0  # how far the canary search has read the stream file


def judge(raw: bytes, window: Window) -> Evidence:
    """The evidence a recorded stream gives; anything it cannot read is UNAVAILABLE."""
    return judge_stream(io.BytesIO(raw), window)


def judge_stream(handle, window: Window) -> Evidence:
    """``judge`` over a binary file, one line at a time (the file is never held whole)."""
    if window.problem:
        return Evidence("UNAVAILABLE", window.problem)
    if not handle.readline().startswith(b"Filtering the log data"):
        return Evidence("UNAVAILABLE", "no stream header")
    tally = _Tally(set(window.canaries), set(window.retired), len(window.canaries) == 2)
    try:
        for raw in handle:
            if not raw.endswith(b"\n"):
                return Evidence("UNAVAILABLE", "the stream ends in a half-written line")
            if line := raw.decode("utf-8", errors="replace").strip():
                tally.add(_event(line, window))
        return tally.evidence()
    except ValueError as exc:  # also a timestamp _moment cannot read
        return Evidence("UNAVAILABLE", str(exc))


def _event(line: str, window: Window):
    """(second, count, ours, operation, target, pid) of one ndjson record; None if not a deny."""
    try:
        record = json.loads(line)
        message, stamp = record["eventMessage"], record["timestamp"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("an unreadable stream line") from exc
    if not isinstance(message, str) or not isinstance(stamp, str):
        raise ValueError("an unreadable stream line")
    if POLICY.match(message):  # the account's own kill -9 -1, a daemon's allow: not a sandbox deny
        return None
    body, _, suffix = message.rpartition("\n") if "\n" in message else (message, "", "")
    if suffix.startswith(TAG_PREFIX) and not TAG.fullmatch(suffix):
        raise ValueError("a malformed tag")
    ours = suffix == window.tag
    match = LINE.fullmatch(body)
    if match is None:
        allow = ALLOW.fullmatch(body)
        if allow is None:
            raise ValueError(f"an unparseable Sandbox line: {body[:120]!r}")
        if ours:  # the tag is only on the run's denies
            raise ValueError("a tagged allow line")
        return (stamp[:19], int(allow.group(1) or 1), False, None, None, 0)  # volume only
    count = int(match.group(1) or 1)
    return (stamp[:19], count, ours, match.group(4), match.group(5), int(match.group(3)))


class _Tally:
    """The window's counts, kept line by line from the opening canary's line on."""

    def __init__(self, canaries: set, retired: set, complete: bool) -> None:
        # a retired canary's line may still arrive late: never a denial of the run, never required
        self.canaries, self.ignored = canaries, canaries | retired
        self.complete, self.opened = complete, False
        self.total, self.buckets = 0, Counter()
        self.seen: set[tuple[int, str]] = set()
        self.found: list[str] = []
        self.first = self.last = self.gap = 0.0

    def add(self, event) -> None:
        if event is None:
            return
        second, count, ours, operation, target, pid = event
        if not self.opened and not (ours and (pid, target) in self.canaries):
            return  # before the window
        moment = _moment(second)
        if self.opened:
            self.gap = max(self.gap, moment - self.last)
        else:
            self.opened, self.first = True, moment
        self.last = moment
        self.total += count
        self.buckets[second] += count
        if ours:
            self.seen.add((pid, target))
            if (pid, target) not in self.ignored and (operation, target) not in BENIGN_DENIES:
                # the target is the run's own text (a path it chose): hidden characters go (#740)
                text = replace_hidden(f"{operation} {target}"[:200])
                if text not in self.found and len(self.found) < MAX_LISTED:
                    self.found.append(text)

    def evidence(self) -> Evidence:
        if not self.opened or not self.complete:
            return Evidence("UNAVAILABLE", "a canary line is missing")
        total, peak = self.total, max(self.buckets.values())
        if not self.canaries <= self.seen:
            return Evidence("UNAVAILABLE", "a canary line is missing", total, peak)
        if self.gap > STALL_SECONDS:
            return Evidence("UNAVAILABLE", f"the stream stalled {self.gap:.0f}s", total, peak)
        cap = MAX_WINDOW_LINES + BACKGROUND_PER_SECOND * (self.last - self.first + 1)
        if total > cap or peak > MAX_LINES_PER_SECOND:
            return Evidence(
                "UNAVAILABLE", "stream volume over the cap: lines may be lost", total, peak
            )
        if self.found:  # the first few distinct ones, in stream order
            return Evidence("DENIED", " | ".join(self.found), total, peak)
        return Evidence("CLEAN", "", total, peak)


@lru_cache(maxsize=256)
def _moment(second: str) -> float:
    return time.mktime(time.strptime(second, "%Y-%m-%d %H:%M:%S"))
