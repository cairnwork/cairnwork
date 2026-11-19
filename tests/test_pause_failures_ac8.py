"""AC8 workaround placement injections: Option A never carries pre-plan changes into R1."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from pause_fakes import FakeGate, FakePlanner

from experimental.pause import receipt, schema
from experimental.pause.flow import RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply

SETTINGS = CallSettings("configured-model", "low", 1, 1)
TASKS = [
    "sed-i",
    "redirection",
    "git-apply-two",
    "python-mode",
    "patch-file",
    "formatter",
    "pytest-snapshot",
    "tmp-script-nested",
    "rename",
    "delete",
    "symlink",
    "ignored",
    "gitattributes",
    "empty-dir-file",
    "exec-bit",
    "crlf",
    "binary",
    "unicode-name",
    "dot-directory",
    "large-file",
]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _scenario(
    root: Path, task: str, place: str, event_seq: Any = None
) -> tuple[RunResult, dict, Path]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Pause test")
    _git(repo, "config", "user.email", "pause@example.invalid")
    (repo / "app.py").write_text("base\n")
    (repo / ".gitignore").write_text("ignored.tmp\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    if place == "r0":
        _write_change(repo, task)
    transcript = root / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "messages": [{"role": "user"}, {"role": "assistant"}],
                "turns": [{"index": 0, "assistant_msg_index": 1}],
                "write_scope": ["src"],
            }
        )
    )
    record = {"base_sha": base, "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0}}
    r0 = RunResult(0, "r0", root, repo, transcript, record, "ep_r0")
    args = ["--checkout", str(repo), "--out", str(root / "out")]

    def runner(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        if mode == "run":
            return r0
        r1_root = root / "runs" / task
        r1_root.mkdir(parents=True)
        checkout = r1_root / "checkout"
        subprocess.run(["git", "clone", "-q", str(repo), str(checkout)], check=True)
        _git(checkout, "checkout", "--detach", base)
        before = "a" * 64
        arrival = before
        if place == "between":
            _write_change(checkout, task)
            arrival = "b" * 64
        elif place == "r1":
            _write_change(checkout, task)
        return RunResult(
            2 if place == "between" else 0,
            "",
            r1_root,
            checkout,
            guard_before_plan=before,
            guard_at_plan_arrival=arrival,
            base_sha=base,
            record={"verification": {"exit_reason": "COMPLETED"}},
            episode_id="ep_r1",
            git_config_sha256=hashlib.sha256(
                (checkout / ".git" / "config").read_bytes()
            ).hexdigest(),
        )

    result, rec = run_flow(
        args,
        task_id=f"{task}-{place}",
        planner=FakePlanner([PlanReply("perform " + task, "configured-model", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "configured-model", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, runner),
        event_seq=event_seq,
    )
    return result, rec, repo


def _write_change(checkout: Path, task: str) -> None:
    app = checkout / "app.py"
    if task == "sed-i":
        app.write_text(app.read_text().replace("base", "sed-mutated"))
    elif task == "redirection":
        (checkout / "redirected.txt").write_text("created by shell redirection\n")
    elif task == "git-apply-two":
        patch = """diff --git a/app.py b/app.py
index df967b9..2bdf67a 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-base
+patched
diff --git a/.gitignore b/.gitignore
index 8b13789..e69de29 100644
--- a/.gitignore
+++ b/.gitignore
@@ -1 +1 @@
-ignored.tmp
+ignored.tmp\npatched.tmp
"""
        subprocess.run(
            ["git", "-C", str(checkout), "apply", "-"], input=patch, text=True, check=True
        )
    elif task == "python-mode":
        app.chmod(0o755)
    elif task == "patch-file":
        (checkout / "change.patch").write_text("diff --git a/app.py b/app.py\n")
    elif task == "formatter":
        app.write_text("def value( ):\n    return  1\n")
        (checkout / ".gitignore").write_text("ignored.tmp\n\n")
    elif task == "pytest-snapshot":
        snap = checkout / "tests" / "__snapshots__"
        snap.mkdir(parents=True)
        (snap / "test_app.ambr").write_text("snapshot\n")
    elif task == "tmp-script-nested":
        script = checkout.parent / "edit.py"
        script.write_text(
            "from pathlib import Path; Path('nested').mkdir(); Path('nested/x.py').write_text('x')"
        )
        subprocess.run([__import__("sys").executable, str(script)], cwd=checkout, check=True)
    elif task == "rename":
        app.rename(checkout / "renamed.py")
    elif task == "delete":
        app.unlink()
    elif task == "symlink":
        (checkout / "link.py").symlink_to("app.py")
    elif task == "ignored":
        (checkout / "ignored.tmp").write_text(task)
    elif task == "gitattributes":
        (checkout / ".gitattributes").write_text("*.py text eol=crlf\n")
    elif task == "empty-dir-file":
        (checkout / "empty-dir").mkdir()
        (checkout / "empty-dir" / "marker").touch()
    elif task == "exec-bit":
        app.chmod(app.stat().st_mode | 0o111)
    elif task == "crlf":
        app.write_bytes(app.read_bytes().replace(b"\n", b"\r\n"))
    elif task == "binary":
        (checkout / "blob.bin").write_bytes(b"\x00\xff\x10")
    elif task == "unicode-name":
        (checkout / "naïve-文件.txt").write_text("unicode\n")
    elif task == "dot-directory":
        (checkout / ".cache").mkdir()
        (checkout / ".cache" / "state").write_text("changed\n")
    elif task == "large-file":
        (checkout / "large.dat").write_bytes(b"x" * 1_000_000)


@pytest.mark.parametrize("task", TASKS)
def test_ac8_workaround_placements_keep_r0_and_discard_or_reject_r1(
    tmp_path: Path, task: str
) -> None:
    for place, reason in (
        ("r0", schema.Reason.PAUSE_R1_NOT_VERIFIED),
        ("r1", schema.Reason.PAUSE_SCOPE_VIOLATION),
    ):
        result, rec, repo = _scenario(tmp_path / place, task, place)
        assert result.episode_id == "ep_r0"
        assert rec["intervened"] is False and rec["reason"] == reason
        assert receipt.verify(rec) == []
        if place == "r0":
            r1_checkout = next((tmp_path / place / "runs").glob("*/checkout"))
            assert _git(r1_checkout, "status", "--porcelain", "--ignored") == ""
            assert _git(r1_checkout, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")


def test_ac8_event_sequence_increases_across_consecutive_runs(tmp_path: Path) -> None:
    values: list[int] = []

    def event_seq(_kind: str) -> int:
        value = len(values)
        values.append(value)
        return value

    for index in range(2):
        result, rec, _repo = _scenario(
            tmp_path / f"run-{index}", "sed-i-a", "r1", event_seq=event_seq
        )
        assert result.episode_id == "ep_r0"
        assert receipt.verify(rec) == []
    assert values == sorted(set(values))
    assert values == [0, 1]
