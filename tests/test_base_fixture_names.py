"""#986 step 5b, unit level: what the extra base run is, and when it is not made."""

import subprocess
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names as bfn

PATHS = ("tests/conftest.py", "tests/test_a.py", "tests/test_new.py")


@pytest.fixture
def task(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=x", "-c", "user.email=x@x", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "tests").mkdir()
    for name in ("conftest.py", "test_a.py"):
        (tmp_path / "tests" / name).write_text("")
    git("add", "-A")
    git("commit", "-qm", "base")
    return SimpleNamespace(repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t")


def _calls(monkeypatch, result=None, error=None):
    calls = []

    def fake(ctx, commit, specs, run_id, **kwargs):
        calls.append((commit, specs, run_id, kwargs))
        if error:
            raise error
        return (SimpleNamespace(fixture_names=result),)

    monkeypatch.setattr(bfn, "run_verifiers", fake)
    return calls


def test_the_base_run_only_collects_and_records(task, monkeypatch):
    calls = _calls(monkeypatch, frozenset({"shared"}))
    names = bfn.collect(SimpleNamespace(account=None), task, PATHS, "r-f")
    assert names == frozenset({"shared"})
    [(commit, specs, run_id, _)] = calls
    assert commit == task.base_commit and run_id == "r-f"
    [spec] = specs
    assert spec.collect_only and spec.record_fixtures and not spec.continue_on_collect_errors
    assert spec.targets == (
        "tests/test_a.py",
    )  # only a base file; not the conftest, not a new file


def test_a_fault_in_the_base_run_leaves_the_old_verdict(task, monkeypatch):
    _calls(monkeypatch, error=RuntimeError("boom"))
    assert bfn.collect(SimpleNamespace(account=None), task, PATHS, "r-f") is None
    seen = {}
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: seen.update(k))
    bfn.check_with_base(SimpleNamespace(account=None), task, PATHS, "r-f")
    assert seen == {"collected": None}


def test_an_account_run_does_not_collect(task, monkeypatch):
    calls = _calls(monkeypatch, frozenset({"shared"}))
    assert bfn.collect(SimpleNamespace(account=object()), task, PATHS, "r-f") is None
    assert calls == []


@pytest.mark.parametrize(
    "paths", [("tests/test_a.py",), ("tests/conftest.py", "tests/test_new.py")]
)
def test_no_conftest_or_no_base_target_makes_no_run(task, monkeypatch, paths):
    calls = _calls(monkeypatch, frozenset({"shared"}))
    assert bfn.collect(SimpleNamespace(account=None), task, paths, "r-f") is None
    assert calls == []
