"""#1768: the nested-config rule's edges (symlink, submodule, unlisted tree, upper-case name,
``-o`` in addopts) and the file comparison's failure injections: a run that cannot be read is
refused, never passed."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change, git

from cairnwork import collect_parity
from cairnwork.collect_parity import _collected_files, file_parity, nested_naming
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.gitcmd import GitCommandError

KEY = "[pytest]\npython_files = check_*\n"


def _task(repo, files=None):
    base = commit_change(repo, {"tests/test_a.py": "def test_a(): pass\n", **(files or {})})
    return SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))


def _refused(task):
    with pytest.raises(BaseUncomparable) as got:
        nested_naming(task)
    assert got.value.comparison.code == "P2P_BASE_NESTED_NAMING"


def test_a_symlinked_config_below_the_root_is_refused(repo):
    (repo / "sub").mkdir()
    (repo / "real.cfg").write_text("[tool:pytest]\npython_files = check_*\n")
    os.symlink("../real.cfg", repo / "sub" / "setup.cfg")
    _refused(_task(repo))


def test_a_submodule_entry_named_like_a_config_is_refused(repo):
    task = _task(repo)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{task.base_commit},sub/pytest.ini")
    git(repo, "commit", "-q", "-m", "gitlink")  # not commit_change: add -A drops a missing gitlink
    task.base_commit = git(repo, "rev-parse", "HEAD")
    _refused(task)


def test_a_tree_that_cannot_be_listed_is_refused(repo, monkeypatch):
    task = _task(repo)

    def boom(*args: Any, **kwargs: Any) -> bytes:
        raise GitCommandError("no tree")

    monkeypatch.setattr(collect_parity, "run_git_bytes", boom)
    _refused(task)


@pytest.mark.parametrize("name", ["sub/PYTEST.INI", "sub/Pytest.Ini", "sub/TOX.INI"])
def test_an_upper_case_config_name_is_read_as_the_config_it_is(repo, name):
    _refused(_task(repo, {name: KEY}))


@pytest.mark.parametrize(
    "opts",
    [
        "-o python_files=check_*",
        "--override-ini=python_classes=Check*",
        "-q -o python_functions=c_*",
    ],
)
def test_naming_set_through_addopts_of_a_nested_config_is_refused(repo, opts):
    _refused(_task(repo, {"sub/pytest.ini": f"[pytest]\naddopts = {opts}\n"}))


@pytest.mark.parametrize("opts", ["-q", "--strict-markers -x", "%(x)s"])
def test_other_addopts_in_a_nested_config_are_not_refused(repo, opts):
    nested_naming(_task(repo, {"sub/pytest.ini": f"[pytest]\naddopts = {opts}\n"}))


# the file comparison: failure injections on the collect-only run


def _run(**kw: Any) -> Any:
    base: dict[str, Any] = dict(
        returncode=0, forged=False, timed_out=False, survivors=False, mutated=False,
        report_too_big=False, log_too_big=False, recorded=True, collection_finished=True,
        outcomes={}, collect_errors=(), collect_skipped=(), collected=("tests/test_a.py::test_a",),
        log_path="/nonexistent/x/log",
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def test_a_clean_run_gives_its_files():
    assert _collected_files(_run()) == ({"tests/test_a.py"}, "")


@pytest.mark.parametrize(
    "kw",
    [
        {"timed_out": True}, {"forged": True}, {"survivors": True}, {"mutated": True},
        {"recorded": False}, {"collection_finished": False}, {"returncode": None},
        {"returncode": 2}, {"returncode": 3}, {"returncode": 4},
        {"collect_errors": ("tests/test_b.py",), "returncode": 2},
        {"collect_skipped": ("tests/test_b.py",)}, {"outcomes": {"x": "passed"}},
    ],
)  # fmt: skip
def test_a_run_that_cannot_be_read_gives_no_files(kw):
    files, why = _collected_files(_run(**kw))
    assert files == set() and why


def _parity(repo, tmp_path, monkeypatch, run):
    task = _task(repo, {"tests/test_b.py": "def test_b(): pass\n"})
    monkeypatch.setattr(collect_parity, "run_verifiers", lambda *a, **k: [run])
    run.log_path = str(tmp_path / "r" / "log")
    ctx = SimpleNamespace()
    return lambda: file_parity(task, ctx, 60, "a1")


def test_an_unreadable_run_is_refused_not_passed(repo, tmp_path, monkeypatch):
    call = _parity(repo, tmp_path, monkeypatch, _run(timed_out=True))
    with pytest.raises(BaseUncomparable) as got:
        call()
    assert got.value.comparison.code == "P2P_BASE_COLLECT_UNUSABLE"


def test_nothing_collected_while_files_are_counted_is_refused(repo, tmp_path, monkeypatch):
    call = _parity(repo, tmp_path, monkeypatch, _run(returncode=5, collected=()))
    with pytest.raises(BaseUncomparable) as got:
        call()
    assert got.value.comparison.code == "P2P_BASE_FILES_UNCOLLECTED"


def test_every_counted_file_collected_is_no_refusal(repo, tmp_path, monkeypatch):
    run = _run(collected=("tests/test_a.py::test_a", "tests/test_b.py::test_b[1]"))
    _parity(repo, tmp_path, monkeypatch, run)()
