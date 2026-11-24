"""Hidden tests for fixture tasks: Core judges with them, the worker checkout never has them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork.intake import Intake
from experimental.local_relay import runtime
from experimental.local_relay.hidden_tests import (
    HiddenVerify,
    load_hidden,
    visible_targets,
    with_python,
)
from experimental.local_relay.worker_launch import write_task_file


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_public.py").write_text("def test_p():\n    pass\n")
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _hidden(tmp_path: Path) -> Path:
    hidden = tmp_path / "private"
    hidden.mkdir(mode=0o700)
    (hidden / "test_hidden.py").write_text("def test_h():\n    assert False\n")
    return hidden


def test_hidden_commit_is_fresh_each_call_and_absent_from_judged_repo(tmp_path):
    repo, base = _repo(tmp_path)
    seen = []

    def fake(intake, **kw):
        sha = kw["test_commit"]
        listing = _git(kw["repo"], "ls-tree", "-r", "--name-only", base)
        seen.append((sha, listing))
        probe = subprocess.run(
            ["git", "-C", str(kw["repo"]), "cat-file", "-e", sha], capture_output=True
        )
        assert probe.returncode != 0  # not in the repo the worker's candidate lives in
        assert intake.test_store().has(sha)
        return {"status": "VERIFIED"}

    hidden = load_hidden({"hidden_tests_dir": str(_hidden(tmp_path))}, repo)
    assert hidden is not None
    verify = HiddenVerify(repo, hidden, fake)
    intake = Intake(tmp_path / "state")
    for _ in range(2):
        assert verify(intake, repo=repo, base=base, candidate=base)["status"] == "VERIFIED"
    assert seen[0][0] != seen[1][0]
    assert all("test_hidden" not in listing for _sha, listing in seen)
    assert not intake.test_store().has(seen[0][0])  # bundle removed afterwards


def test_hidden_files_are_not_in_worker_checkout_or_task_file(tmp_path):
    repo, base = _repo(tmp_path)
    hidden = load_hidden({"hidden_tests_dir": str(_hidden(tmp_path))}, repo)
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(repo), str(checkout))
    assert not (checkout / "tests" / "test_hidden.py").exists()
    cmd = ("python3", "-m", "pytest", "-q", "tests/test_hidden.py", "tests/test_public.py")
    task = {"task_id": "a" * 32, "prompt": "p", "verifiers": [("v", cmd)], "hidden": hidden}
    run_root = tmp_path / "run"
    run_root.mkdir()
    text = write_task_file(run_root, task).read_text()
    assert "test_hidden" not in text and "test_public" in text
    assert visible_targets(("tests/test_hidden.py::test_h",), hidden) == ()


def test_core_gets_hidden_files_via_real_overlay_commit(tmp_path):
    repo, base = _repo(tmp_path)
    hidden = load_hidden({"hidden_tests_dir": str(_hidden(tmp_path))}, repo)
    assert hidden is not None
    intake = Intake(tmp_path / "state")
    got = {}

    def fake(intake, **kw):
        store = intake.test_store()
        target = tmp_path / "fetched"
        _git(tmp_path, "clone", "-q", "--no-checkout", str(repo), str(target))
        store.fetch_into(target, kw["test_commit"])
        got["files"] = _git(target, "ls-tree", "-r", "--name-only", kw["test_commit"]).split()
        got["parent"] = _git(target, "rev-parse", f"{kw['test_commit']}^")
        return {"status": "VERIFIED"}

    HiddenVerify(repo, hidden, fake)(intake, repo=repo, base=base, candidate=base)
    assert "tests/test_hidden.py" in got["files"] and got["parent"] == base


@pytest.mark.parametrize(
    "extra",
    [
        {"hidden_tests_dir": "relative/dir"},
        {"hidden_tests_dir": "INSIDE"},
        {"hidden_tests_dir": "MISSING"},
        {"verifier_python": "/bin/sh"},
        {"hidden_tests_dir": "OK", "verifier_python": "relative/python"},
    ],
)
def test_bad_hidden_config_is_refused(tmp_path, extra):
    repo, _base = _repo(tmp_path)
    ok = str(_hidden(tmp_path))
    extra = {
        k: {"INSIDE": str(repo / "tests"), "MISSING": str(tmp_path / "nope"), "OK": ok}.get(v, v)
        for k, v in extra.items()
    }
    with pytest.raises(ValueError):
        load_hidden(extra, repo)


def test_group_readable_hidden_dir_is_refused(tmp_path):
    repo, _base = _repo(tmp_path)
    hidden = _hidden(tmp_path)
    hidden.chmod(0o750)
    with pytest.raises(ValueError, match="group or others"):
        load_hidden({"hidden_tests_dir": str(hidden)}, repo)


def test_symlink_in_hidden_dir_is_refused(tmp_path):
    repo, _base = _repo(tmp_path)
    hidden = _hidden(tmp_path)
    (hidden / "link.py").symlink_to(repo / "src" / "calc.py")
    with pytest.raises(ValueError, match="symlink"):
        load_hidden({"hidden_tests_dir": str(hidden)}, repo)


def test_verifier_python_overrides_only_that_key(tmp_path):
    intake = Intake(tmp_path / "state")
    before = intake.config()
    patched = with_python(intake, "/usr/bin/python3")
    after = patched.config()
    assert after["verifier_python"] == "/usr/bin/python3"
    assert {k: v for k, v in after.items() if k != "verifier_python"} == {
        k: v for k, v in before.items() if k != "verifier_python"
    }


def test_runtime_checks_hidden_target_in_private_dir_not_base(tmp_path):
    repo, _base = _repo(tmp_path)
    data = {
        "task_id": "b" * 32, "prompt": "fix add", "allowed_files": ["src/calc.py"],
        "verifiers": [["python3", "-m", "pytest", "-q", "tests/test_hidden.py"]],
        "hidden_tests_dir": str(_hidden(tmp_path)),
    }  # fmt: skip
    task = runtime._validate_task(data, repo)
    assert task["hidden"].files == ("tests/test_hidden.py",)
    data["verifiers"] = [["python3", "-m", "pytest", "-q", "tests/test_absent.py"]]
    with pytest.raises(Exception, match="TASK_VERIFIER_TARGET_MISSING"):
        runtime._validate_task(data, repo)


def test_symlink_in_a_parent_path_component_is_refused(tmp_path):
    repo, _base = _repo(tmp_path)
    parent = tmp_path / "real_parent"
    parent.mkdir()
    _hidden_at(parent / "private")
    (tmp_path / "alias").symlink_to(parent)
    with pytest.raises(ValueError, match="symlink"):
        load_hidden({"hidden_tests_dir": str(tmp_path / "alias" / "private")}, repo)
    assert load_hidden({"hidden_tests_dir": str(parent / "private")}, repo) is not None


def _hidden_at(path: Path) -> Path:
    path.mkdir(mode=0o700)
    (path / "test_hidden.py").write_text("def test_h():\n    assert False\n")
    return path


def test_trusted_link_target_with_untrusted_symlink_is_refused(tmp_path):
    from experimental.local_relay.hidden_tests import _refuse_symlink_components

    real = tmp_path / "real"
    real.mkdir()
    _hidden_at(real / "private")
    user_dir = tmp_path / "user_dir"
    user_dir.mkdir()
    (user_dir / "inner").symlink_to(real)  # not trusted
    trusted = tmp_path / "trusted_link"
    trusted.symlink_to(user_dir)  # stands in for a root-owned link into user-controlled space
    with pytest.raises(ValueError, match="symlink"):
        _refuse_symlink_components(trusted / "inner" / "private", lambda p: p == trusted)


def test_trusted_link_to_a_clean_path_passes(tmp_path):
    from experimental.local_relay.hidden_tests import _refuse_symlink_components

    real = tmp_path / "real"
    real.mkdir()
    _hidden_at(real / "private")
    trusted = tmp_path / "trusted_link"
    trusted.symlink_to(real)
    _refuse_symlink_components(trusted / "private", lambda p: p == trusted)  # no error


def test_trusted_link_cycle_is_refused(tmp_path):
    from experimental.local_relay.hidden_tests import _refuse_symlink_components

    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    with pytest.raises(ValueError, match="symlink"):
        _refuse_symlink_components(a / "x", lambda p: True)
