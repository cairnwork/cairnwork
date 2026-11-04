"""The parsed verifier record and its helpers, moved out of report_guard.py (pure move, #775
Step 0: report_guard.py is near the 400-line cap). Behaviour is unchanged; report_guard
re-exports ``Verdict`` and still builds it in ``parse_verdict``."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    ran: bool
    outcomes: dict[str, str]
    too_big: bool = False  # the file exceeds MAX_REPORT_BYTES: nothing could be read from it
    recorded: bool = False  # sessionfinish wrote a well-formed record with our nonce (#234)
    collect_errors: tuple[str, ...] = ()  # node ids of collectors that failed (B2, #234)
    collect_skipped: tuple[str, ...] = ()  # node ids of collectors skipped while collecting
    skipped_deps: tuple[str, ...] = ()  # modules the skipped collectors could not import (#813)
    renamed: tuple[str, ...] = ()  # items whose node id or file changed after collection
    paths: dict[str, str] = field(default_factory=dict)  # node id -> its file, as collected
    # #478: tests whose teardown failed. A shared fixture's finalizer runs at its last user's
    # teardown, so the node named is where the error landed, not the test that owns the fixture
    teardown_errors: tuple[str, ...] = ()
    # #775: why a failed node failed: "setup" (a fixture raised before the body) or "env" (every
    # failed call raised an OSError). Absent: an ordinary test failure. ``outcomes`` are unchanged.
    fail_kind: dict[str, str] = field(default_factory=dict)
    exitstatus: int | None = None  # pytest's own session status, as recorded (#276)
    testsfailed: int | None = None  # pytest's own count of failed tests, as recorded
    collected: tuple[str, ...] = ()  # #793: every node id pytest collected, as recorded
    collection_finished: bool = False  # #793: pytest_collection_finish ran (not interrupted)

    def not_run(self, targets) -> tuple[str, ...]:
        """The named ``targets`` (``path`` or ``path::node``) that own no recorded test, then the
        collectors skipped while collecting: a run that leaves any of them is not a pass, whatever
        else it ran. A test belongs to a target only when it was collected from the target's file
        (``paths``, taken from the item's path when pytest collected it, before any hook could
        reorder or rename items) and its node id is under the target."""
        missing = [t for t in targets if not any(self._owns(node, t) for node in self.outcomes)]
        return tuple(dict.fromkeys([*missing, *self.collect_skipped]))

    def _owns(self, nodeid: str, target: str) -> bool:
        return self.paths.get(nodeid) == target.partition("::")[0] and _within(nodeid, target)


def _within(nodeid: str, target: str) -> bool:
    """A test belongs to a target when it is that node, a node under it (``::``) or one of its
    parametrisations (``[``)."""
    return nodeid == target or nodeid.startswith((f"{target}::", f"{target}["))


def _sources(collected: list[str], files: list[str], file_of) -> dict[str, str]:
    """Each collected node id's file; ``""`` when it was not seen collected or one id names
    items of two files (then neither owns a target)."""
    paths: dict[str, str] = {}
    for node, n in zip(collected, file_of, strict=False):
        source = files[n] if n >= 0 else ""
        if paths.setdefault(node, source) != source:
            paths[node] = ""
    return paths
