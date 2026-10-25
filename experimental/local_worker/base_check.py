"""The observed tree and the start check of a local worker run (#88, #261), split from agent.py.

``observe`` is the fixed status query. With a start allow-list (untracked_allow.py) it also runs
the wrapper's digest query in the same bookkeeping step (one call per observation, whatever the
number of allowed files), and the tree's entries carry one ``r <sha256> <root>`` record per
allowed root: the hash of the digest records under it, so a change to any file under an allowed
root (content, mode, a new or removed file) changes the observed tree while the recorded tree
stays small. Without a list the digest query is never called, so an installed wrapper older than
it still serves such runs.

``_check_base`` refuses a start that is not the clean pinned base: with no list, any status
entry refuses, exactly as before #261. ``_own_patch`` turns the final numstat into the worker's
own count: with a list, less the base's count (the allowed files' lines), and null when a file
present at base under an allowed root changed. AgentLoop inherits it.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING

from experimental.local_worker.gitstate import TreeState, parse_digest, parse_numstat, parse_status
from experimental.local_worker.loop_config import RefusedStart, Settings
from experimental.local_worker.tools import (
    ToolCall,
    ToolDeadline,
    WorkerIsolationError,
    WorkerTools,
)
from experimental.local_worker.untracked_allow import base_path, roots, under

if TYPE_CHECKING:
    from collections.abc import Callable

    from experimental.local_worker.episode import LoopOutcome

NOT_CLEAN = "checkout is not a clean git tree at its base"
Patch = tuple[int, int, int]


def _whole(call: ToolCall) -> bool:
    """Exited 0 with its whole output captured (a capped capture is never parsed as complete:
    Claude review of #239, NB5)."""
    return call.exit_code == 0 and call.capture_complete


def compress(
    records: tuple[str, ...], allowed: tuple[str, ...], status: TreeState
) -> tuple[str, ...]:
    """One ``r <sha256> <root>`` record per allowed root, hashing the digest records under it.
    A record under no root is left out only when ``status`` lists its path (or an ignored
    directory above it); else it is kept as it is (Codex review of PR #263: status and digest
    are two queries, and a path a surviving process created between them must still change the
    tree, never be dropped)."""
    out = []
    for root in allowed:
        mine = "\0".join(r for r in records if under(base_path(r), root))
        sha = hashlib.sha256(mine.encode("utf-8", errors="surrogateescape")).hexdigest()
        out.append(f"r {sha} {root}")
    seen = set(status.paths())
    dirs = tuple(p for p in seen if p.endswith("/"))
    for r in records:
        p = base_path(r)
        if not any(under(p, root) for root in allowed) and p not in seen and not p.startswith(dirs):
            out.append(r)
    return tuple(out)


class BaseCheckMixin:
    tools: WorkerTools
    settings: Settings
    _step_deadline: Callable[[], None]
    _base_patch: Patch | None = None  # the base's numstat, only with an allow-list
    _roots: tuple[str, ...] | None = None  # the allowed roots, known once the base is checked
    _base: TreeState | None = None  # the checked base, only with an allow-list
    abort_detail: str | None
    _own: Callable[[], float]

    def observe(self) -> TreeState:
        """The worker's git status now (with an allow-list, the digest too, in the same step);
        not observed unless every query exited 0 with its whole output captured."""
        self._step_deadline()
        call = self.tools.status()
        if not _whole(call):
            return TreeState(observed=False)
        tree = parse_status(call.raw)
        if not tree.observed or not self.settings.untracked_allow.active:
            return tree
        call = self.tools.digest()  # its timeout: what is left of the status step
        records = parse_digest(call.raw) if _whole(call) else None
        if records is None:
            return TreeState(observed=False)
        hashed = () if self._roots is None else compress(records, self._roots, tree)
        entries = tuple(sorted(tree.entries + hashed))
        return TreeState(observed=True, head=tree.head, entries=entries, digest=records)

    def _check_base(self, base: TreeState, expected_base: str | None) -> TreeState:
        """The base the run starts from (RefusedStart unless the clean pinned base)."""
        allow = self.settings.untracked_allow
        if not base.observed or base.head is None or (base.entries and not allow.active):
            raise RefusedStart(NOT_CLEAN)
        reason = allow.refusal(base) if allow.active else None
        if reason is not None:
            raise RefusedStart(f"{NOT_CLEAN}: {reason}")
        if expected_base is not None and base.head != expected_base:
            raise RefusedStart(f"checkout HEAD {base.head} is not the pinned base {expected_base}")
        if not allow.active:
            return base
        self._roots = roots(base)
        entries = tuple(sorted(base.entries + compress(base.digest, self._roots, base)))
        base = replace(base, entries=entries)
        if allow.expected is not None and base.entries != allow.expected:
            raise RefusedStart(f"{NOT_CLEAN}: the base entries differ from the parent's base")
        self._step_deadline()  # what the allowed files alone count, subtracted at the end
        call = self.tools.numstat()
        self._base_patch = parse_numstat(call.raw) if _whole(call) else None
        if self._base_patch is None:
            raise RefusedStart("the base numstat was not observed")
        self._base = base
        return base

    def _cleanup_allowed(self, out: LoopOutcome, sweep_ok: bool, raw: Patch | None) -> None:
        """The cleanup's last step with an allow-list (review of PR #263): observe the tree
        fresh right after numstat (status and digest, even when the last turn was observed),
        give it to the last turn that ran a tool if that turn was not observed or a surviving
        process changed the tree since, and count numstat only from this observation."""
        try:
            fresh = self.observe()
        except (WorkerIsolationError, ToolDeadline):
            fresh = TreeState(observed=False)
        last = next((t for t in reversed(out.turns) if t.tool_ran), None)
        seen = None if last is None else last.tree
        if last is not None and (seen is None or not seen.observed):
            last.tree, last.t_end = fresh, self._own()
        elif last is not None and seen is not None and fresh.observed:
            if fresh.entries != seen.entries:  # changed after the turn's own observation
                last.tree = fresh
        out.patch = self._own_patch(fresh, raw)
        out.cleanup = "ok" if sweep_ok and out.patch is not None else "failed"
        if raw is not None and out.patch is None:
            self.abort_detail = self.abort_detail or "cleanup: an allowed base file changed"

    def _own_patch(self, fresh: TreeState, raw: Patch | None) -> Patch | None:
        """``raw`` less the base's count (the allowed files' lines), if ``fresh`` (observed just
        after numstat) still has every base digest record (each allowed base file unchanged; new
        files may be added) and no count is negative; else None: an allowed file's own lines and
        the worker's edit of it cannot be told apart."""
        base = self._base
        if raw is None or self._base_patch is None or base is None or not fresh.observed:
            return None
        if not set(base.digest) <= set(fresh.digest):
            return None
        own = tuple(a - b for a, b in zip(raw, self._base_patch, strict=True))
        if min(own) < 0:
            return None
        return own[0], own[1], own[2]

    def _count_allowed(self, out: LoopOutcome) -> None:
        """allowed_base_changed_turns: the turns whose observed tree lacks a base digest record
        (an allowed base file changed or went); only with an allow-list."""
        if self._base is None:
            return
        want = set(self._base.digest)
        out.allowed_base_changed_turns = sum(
            t.tree is not None and t.tree.observed and not want <= set(t.tree.digest)
            for t in out.turns
        )
