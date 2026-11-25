"""#1767 (#986 AC7): the fixture-name collect covers the base's whole test tree.

Unit level: which files are the targets, and what happens when the whole-tree run gives nothing."""

import logging
import subprocess
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.base_tree_targets import base_tree_targets

FILES = {
    "tests/conftest.py": "",
    "tests/test_a.py": "",
    "tests/sib/test_s.py": "",
    "tests/deep/er/test_d.py": "",
    "tests/helpers.py": "",
    "tests/check_w.py": "",
    "other/test_z.py": "",
}
PATHS = ("tests/conftest.py", "tests/test_a.py", "tests/test_new.py")


def _repo(tmp_path, files, pyproject=""):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=x", "-c", "user.email=x@x", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    for name, text in {**files, **({"pyproject.toml": pyproject} if pyproject else {})}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    git("add", "-A")
    git("commit", "-qm", "base")
    return SimpleNamespace(
        repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t", test_roots=("tests",)
    )


@pytest.fixture
def task(tmp_path):
    return _repo(tmp_path, FILES)


def test_the_targets_are_every_base_test_file_under_the_roots(task):
    assert base_tree_targets(task.repo, task.base_commit, ("tests",)) == (
        "tests/deep/er/test_d.py",
        "tests/sib/test_s.py",
        "tests/test_a.py",
    )  # no conftest, no helper module, nothing outside the roots; sorted


def test_the_bases_own_python_files_decide_which(tmp_path):
    pyproject = '[tool.pytest.ini_options]\npython_files = ["check_*.py"]\n'
    task = _repo(tmp_path, FILES, pyproject)
    assert base_tree_targets(task.repo, task.base_commit, ("tests",)) == ("tests/check_w.py",)


def test_a_file_the_test_commit_adds_is_not_a_base_target(task):
    got = base_tree_targets(task.repo, task.base_commit, ("tests",))
    assert got is not None and "tests/test_new.py" not in got


def test_an_unreadable_base_gives_none(task):
    assert base_tree_targets(task.repo, "0" * 40, ("tests",)) is None


def _run(monkeypatch, outcomes):
    """Fake run_verifiers: one outcome per call (a names set, None, or an exception)."""
    calls = []

    def fake(ctx, commit, specs, run_id, **kwargs):
        calls.append((specs[0].targets, run_id))
        got = outcomes[len(calls) - 1]
        if isinstance(got, Exception):
            raise got
        return (SimpleNamespace(fixture_names=got),)

    monkeypatch.setattr(bfn, "run_verifiers", fake)
    return calls


CTX = SimpleNamespace(account=None)


def test_the_first_run_names_the_changed_files_then_the_rest_of_the_tree(task, monkeypatch):
    calls = _run(monkeypatch, [frozenset({"n"})])
    assert bfn.probe(CTX, task, PATHS, "r") == (frozenset({"n"}), "OK")
    assert calls == [
        (("tests/test_a.py", "tests/deep/er/test_d.py", "tests/sib/test_s.py"), "r")
    ]  # changed file first, once; one run only when it gave names


@pytest.mark.parametrize("first", [None, RuntimeError("boom")])
def test_a_whole_tree_run_without_names_falls_back_to_the_narrow_run(task, monkeypatch, first):
    calls = _run(monkeypatch, [first, frozenset({"n"})])
    assert bfn.probe(CTX, task, PATHS, "r") == (frozenset({"n"}), "TREE_RUN_FAILED")
    assert [c[0] for c in calls][1] == ("tests/test_a.py",)
    assert calls[0][1] != calls[1][1]  # two runs never share a run id


def test_both_runs_without_names_give_none_with_the_narrow_reason(task, monkeypatch):
    _run(monkeypatch, [None, RuntimeError("boom")])
    assert bfn.probe(CTX, task, PATHS, "r") == (None, "RUN_FAULT")
    _run(monkeypatch, [None, None])
    assert bfn.probe(CTX, task, PATHS, "r") == (None, "NO_NAMES_RECORDED")


def test_a_tree_over_the_cap_is_not_run_whole_and_says_so(task, monkeypatch, caplog):
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 2)
    calls = _run(monkeypatch, [frozenset({"n"})])
    with caplog.at_level("INFO", logger=bfn.LOG.name):
        assert bfn.collect(CTX, task, PATHS, "r") == frozenset({"n"})
    assert [c[0] for c in calls] == [("tests/test_a.py",)]
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "TREE_OVER_CAP" in caplog.records[0].getMessage()


@pytest.mark.parametrize(
    "extra", [(), ("tests/helpers.py",), ("tests/helpers.py", "tests/check_w.py")]
)
def test_what_the_candidate_changes_never_moves_the_tree_cap(task, monkeypatch, extra):
    """The cap reads the base tree alone: more changed base files cannot push the run to the
    narrow fallback (the 3 test files of the base fit a cap of 3 for every k)."""
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 3)
    calls = _run(monkeypatch, [frozenset({"n"})])
    paths = ("tests/conftest.py", "tests/test_a.py", *extra)
    assert bfn.probe(CTX, task, paths, "r") == (frozenset({"n"}), "OK")
    assert len(calls) == 1 and set(calls[0][0]) >= {
        "tests/sib/test_s.py",
        "tests/deep/er/test_d.py",
    }


def test_only_the_base_tests_under_a_new_conftests_directory_are_collected(task, monkeypatch):
    calls = _run(monkeypatch, [frozenset({"n"})])
    paths = ("tests/sib/conftest.py", "tests/test_a.py")
    assert bfn.probe(CTX, task, paths, "r") == (frozenset({"n"}), "OK")
    assert calls[0][0] == ("tests/test_a.py", "tests/sib/test_s.py")  # not deep/ and not others


def test_an_unreadable_tree_keeps_the_narrow_run(task, monkeypatch):
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: None)
    calls = _run(monkeypatch, [frozenset({"n"})])
    assert bfn.probe(CTX, task, PATHS, "r") == (frozenset({"n"}), "TREE_UNREADABLE")
    assert [c[0] for c in calls] == [("tests/test_a.py",)]


def test_the_guards_still_make_no_run(task, monkeypatch):
    calls = _run(monkeypatch, [frozenset({"n"})])
    assert bfn.probe(SimpleNamespace(account=object()), task, PATHS, "r")[1] == "ACCOUNT_RUN"
    assert bfn.probe(CTX, task, ("tests/test_a.py",), "r")[1] == "NO_CONFTEST_CHANGE"
    conftest_and_new = ("tests/conftest.py", "tests/test_new.py")
    assert bfn.probe(CTX, task, conftest_and_new, "r")[1] == "NO_BASE_TARGET"
    assert calls == []


def test_a_failing_log_handler_does_not_change_the_names(task, monkeypatch):
    class Boom(logging.Handler):
        def emit(self, record):
            raise RuntimeError("handler")

    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 1)
    _run(monkeypatch, [frozenset({"n"})])
    monkeypatch.setattr(bfn.LOG, "handlers", [Boom()])
    monkeypatch.setattr(bfn.LOG, "log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert bfn.collect(CTX, task, PATHS, "r") == frozenset({"n"})
