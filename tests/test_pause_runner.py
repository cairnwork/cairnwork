"""Offline coverage for PAUSE's two-run worker adapter."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pause_fakes import FakeGate, FakePlanner

from experimental.pause.flow import CoreVerdict, run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.runner import WorkerRunner, _copy_access, _parse_worker_output


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repo(path: Path) -> tuple[Path, str]:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "app.py").write_text("base\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return path, git(path, "rev-parse", "HEAD")


def invoke(tmp_path: Path, args: list[str], *, episode: str = "ep_test") -> tuple[int, str]:
    parsed, _ = (
        __import__("experimental.local_worker.cli", fromlist=["parser"])
        .parser()
        .parse_known_args(args)
    )
    checkout = parsed.checkout
    out = parsed.out
    out.mkdir(parents=True, exist_ok=True)
    (checkout / "app.py").write_text("edited\n")
    record: dict[str, Any] = {
        "episode_id": episode,
        "base_sha": git(checkout, "rev-parse", "HEAD"),
        "verification": {"exit_reason": "COMPLETED"},
    }
    corpus = out / "episodes.jsonl"
    line = json.dumps(record, separators=(",", ":"))
    corpus.write_text(line + "\n")
    sha = hashlib.sha256((line + "\n").encode()).hexdigest()
    private = out / "private" / episode
    private.mkdir(parents=True)
    transcript = private / "transcript.json"
    transcript.write_text(json.dumps({"record": record}))
    return 0, f"LOCAL_WORKER_EPISODE COMPLETED {episode} {corpus} line_sha256={sha}\n"


def arguments(tmp_path: Path, checkout: Path) -> list[str]:
    task = tmp_path / "task.json"
    task.write_text("{}")
    return [
        "--task",
        str(task),
        "--checkout",
        str(checkout),
        "--out",
        str(tmp_path / "out"),
        "--model",
        "m",
        "--context-cap",
        "100",
        "--canary-report",
        "c",
    ]


def test_runner_parses_worker_episode(tmp_path: Path) -> None:
    source, base = repo(tmp_path / "source")
    r0 = source.parent / "r0" / "checkout"
    r0.parent.mkdir()
    subprocess.run(["git", "clone", "--quiet", str(source), str(r0)], check=True)
    runargs = arguments(tmp_path, r0)
    runner = WorkerRunner(
        worker_main=lambda a: invoke(tmp_path, a)[0],
        runs_dir=tmp_path / "runs",
        prepare=lambda *a: None,
    )

    # Use a real successful worker output with the configured checkout in run mode.
    runner.worker_main = lambda a: print(invoke(tmp_path, a)[1], end="") or 0
    first = runner("run", runargs)
    assert first.record and first.record["episode_id"] == "ep_test"
    assert first.transcript and first.transcript.exists()
    assert first.record["base_sha"] == base


def test_runner_refuses_malformed_episode_line(tmp_path: Path) -> None:
    _, _ = repo(tmp_path / "repo")
    checkout = tmp_path / "run" / "checkout"
    checkout.parent.mkdir()
    git(checkout.parent, "init", "-q")
    runner = WorkerRunner(
        worker_main=lambda _a: print("LOCAL_WORKER_EPISODE bad") or 0, runs_dir=tmp_path / "runs"
    )
    result = runner("run", arguments(tmp_path, checkout))
    assert result.record is None and result.transcript is None


@pytest.mark.parametrize(
    "output",
    [
        "",
        "LOCAL_WORKER_EPISODE malformed\n",
        "LOCAL_WORKER_EPISODE COMPLETED ep_a path line_sha256=" + "0" * 64 + "\n",
        "LOCAL_WORKER_EPISODE COMPLETED ep_a x line_sha256=" + "0" * 64 + "\n"
        "LOCAL_WORKER_EPISODE COMPLETED ep_a x line_sha256=" + "0" * 64 + "\n",
    ],
)
def test_worker_output_requires_one_strict_episode_line(tmp_path: Path, output: str) -> None:
    result = _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", tmp_path)
    assert result.record is None and result.transcript is None


def test_worker_output_refuses_bad_corpus_digest_and_episode_binding(tmp_path: Path) -> None:
    out = tmp_path / "out"
    eid = "ep_bound"
    record = {"episode_id": "ep_other"}
    line = json.dumps(record, separators=(",", ":")) + "\n"
    corpus = out / "episodes.jsonl"
    corpus.parent.mkdir(parents=True)
    corpus.write_text(line)
    private = out / "private" / eid
    private.mkdir(parents=True)
    (private / "transcript.json").write_text("{}")
    bad_digest = "0" * 64
    output = f"LOCAL_WORKER_EPISODE COMPLETED {eid} {corpus} line_sha256={bad_digest}\n"
    assert _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", out).record is None
    actual = hashlib.sha256(line.encode()).hexdigest()
    output = f"LOCAL_WORKER_EPISODE COMPLETED {eid} {corpus} line_sha256={actual}\n"
    assert _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", out).record is None


def test_worker_output_rejects_corpus_outside_output_root(tmp_path: Path) -> None:
    out = tmp_path / "out"
    outside = tmp_path / "outside.jsonl"
    record = {"episode_id": "ep_outside", "verification": {"exit_reason": "COMPLETED"}}
    line = json.dumps(record, separators=(",", ":")) + "\n"
    outside.write_text(line)
    transcript = out / "private" / "ep_outside" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}")
    sha = hashlib.sha256(line.encode()).hexdigest()
    output = f"LOCAL_WORKER_EPISODE COMPLETED ep_outside {outside} line_sha256={sha}\n"
    result = _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", out)
    assert result.record is None and result.transcript is None


def test_worker_output_requires_record_exit_reason_to_match_line(tmp_path: Path) -> None:
    out = tmp_path / "out"
    record = {"episode_id": "ep_reason", "verification": {"exit_reason": "FAILED"}}
    line = json.dumps(record, separators=(",", ":")) + "\n"
    corpus = out / "episodes.jsonl"
    corpus.parent.mkdir(parents=True)
    corpus.write_text(line)
    transcript = out / "private" / "ep_reason" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}")
    sha = hashlib.sha256(line.encode()).hexdigest()
    output = f"LOCAL_WORKER_EPISODE COMPLETED ep_reason {corpus} line_sha256={sha}\n"
    result = _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", out)
    assert result.record is None and result.transcript is None


def test_copy_access_skips_symlink_in_fresh_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target_file = source / "target"
    target_file.write_text("target\n")
    target_file.chmod(0o600)
    (source / "link").symlink_to("target")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (source / ".git").mkdir()
    (fresh / ".git").mkdir()
    (fresh / "target").write_text("target\n")
    (fresh / "target").chmod(0o600)
    (fresh / "link").symlink_to("target")
    (source / "link").unlink()
    (source / "link").write_text("replacement\n")
    (source / "link").chmod(0o777)
    _copy_access(source, fresh)
    assert (fresh / "target").stat().st_mode & 0o777 == 0o600
    assert fresh.stat().st_gid == source.stat().st_gid
    assert (fresh / ".git").stat().st_gid == source.stat().st_gid
    assert (fresh / "link").lstat().st_gid == source.stat().st_gid


def test_copy_access_gives_deleted_tracked_file_derived_mode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.chmod(0o754)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "deleted.py").write_text("base\n")
    (fresh / "deleted.py").chmod(0o644)
    _copy_access(source, fresh)
    assert (fresh / "deleted.py").stat().st_mode & 0o777 == 0o644


def test_resume_run_root_mode_ignores_umask(tmp_path: Path) -> None:
    source, base = repo(tmp_path / "source")
    checkout = tmp_path / "r0" / "checkout"
    checkout.parent.mkdir()
    subprocess.run(["git", "clone", "--quiet", str(source), str(checkout)], check=True)
    checkout.chmod(0o751)
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"record": {"base_sha": base}}))
    runs = tmp_path / "runs"
    old_umask = os.umask(0o077)
    try:
        runner = WorkerRunner(lambda _argv: 2, runs, prepare=lambda *_args: None)
        result = runner("resume", arguments(tmp_path, checkout), parent=parent, cut=0, plan="p")
    finally:
        os.umask(old_umask)
    assert result.root is not None
    assert result.root.stat().st_mode & 0o777 == checkout.stat().st_mode & 0o777
    assert result.root.stat().st_gid == checkout.stat().st_gid


@pytest.mark.parametrize("mode", ["run", "resume"])
def test_clean_run_root_refuses_before_worker_in_both_modes(tmp_path: Path, mode: str) -> None:
    called: list[list[str]] = []
    runner = WorkerRunner(lambda argv: called.append(argv) or 0)
    args = arguments(tmp_path, tmp_path / "checkout") + ["--clean-run-root"]
    with pytest.raises(ValueError, match="clean-run-root"):
        runner(
            mode,
            args,
            parent=tmp_path / "missing-parent.json",
            cut=0,
            plan="plan",
        )
    assert called == []


def test_worker_refusal_exit_keeps_record_empty(tmp_path: Path) -> None:
    result = _parse_worker_output(
        "LOCAL_WORKER_EPISODE_REFUSED refusal\n", 2, tmp_path, tmp_path / "checkout", tmp_path
    )
    assert result.code == 2 and result.record is None and result.transcript is None


def test_resume_rejects_invalid_parent_base_before_worker(tmp_path: Path) -> None:
    source, _ = repo(tmp_path / "source")
    checkout = tmp_path / "r0" / "checkout"
    checkout.parent.mkdir()
    subprocess.run(["git", "clone", "--quiet", str(source), str(checkout)], check=True)
    parent = tmp_path / "parent.json"
    parent.write_text('{"record":{"base_sha":"not-a-sha"}}')
    called: list[list[str]] = []
    runner = WorkerRunner(lambda argv: called.append(argv) or 0, tmp_path / "runs")
    with pytest.raises(RuntimeError, match="base_sha"):
        runner("resume", arguments(tmp_path, checkout), parent=parent, cut=1, plan="p")
    assert called == []


def test_checkout_change_between_plan_digests_stops_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source, base = repo(tmp_path / "source")
    checkout = tmp_path / "r0" / "checkout"
    checkout.parent.mkdir()
    subprocess.run(["git", "clone", "--quiet", str(source), str(checkout)], check=True)
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"record": {"base_sha": base}}))
    calls: list[list[str]] = []

    class ChangingGuard:
        count = 0

        def __init__(self, _path: Path, **_options) -> None:
            pass

        def digest(self) -> str:
            type(self).count += 1
            return "a" * 64 if type(self).count == 1 else "b" * 64

    monkeypatch.setattr("experimental.pause.runner.TreeGuard", ChangingGuard)
    runner = WorkerRunner(lambda argv: calls.append(argv) or 0, tmp_path / "runs")
    result = runner("resume", arguments(tmp_path, checkout), parent=parent, cut=1, plan="p")
    assert result.code == 2 and result.guard_before_plan != result.guard_at_plan_arrival
    assert calls == []


@pytest.mark.parametrize("verdict, expected", [("VERIFIED", "R1"), ("FAILED", "R0")])
def test_real_runner_flow_selects_only_verified_r1(
    tmp_path: Path, verdict: str, expected: str
) -> None:
    source, base = repo(tmp_path / "source")
    r0 = tmp_path / "r0" / "checkout"
    r0.parent.mkdir()
    subprocess.run(["git", "clone", "--quiet", str(source), str(r0)], check=True)
    args = arguments(tmp_path, r0) + ["--write-path", "src"]
    sequence = iter(("ep_r0", "ep_r1"))

    def worker(argv: list[str]) -> int:
        parsed, _ = (
            __import__("experimental.local_worker.cli", fromlist=["parser"])
            .parser()
            .parse_known_args(argv)
        )
        eid = next(sequence)
        checkout, out = parsed.checkout, parsed.out
        out.mkdir(parents=True, exist_ok=True)
        if argv[0] == "resume":
            assert git(checkout, "rev-parse", "HEAD") == base
            assert Path(parsed.plan).is_relative_to(out)
            assert Path(parsed.plan).stat().st_mode & 0o777 == 0o600
        (checkout / "src").mkdir(exist_ok=True)
        (checkout / "src" / "app.py").write_text(f"{eid}\n")
        record: dict[str, Any] = {
            "episode_id": eid,
            "base_sha": base,
            "write_scope": ["src"],
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
            "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0},
        }
        corpus = out / "episodes.jsonl"
        line = json.dumps(record, separators=(",", ":"))
        with corpus.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        sha = hashlib.sha256((line + "\n").encode()).hexdigest()
        private = out / "private" / eid
        private.mkdir(parents=True)
        transcript = private / "transcript.json"
        value: dict[str, Any] = {"record": record}
        if argv[0] == "run":
            value.update(
                messages=[{"role": "user"}, {"role": "assistant"}],
                turns=[{"index": 0, "assistant_msg_index": 1}],
            )
        transcript.write_text(json.dumps(value))
        print(f"LOCAL_WORKER_EPISODE COMPLETED {eid} {corpus} line_sha256={sha}")
        return 0

    runner = WorkerRunner(worker, tmp_path / "runs")
    settings = CallSettings("model", "low", 1, 1)
    first, receipt = run_flow(
        args,
        task_id="task",
        planner=FakePlanner([PlanReply("plan text", "planner", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "gate", "low")]),
        planner_settings=settings,
        gate_settings=settings,
        runner=runner,
        core_verdict=lambda r1: CoreVerdict(verdict, TreeGuard(r1.checkout).digest()),
    )
    assert receipt["reason"] in (None, "PAUSE_R1_NOT_VERIFIED"), receipt
    assert first.episode_id == ("ep_r1" if expected == "R1" else "ep_r0")
    assert receipt["core_verdict"] == verdict
    if expected == "R1":
        assert first.base_sha == base
        assert first.root != r0.parent
        assert first.checkout != r0
        assert len(first.guard_before_plan or "") == 64
        assert len(first.guard_at_plan_arrival or "") == 64
        assert first.guard_before_plan == first.guard_at_plan_arrival
        assert first.record and first.record["verification"]["exit_reason"] == "COMPLETED"
