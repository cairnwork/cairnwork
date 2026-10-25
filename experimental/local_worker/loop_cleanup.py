"""The cleanup of a local worker run (#88), split from agent.py: sweep, numstat and the last
tree, as far as they get, after the loop stopped. AgentLoop inherits it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experimental.local_worker.episode import LoopOutcome
from experimental.local_worker.gitstate import TreeState, parse_numstat
from experimental.local_worker.tools import ToolDeadline, WorkerIsolationError, WorkerTools

if TYPE_CHECKING:
    from collections.abc import Callable


class CleanupMixin:
    tools: WorkerTools
    abort_detail: str | None
    deferred: BaseException | None
    _own: Callable[[], float]
    _step_deadline: Callable[[], None]
    observe: Callable[[], TreeState]
    _own_patch: Callable[[LoopOutcome, tuple[int, int, int] | None], tuple[int, int, int] | None]

    def _cleanup(self, out: LoopOutcome) -> None:
        """Sweep, numstat and the last tree, as far as they get: whatever stops them (even
        SystemExit) leaves cleanup "failed" and the line still gets written."""
        try:
            self._cleanup_steps(out)
        except BaseException as exc:  # noqa: BLE001 - the caller re-raises it after the line
            out.cleanup = "failed"
            self.abort_detail = self.abort_detail or f"cleanup: {type(exc).__name__}: {exc}"
            if not isinstance(exc, Exception):
                self.deferred = self.deferred or exc

    def _cleanup_steps(self, out: LoopOutcome) -> None:
        try:
            self._step_deadline()
            sweep = self.tools.sweep()
            self._step_deadline()
            stat = self.tools.numstat()
        except (WorkerIsolationError, ToolDeadline) as exc:
            out.cleanup = "failed"
            self.abort_detail = self.abort_detail or f"cleanup: {exc}"
            return
        whole = stat.exit_code == 0 and stat.capture_complete  # a capped count is no count
        raw = parse_numstat(stat.raw) if whole else None
        out.patch = self._own_patch(out, raw)  # raw unless a start allow-list is set (#261)
        out.cleanup = "ok" if sweep.exit_code == 0 and out.patch is not None else "failed"
        self._observe_last(out)
        if raw is not None and out.patch is None:  # an allow-list: now with the last tree
            out.patch = self._own_patch(out, raw)
            out.cleanup = "ok" if sweep.exit_code == 0 and out.patch is not None else "failed"
            if out.patch is None:
                self.abort_detail = self.abort_detail or "cleanup: an allowed base file changed"

    def _observe_last(self, out: LoopOutcome) -> None:
        """The run stopped after a tool started, before its tree was observed: observe it now, after
        the sweep (nothing else ran since); else it stays unobserved. The turn ends when this try
        ends: its processes ran until the sweep, so a limit before that is null (Codex, PR #248)."""
        last = out.turns[-1] if out.turns else None
        if last is None or not last.tool_ran or (last.tree is not None and last.tree.observed):
            return
        try:
            last.tree = self.observe()
        except (WorkerIsolationError, ToolDeadline):
            pass
        finally:
            last.t_end = self._own()
