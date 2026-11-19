"""The gate reviews the change itself with repo-relative paths, never a RunResult repr."""

import subprocess

import pytest

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.gate_plan import MAX_PLAN_CHARS, build_gate_plan
from experimental.local_relay.relay import Acceptance, Attempt, run_relay
from experimental.pause.flow import RunResult
from experimental.pause.interfaces import GateReply
from experimental.pause.schema import Reason

ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "HOME": "/nonexistent"}


def _repo(tmp_path):
    repo = tmp_path / "work" / "checkout"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[n:]\n")
    import os

    env = {**os.environ, **ENV}
    for argv in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env)
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, env=env
    ).stdout.strip()
    return repo, base


TASK = {"prompt": "Fix last_n", "allowed_files": ["pkg/stats.py"]}


def test_plan_has_the_task_the_relative_diff_and_new_files_but_no_host_paths(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[-n:]\n")
    (repo / "pkg" / "extra.py").write_text("VALUE = 1\n")
    result = RunResult(0, "", repo.parent, repo, base_sha=base)
    plan = build_gate_plan(TASK, result)
    assert "Fix last_n" in plan and "pkg/stats.py" in plan
    assert "-    return items[n:]" in plan and "+    return items[-n:]" in plan
    assert "New file pkg/extra.py" in plan and "VALUE = 1" in plan
    assert str(tmp_path) not in plan and "/Users/" not in plan and "RunResult" not in plan
    assert plan.rstrip().endswith("then a short reason.")


def test_plan_needs_a_checkout_and_a_base_commit(tmp_path):
    with pytest.raises(ValueError):
        build_gate_plan(
            TASK,
            RunResult(0, "", tmp_path, None, base_sha="a" * 40),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        build_gate_plan(TASK, RunResult(0, "", tmp_path, tmp_path, base_sha=None))


def test_plan_is_bounded(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "pkg" / "stats.py").write_text("x = 1\n" * 20000)
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert len(plan) < MAX_PLAN_CHARS + 200 and "[truncated]" in plan


def test_relay_hands_the_gate_the_built_plan_not_the_candidate_repr(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    seen = []

    class Gate:
        def review(self, request):
            seen.append(request.plan)
            return GateReply("ACCEPT", "m", "e", None)

    class Rows:
        def append(self, row):
            pass

    candidate = object()
    config = RelayConfig(enabled=True, plan_for=lambda cand: "PLAN FOR " + str(cand is candidate))
    result = run_relay(
        "t",
        lambda _ctx: Attempt(candidate, "local", 1, 1, 0.0),
        lambda _cand: Acceptance(True, "ok"),
        Gate(),
        lambda _t: Attempt(candidate, "cloud", 1, 1, 0.1),
        Rows(),
        config,
    )
    assert result.route == "LOCAL_FIRST" and seen == ["PLAN FOR True"]


def test_a_failing_plan_builder_fails_closed(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")

    class Gate:
        def review(self, request):
            raise AssertionError("must not be called")

    class Rows:
        def append(self, row):
            pass

    def boom(_cand):
        raise RuntimeError("cannot read the candidate change for the gate")

    result = run_relay(
        "t",
        lambda _ctx: Attempt(object(), "local", 1, 1, 0.0),
        lambda _cand: Acceptance(True, "ok"),
        Gate(),
        lambda _t: Attempt(object(), "cloud", 1, 1, 0.1),
        Rows(),
        RelayConfig(enabled=True, plan_for=boom),
    )
    assert result.route == "FAILED" and "cannot read" in (result.reason or "")
    assert Reason.PAUSE_GATE_REFUSED is not None
