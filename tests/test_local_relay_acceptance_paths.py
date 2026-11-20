"""Candidate-path checks and failure-cost records never trust the worker's repository."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from experimental.local_relay.adapters import CoreAcceptance, HaikuProxyFallback
from experimental.local_relay.config import RelayConfig
from experimental.local_relay.gate_plan import PlanIncomplete, build_gate_plan, changed_paths
from experimental.local_relay.relay import Acceptance, LocalAttemptFailed, run_relay
from experimental.local_worker.endpoint import EndpointUnreachable
from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult
from experimental.pause.interfaces import CallSettings, GateReply, GateRequest

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": "/nonexistent"}
TASK = {"prompt": "fix", "allowed_files": ["inside.py"]}


def _git(repo, *argv):
    return subprocess.run(
        ["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "work" / "checkout"
    repo.mkdir(parents=True)
    (repo / "inside.py").write_text("x = 1\n")
    (repo / "other.py").write_text("y = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(repo), str(source)], check=True, env=ENV
    )
    return repo, source, base


def _acceptance(source):
    return CoreAcceptance(
        lambda _c: pytest.fail("a refused candidate reached CoreJudge"), ("inside.py",), source
    )


def test_untracked_outside_file_is_refused_before_core(tmp_path):
    repo, source, base = _repo(tmp_path)
    (repo / "outside.py").write_text("z = 1\n")
    verdict = _acceptance(source)(RunResult(0, "", repo.parent, repo, base_sha=base))
    assert verdict.passed is False and "outside.py" in verdict.raw


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_bits_cannot_hide_a_modified_outside_file(tmp_path, flag):
    repo, source, base = _repo(tmp_path)
    _git(repo, "update-index", flag, "other.py")
    (repo / "other.py").write_text("y = 999\n")
    verdict = _acceptance(source)(RunResult(0, "", repo.parent, repo, base_sha=base))
    assert verdict.passed is False and "other.py" in verdict.raw


def test_worker_fsmonitor_config_is_never_executed_while_listing_changes(tmp_path):
    repo, source, base = _repo(tmp_path)
    marker = tmp_path / "executed"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    with (repo / ".git" / "config").open("a") as handle:
        handle.write(f"[core]\n\tfsmonitor = {hook}\n")
    (repo / "inside.py").write_text("x = 2\n")
    assert changed_paths(source, repo, base) == {"inside.py"}
    assert not marker.exists()


def test_allowlist_check_requires_the_trusted_source_repository():
    with pytest.raises(ValueError):
        CoreAcceptance(lambda _c: None, ("inside.py",))


def test_base_commit_must_be_a_full_lowercase_sha(tmp_path):
    repo, source, _base = _repo(tmp_path)
    for bad in ("HEAD", "abc123", "A" * 40):
        with pytest.raises(ValueError):
            changed_paths(source, repo, bad)


def test_a_tracked_file_swapped_for_a_hard_link_is_never_sent_to_the_gate(tmp_path):
    repo, source, base = _repo(tmp_path)
    outside = tmp_path / "host.txt"
    outside.write_text("HOST-ONLY-DATA\n")
    (repo / "inside.py").unlink()
    os.link(outside, repo / "inside.py")
    with pytest.raises(PlanIncomplete):
        build_gate_plan(
            {**TASK, "repo": str(source)}, RunResult(0, "", repo.parent, repo, base_sha=base)
        )


def test_a_deleted_or_symlinked_tracked_file_is_still_reviewable(tmp_path):
    repo, source, base = _repo(tmp_path)
    (repo / "other.py").unlink()
    (repo / "inside.py").unlink()
    (repo / "inside.py").symlink_to(tmp_path / "host.txt")
    plan = build_gate_plan(
        {**TASK, "repo": str(source)}, RunResult(0, "", repo.parent, repo, base_sha=base)
    )
    assert "-y = 1" in plan and "HOST-ONLY-DATA" not in plan


class Rows:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class AcceptGate:
    def review(self, request):
        return GateReply("ACCEPT", "haiku", "medium", None)


def _failing_fallback(stats):
    def request(method, url, body):
        if method == "GET":
            if stats is None:
                raise RuntimeError("proxy gone")
            return stats
        return {}

    def run_attempt(_ctx, _endpoint):
        raise EndpointUnreachable("haiku proxy upstream failed")

    return HaikuProxyFallback("http://127.0.0.1:18301/v1", run_attempt, request)


def _run(fallback, rows, monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")

    def local(_ctx):
        raise EndpointUnreachable("local offline")

    return run_relay(
        "t",
        local,
        lambda _c: Acceptance(True, "ok"),
        AcceptGate(),
        fallback,
        rows,
        RelayConfig(enabled=True),
    )


def test_a_failed_fallback_records_what_the_proxy_actually_spent(monkeypatch):
    rows = Rows()
    stats = {"cost_usd": 0.0031, "input_tokens": 500, "output_tokens": 40}
    result = _run(_failing_fallback(stats), rows, monkeypatch)
    row = next(r for r in rows.rows if r["stage"] == "fallback")
    assert result.route == "FAILED"
    assert (row["usd"], row["input_tokens"], row["output_tokens"]) == (0.0031, 500, 40)
    assert row["usd_known"] is True


def test_a_failed_fallback_with_unreadable_stats_is_unknown_never_zero(monkeypatch):
    rows = Rows()
    result = _run(_failing_fallback(None), rows, monkeypatch)
    row = next(r for r in rows.rows if r["stage"] == "fallback")
    assert result.route == "FAILED"
    assert row["usd"] is None and row["usd_known"] is False


def test_garbage_stats_after_a_failed_fallback_are_unknown(monkeypatch):
    rows = Rows()
    _run(_failing_fallback({"cost_usd": "free", "input_tokens": -1}), rows, monkeypatch)
    row = next(r for r in rows.rows if r["stage"] == "fallback")
    assert row["usd"] is None and row["usd_known"] is False


def test_the_cost_attributes_exist_on_the_exception():
    fallback = _failing_fallback({"cost_usd": 0.5, "input_tokens": 1, "output_tokens": 2})
    with pytest.raises(LocalAttemptFailed) as caught:
        fallback("task")
    assert caught.value.usd == 0.5  # attached for the relay to record


def test_a_stalled_git_is_killed_by_the_wall_clock(monkeypatch, tmp_path):
    import sys
    import time

    from experimental.local_relay import gate_plan

    monkeypatch.setattr(gate_plan, "GIT_TIMEOUT", 0.3)
    monkeypatch.setattr(gate_plan, "_GIT", (sys.executable, "-c", "import time; time.sleep(30)"))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gate_plan._git(tmp_path, "diff", limit=100, env=dict(os.environ))
    assert time.monotonic() - started < 10


def test_a_tracked_file_swapped_while_git_reads_it_is_caught_by_the_second_check(
    monkeypatch, tmp_path
):
    from experimental.local_relay import gate_plan

    repo, source, base = _repo(tmp_path)
    (repo / "inside.py").write_text("x = 2\n")
    real_git = gate_plan._git

    def swapping_git(checkout, *args, **kwargs):
        data = real_git(checkout, *args, **kwargs)
        if args and args[0] == "diff" and "--text" in args:  # a leftover worker process at work
            content = (repo / "inside.py").read_bytes()
            (repo / "inside.py").unlink()
            (repo / "inside.py").write_bytes(content)  # same bytes, new inode
        return data

    monkeypatch.setattr(gate_plan, "_git", swapping_git)
    with pytest.raises(PlanIncomplete, match="changed while"):
        build_gate_plan(
            {**TASK, "repo": str(source)}, RunResult(0, "", repo.parent, repo, base_sha=base)
        )


def test_an_untouched_tracked_change_passes_both_checks(tmp_path):
    repo, source, base = _repo(tmp_path)
    (repo / "inside.py").write_text("x = 2\n")
    plan = build_gate_plan(
        {**TASK, "repo": str(source)}, RunResult(0, "", repo.parent, repo, base_sha=base)
    )
    assert "+x = 2" in plan


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_costs_are_never_recorded_as_spend(monkeypatch, bad):
    from experimental.local_relay.proxy_gate import ProxyGate

    rows = Rows()
    stats = {"cost_usd": bad, "input_tokens": 5, "output_tokens": 2}
    _run(_failing_fallback(stats), rows, monkeypatch)
    row = next(r for r in rows.rows if r["stage"] == "fallback")
    assert row["usd"] is None and row["usd_known"] is False

    def request(method, url, body):
        if "/ab/stats" in url:
            return {
                "requests": 1,
                "cost_usd": bad,
                "input_tokens": 5,
                "output_tokens": 2,
                "budget_stop": False,
                "model_mismatch": 0,
            }
        if url.endswith("/chat/completions"):
            return {
                "choices": [{"message": {"content": '{"verdict": "可直接合併", "reason": "ok"}'}}]
            }
        return {}

    reply = ProxyGate("http://127.0.0.1:18301/v1", request).review(
        GateRequest("t", "", "p", CallSettings("haiku", "medium", 5.0, 1))
    )
    assert reply.verdict is None and reply.usd == 0.0


def test_core_acceptance_includes_rejection_diagnostics_in_stage_output():
    class Verdict:
        status = "FAILED"
        tree_sha256 = "a" * 64
        reason = "pytest failed"
        pytest_exit_code = 1
        pytest_output = "\n".join(f"line {number}" for number in range(1, 46))

    verdict = CoreAcceptance(lambda _candidate: Verdict())(RunResult(0, "", Path("."), Path(".")))

    output = json.loads(verdict.raw)
    assert verdict.passed is False
    assert output["reason"] == "pytest failed"
    assert output["pytest_exit_code"] == 1
    assert output["pytest_output_tail"].splitlines() == [
        *(f"line {number}" for number in range(6, 46))
    ]


def test_real_core_judge_verdict_carries_diagnostics_into_acceptance(tmp_path):
    from experimental.local_relay.relay import _stage

    repo, source, base = _repo(tmp_path)
    (repo / "inside.py").write_text("x = 2\n")
    result = RunResult(0, "", repo.parent, repo, base_sha=base)
    judge = CoreJudge(
        tmp_path / "state",
        (),
        verify=lambda *_a, **_kw: {
            "status": "FAILED",
            "reason": "pytest failed",
            "pytest_exit_code": 1,
            "pytest_output": "x" * 5000,
        },
    )
    acceptance = CoreAcceptance(judge, ("inside.py",), source)(result)
    stage = _stage("core", acceptance, GateReply(None, "test", "test"), "")
    evidence = json.loads(acceptance.raw)
    assert evidence["reason"] == "pytest failed"
    assert evidence["status"] == "FAILED"
    assert evidence["tree_sha256"]
    assert len(evidence["pytest_output_tail"]) <= 600
    assert '"status": "FAILED"' in stage["acceptance"]
    assert '"tree_sha256": "' + evidence["tree_sha256"] in stage["acceptance"]


@pytest.mark.parametrize("tail", ["\n".join("x" * 200 for _ in range(40)), "y" * 5000])
def test_core_stage_preserves_verdict_fields_for_long_tails(tmp_path, tail):
    class Verdict:
        status = "FAILED"
        tree_sha256 = "a" * 64
        reason = "pytest failed"
        pytest_exit_code = 1
        pytest_output = tail

    from experimental.local_relay.relay import _stage

    acceptance = CoreAcceptance(lambda _c: Verdict())(RunResult(0, "", Path("."), Path(".")))
    stage = _stage("core", acceptance, GateReply(None, "test", "test"), "")
    assert '"status": "FAILED"' in stage["acceptance"]
    assert '"tree_sha256": "' + "a" * 64 in stage["acceptance"]
    assert len(json.loads(acceptance.raw)["pytest_output_tail"]) <= 600


def test_core_acceptance_redacts_tail_and_rejects_invalid_exit_code():
    class Verdict:
        status = "FAILED"
        tree_sha256 = "a" * 64
        pytest_exit_code = True
        pytest_output = "GITHUB_TOKEN=ghp_abcdefghijklmno /Users/james/private/file.py"

    output = CoreAcceptance(lambda _c: Verdict())(RunResult(0, "", Path("."), Path(".")))
    evidence = json.loads(output.raw)
    assert "pytest_exit_code" not in evidence
    assert "ghp_abcdefghijklmno" not in output.raw
    assert "/Users/james/private/file.py" not in output.raw


def test_core_acceptance_without_diagnostic_fields_keeps_legacy_output():
    class Verdict:
        status = "VERIFIED"
        tree_sha256 = "a" * 64

    output = CoreAcceptance(lambda _c: Verdict())(RunResult(0, "", Path("."), Path(".")))
    assert output.raw == json.dumps({"status": "VERIFIED", "tree_sha256": "a" * 64}, sort_keys=True)
