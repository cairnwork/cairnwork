"""The gate reviews the change itself with repo-relative paths, never a RunResult repr."""

import subprocess

import pytest

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.gate_plan import build_gate_plan
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


def test_an_oversized_or_unshowable_change_raises_instead_of_being_cut(tmp_path):
    from experimental.local_relay.gate_plan import PlanIncomplete

    repo, base = _repo(tmp_path)
    result = RunResult(0, "", repo.parent, repo, base_sha=base)
    (repo / "pkg" / "stats.py").write_text("x = 1\n" * 20000)  # diff over the byte cap
    with pytest.raises(PlanIncomplete):
        build_gate_plan(TASK, result)
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[n:]\n")
    (repo / "pkg" / "big.py").write_text("y = 2\n" * 4000)  # new file over the per-file cap
    with pytest.raises(PlanIncomplete):
        build_gate_plan(TASK, result)


def test_new_symlinks_show_the_target_text_and_never_leak_the_pointed_to_file(tmp_path):
    repo, base = _repo(tmp_path)
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOP-SECRET-HOST-DATA\n")
    (repo / "pkg" / "leak.py").symlink_to(secret)
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "TOP-SECRET-HOST-DATA" not in plan
    assert "New file pkg/leak.py:\n(symlink to " in plan


def test_hard_linked_new_files_are_refused(tmp_path):
    import os

    from experimental.local_relay.gate_plan import PlanIncomplete

    repo, base = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("planted\n")
    os.link(outside, repo / "pkg" / "hard.py")
    with pytest.raises(PlanIncomplete):
        build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))


def test_untracked_files_are_listed_like_acceptance_even_when_gitignored(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("hidden.py\n")
    (repo / "hidden.py").write_text("SNEAKY = 1\n")
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "New file hidden.py" in plan and "SNEAKY = 1" in plan


def test_worker_controlled_textconv_and_attributes_are_never_executed(tmp_path):
    repo, base = _repo(tmp_path)
    marker = tmp_path / "executed"
    hook = tmp_path / "textconv.sh"
    hook.write_text(f'#!/bin/sh\ntouch {marker}\ncat "$1"\n')
    hook.chmod(0o755)
    with (repo / ".git" / "config").open("a") as handle:
        handle.write(f'[diff "evil"]\n\ttextconv = {hook}\n')
    (repo / ".gitattributes").write_text("*.py diff=evil\n")
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[-n:]\n")
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "+    return items[-n:]" in plan and not marker.exists()


def test_unsafe_relative_paths_are_refused(tmp_path):
    import os

    from experimental.local_relay.gate_plan import PlanIncomplete, _read_new_file

    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for bad in ("../escape.py", "/etc/passwd"):
            with pytest.raises(PlanIncomplete):
                _read_new_file(root_fd, os.fstat(root_fd).st_dev, bad)
    finally:
        os.close(root_fd)


def test_relay_rejects_the_stage_instead_of_approving_an_incomplete_plan(monkeypatch):
    from experimental.local_relay.gate_plan import PlanIncomplete

    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    calls = []

    class Gate:
        def review(self, request):
            calls.append(request)
            return GateReply("ACCEPT", "m", "e", None)

    class Rows:
        def append(self, row):
            pass

    def incomplete(_cand):
        raise PlanIncomplete("plan exceeds 30000 characters")

    result = run_relay(
        "t",
        lambda _ctx: Attempt(object(), "local", 1, 1, 0.0),
        lambda _cand: Acceptance(True, "ok"),
        Gate(),
        lambda _t: Attempt(object(), "cloud", 1, 1, 0.1),
        Rows(),
        RelayConfig(enabled=True, plan_for=incomplete),
    )
    assert calls == [] and result.route == "FAILED"  # every stage rejected, none approved
    assert [s["gate_reason"][:15] for s in result.stages if "gate_reason" in s] == [
        "PLAN_INCOMPLETE"
    ] * 3


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


def test_attributes_cannot_hide_the_diff_behind_a_binary_placeholder(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.py -diff\n")  # worker-writable
    (repo / ".gitattributes").write_text("*.py binary\n")
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[-n:]\n")
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "+    return items[-n:]" in plan and "Binary files" not in plan


def test_a_binary_placeholder_in_the_diff_is_refused(monkeypatch, tmp_path):
    from experimental.local_relay import gate_plan
    from experimental.local_relay.gate_plan import PlanIncomplete

    repo, base = _repo(tmp_path)
    monkeypatch.setattr(gate_plan, "_git", lambda *_a, **_k: b"Binary files a/x and b/x differ\n")
    with pytest.raises(PlanIncomplete):
        build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))


def _update_index(repo, *args):
    import os

    subprocess.run(
        ["git", "-C", str(repo), "update-index", *args],
        check=True,
        env={**os.environ, **ENV},
    )


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_bits_set_by_the_worker_cannot_hide_a_modified_file(tmp_path, flag):
    repo, base = _repo(tmp_path)
    _update_index(repo, flag, "pkg/stats.py")
    (repo / "pkg" / "stats.py").write_text("def last_n(items, n):\n    return items[-n:]\n")
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "+    return items[-n:]" in plan


def test_a_removed_tracked_file_and_a_redirected_worktree_setting_are_still_visible(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "pkg" / "stats.py").unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with (repo / ".git" / "config").open("a") as handle:
        handle.write(f"[core]\n\tworktree = {elsewhere}\n")
    plan = build_gate_plan(TASK, RunResult(0, "", repo.parent, repo, base_sha=base))
    assert "-def last_n(items, n):" in plan  # the deletion is seen; core.worktree is ignored
