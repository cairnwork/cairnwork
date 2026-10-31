"""Fakes for the PAUSE interfaces (#476): a scripted planner and gate, a dict checkout behind the
checkout guard, and an in-memory event log. No model, no network, no M5. Steps 2-4 reuse them."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from experimental.pause.interfaces import (
    GateReply,
    GateRequest,
    GuardUnavailable,
    PlanReply,
    PlanRequest,
)


class FakePlanner:
    """Returns (or raises) the scripted replies in order; records every request."""

    def __init__(self, replies: list[PlanReply | BaseException]) -> None:
        self.replies, self.requests = list(replies), []

    def plan(self, request: PlanRequest) -> PlanReply:
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class FakeGate:
    def __init__(self, replies: list[GateReply | BaseException]) -> None:
        self.replies, self.requests = list(replies), []

    def review(self, request: GateRequest) -> GateReply:
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class FakeGuard:
    """A checkout as {path: (content, mode)}; ``broken`` makes every call fail closed."""

    def __init__(self, files: dict[str, tuple[str, int]] | None = None) -> None:
        self.files = dict(files or {"src/app.py": ("x = 1\n", 0o644)})
        self.snapshots: dict[str, dict[str, tuple[str, int]]] = {}
        self.broken = False

    def digest(self) -> str:
        if self.broken:
            raise GuardUnavailable("digest failed")
        d = hashlib.sha256(json.dumps(sorted(self.files.items())).encode()).hexdigest()
        self.snapshots[d] = dict(self.files)
        return d

    def rollback(self, to: str) -> None:
        if self.broken or to not in self.snapshots:
            raise GuardUnavailable(f"no snapshot {to}")
        self.files = dict(self.snapshots[to])


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, dict[str, Any]]] = []

    def record(self, kind: str, **fields: Any) -> int:
        seq = len(self.events) + 1
        self.events.append((seq, kind, fields))
        return seq
