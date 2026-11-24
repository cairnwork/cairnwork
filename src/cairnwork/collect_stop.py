"""#1671: the parent's side of a quiet collect run (the #974 collected-set check).

A quiet run has the argv, the environment and the hand-over of a real run, so the code under test
cannot tell it from the run that executes its tests. The guard writes a collection record (``stage:
"collected"``, no outcomes) to its own destination in every run, the moment collection returned.
Only the parent decides what that means: for a quiet run it reads that destination on each poll
of the wait and, once a record with this run's nonce is readable, ends the run's own process group
(confined.execute). The child holds no bit; a collection record in a real run is never read, and
a final-record read never accepts one (report_guard.parse_verdict).

The verdict of a quiet run is the collection record when there is one, whatever the process did
after it (a fast test may finish before the next poll; a test the child started in the few
milliseconds before the kill is never read). Without one the run is read as any other, so a run
that never reached the end of collection (a crash, a timeout, an ``os._exit``) stays unusable:
nothing here lets a missing or forged record pass.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from cairnwork import report_guard
from cairnwork.verdict_record import Verdict


class CollectStop:
    """Callable for ``confined.execute(stop=...)``: True once a valid collection record is
    readable. ``record()`` then returns the record as it was first read, so a process that is
    still writing (one that escaped the kill) cannot change what the verdict is built from."""

    def __init__(self, read: Callable[[], Verdict]) -> None:
        self._read = read
        self._seen: Verdict | None = None

    def __call__(self) -> bool:
        if self._seen is None:
            try:
                got = self._read()
            except Exception:  # noqa: BLE001 - an unreadable frame never stops a run early
                return False
            if got.recorded:
                self._seen = got
        return self._seen is not None

    def record(self) -> Verdict | None:
        """The collection record of the run, read once more if no poll has seen one."""
        self()
        return self._seen


def watcher(spec, channel, verdict_path: Path, nonce: str) -> CollectStop | None:
    """The stop watcher of a quiet collect run (decided here, on the parent side only), else
    None. Owner run: the channel's collection descriptor. Account run: the file beside the
    report, in the run directory."""
    if not (spec.collect_only and not spec.announce):
        return None
    if channel is not None:
        return CollectStop(channel.read_collected)
    path = verdict_path.with_name(verdict_path.name + report_guard.COLLECTED_SUFFIX)
    return CollectStop(partial(report_guard.read_verdict, path, nonce, True))
