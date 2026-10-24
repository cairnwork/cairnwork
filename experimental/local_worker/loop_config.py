"""The frozen settings of a local worker run (#88): its limits, its task and the model settings.
agent.py re-exports them (``from experimental.local_worker.agent import Limits, Task``)."""

from __future__ import annotations

from dataclasses import dataclass

from experimental.local_worker.tools import KILL_GRACE_SECONDS
from experimental.local_worker.write_scope import normalize


class RefusedStart(RuntimeError):
    """No episode is run: the checkout is not its clean base, or a resume has no time left."""


@dataclass(frozen=True)
class Limits:
    context_cap: int
    max_steps: int = 40
    max_wall_seconds: float = 1800.0
    cut_seconds: float = 900.0
    tool_cap_seconds: float = 300.0
    cleanup_reserve_seconds: float = 60.0
    max_completion_tokens: int = 4096
    # The robustness guards (guards.py); every one is frozen in the manifest with the limits.
    # Defaults: Nori's ADDENDUM_05 (hint off, stop at 5 with an unchanged tree, window 65536).
    context_window: int = 65536  # the server's window: prompt + max_completion_tokens must fit
    repeat_hint_at: int | None = None  # identical calls from this count are not run (None: off)
    repeat_stop_at: int = 5  # at this count of identical calls the episode ends (REPEAT_LIMIT)
    repeat_requires_unchanged_tree: bool = True  # count a repeat only if the tree did not change
    invalid_call_limit: int = 6  # consecutive invalid tool calls that end it (INVALID_..._LIMIT)

    def __post_init__(self) -> None:
        if self.step_seconds < 1.0:
            raise ValueError(f"cleanup_reserve_seconds must be >= {3 * (KILL_GRACE_SECONDS + 1)}")
        if self.repeat_stop_at < 2 or self.invalid_call_limit < 1:
            raise ValueError("repeat_stop_at must be >= 2 and invalid_call_limit >= 1")
        hint = self.repeat_hint_at
        if hint is not None and not 2 <= hint < self.repeat_stop_at:
            raise ValueError("repeat_hint_at must be off or in [2, repeat_stop_at)")
        if self.max_completion_tokens >= self.context_window:
            raise ValueError("max_completion_tokens must be below context_window")

    @property
    def step_seconds(self) -> float:
        """Timeout of each bookkeeping step (status, sweep, numstat): a third of the reserve,
        less the kill grace a timed-out step may still use."""
        return self.cleanup_reserve_seconds / 3 - KILL_GRACE_SECONDS

    def resume_budget(self, parent_seconds: float) -> float:
        """A resume's wall seconds: max_wall_seconds less the parent's pre-cut seconds
        (parent_pre_cut_wall_seconds), one budget for the whole episode as max_steps is. Refused
        (RefusedStart) unless it is more than the cleanup reserve: with no more, no turn could
        start, and the base observation and the cleanup would not fit either."""
        left = self.max_wall_seconds - parent_seconds
        if left <= self.cleanup_reserve_seconds:
            raise RefusedStart(
                f"no wall time left to resume: max_wall_seconds {self.max_wall_seconds} less the"
                f" parent's {parent_seconds} s before the cut leaves {left:.3f} s, not more than"
                f" the {self.cleanup_reserve_seconds} s cleanup reserve"
            )
        return left


@dataclass(frozen=True)
class Task:
    task_id: str
    repo: str
    prompt: str
    public_tests: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    model: str
    limits: Limits
    seed: int | None = None
    temperature: float = 0.0
    # write_file/edit_file only under these repo-relative entries (write_scope.py); None: anywhere
    write_scope: tuple[str, ...] | None = None
    search_tool: bool = True  # offer the read-only search tool (agent_protocol.SEARCH_SCHEMA)

    def __post_init__(self) -> None:
        object.__setattr__(self, "write_scope", normalize(self.write_scope))
