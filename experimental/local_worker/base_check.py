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
from experimental.local_worker.tools import ToolCall, WorkerTools
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


def compress(records: tuple[str, ...], allowed: tuple[str, ...]) -> tuple[str, ...]:
    """One ``r <sha256> <root>`` record per allowed root, hashing the digest records under it.
    Records under no root are left out: such a path differs from HEAD, so the status lists it."""
    out = []
    for root in allowed:
        mine = "\0".join(r for r in records if under(base_path(r), root))
        sha = hashlib.sha256(mine.encode("utf-8", errors="surrogateescape")).hexdigest()
        out.append(f"r {sha} {root}")
    return tuple(out)


class BaseCheckMixin:
    tools: WorkerTools
    settings: Settings
    _step_deadline: Callable[[], None]
    _base_patch: Patch | None = None  # the base's numstat, only with an allow-list
    _roots: tuple[str, ...] | None = None  # the allowed roots, known once the base is checked

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
        hashed = () if self._roots is None else compress(records, self._roots)
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
        entries = tuple(sorted(base.entries + compress(base.digest, self._roots)))
        base = replace(base, entries=entries)
        if allow.expected is not None and base.entries != allow.expected:
            raise RefusedStart(f"{NOT_CLEAN}: the base entries differ from the parent's base")
        self._step_deadline()  # what the allowed files alone count, subtracted at the end
        call = self.tools.numstat()
        self._base_patch = parse_numstat(call.raw) if _whole(call) else None
        if self._base_patch is None:
            raise RefusedStart("the base numstat was not observed")
        return base

    def _own_patch(self, out: LoopOutcome, raw: Patch | None) -> Patch | None:
        """``raw`` without an allow-list; with one, ``raw`` less the base's count, if the last
        observed tree still has every base digest record (each allowed file unchanged; new
        files may be added), else None: an allowed file's own lines and the worker's edit of it
        cannot be told apart."""
        if not self.settings.untracked_allow.active:
            return raw
        if raw is None or self._base_patch is None:
            return None
        last = next((t for t in reversed(out.turns) if t.tool_ran), None)
        tree = out.base if last is None else last.tree
        if tree is None or not tree.observed or not set(out.base.digest) <= set(tree.digest):
            return None
        added, removed, binary = (a - b for a, b in zip(raw, self._base_patch, strict=True))
        return added, removed, binary
