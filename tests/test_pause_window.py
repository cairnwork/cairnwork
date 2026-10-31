"""PAUSE (#476) AC8 (a): the read-only window opens only if no tool call has started, in one step
with the check every call makes, and closes only once the plan's arrival is recorded."""

import sys
import threading
from typing import Any

import pytest
from test_pause_guard import _fill, _log

from experimental.local_worker.tools import START_MARKER, PrePlanReadOnly, WorkerTools
from experimental.pause.controller import PrePlanGuard
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import GuardUnavailable


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")  # the fake invoker stands in for Seatbelt
    co = tmp_path / "repo"
    co.mkdir()
    _fill(co)
    return co


def _invoker(started, hold=None):
    """Records each action; ``hold`` (entered, release) blocks a writable pytest inside it."""

    def invoke(argv, stdin, timeout):
        i = argv.index("--start-nonce")
        started.append(argv[i + 2])
        if hold is not None and argv[i + 2] == "pytest":
            hold[0].set()
            assert hold[1].wait(timeout=30)
        return 0, b"", f"{START_MARKER} {argv[i + 1]}\n".encode()

    return invoke


def test_the_guard_cannot_start_while_a_call_is_running(checkout, tmp_path):
    started, hold = [], (threading.Event(), threading.Event())
    tools = WorkerTools(tmp_path / "run", tmp_path / "calls.jsonl", invoker=_invoker(started, hold))
    a = threading.Thread(target=lambda: tools.run_tests(["-q"]))
    a.start()
    assert hold[0].wait(timeout=30)  # the writable call is inside the invoker
    pre = PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools)
    with pytest.raises(GuardUnavailable, match="first tool call"):
        pre.start()
    hold[1].set()
    a.join()
    assert "pytest-ro" not in started and tools.read_only_until_plan is False


class Racing(WorkerTools):
    """The main thread's read of calls_started lets thread A try a writable call right then:
    without the window lock A gets it in before the window opens; with it, A waits and is refused.
    The bounded wait only decides how long the locked case takes, never its outcome."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.a: threading.Thread | None = None
        self.tried = threading.Event()
        super().__init__(*args, **kwargs)

    @property
    def calls_started(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        n = self.__dict__.get("_n", 0)
        if self.a is not None and threading.current_thread() is threading.main_thread():
            a, self.a = self.a, None
            a.start()
            self.tried.wait(timeout=2)
        return n

    @calls_started.setter
    def calls_started(self, value: int) -> None:
        self.__dict__["_n"] = value


def test_opening_the_window_and_starting_a_call_cannot_interleave(checkout, tmp_path):
    started, errors = [], []
    tools = Racing(tmp_path / "run", tmp_path / "calls.jsonl", invoker=_invoker(started))

    def writable():
        try:
            tools.run_tests(["-q"])
        except PrePlanReadOnly as e:
            errors.append(e)
        finally:
            tools.tried.set()

    tools.a = threading.Thread(target=writable)
    PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools).start()
    tools.tried.wait(timeout=30)
    assert "pytest" not in started and len(errors) == 1  # refused, never run in the window


class FailingLog:
    def record(self, kind: str, **fields: Any) -> int:
        if kind == "plan_arrival":
            raise GuardUnavailable("event log: no space left on device")
        return 1


def test_a_plan_arrival_it_cannot_record_keeps_the_window_read_only(checkout, tmp_path):
    started = []
    tools = WorkerTools(tmp_path / "run", tmp_path / "calls.jsonl", invoker=_invoker(started))
    log: Any = FailingLog()
    pre = PrePlanGuard(TreeGuard(checkout), log, tools)
    pre.start()
    with pytest.raises(GuardUnavailable, match="event log"):
        pre.at_plan_arrival()
    with pytest.raises(PrePlanReadOnly):
        tools.run_tests(["-q"])
    assert tools.read_only_until_plan and started == ["pytest-ro", "sweep"]
