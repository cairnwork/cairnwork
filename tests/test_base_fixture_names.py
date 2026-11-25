"""#986 step 5b, unit level: what the extra base run is, and when it is not made."""

import logging
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
    return SimpleNamespace(
        repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t", test_roots=("tests",)
    )


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


@pytest.mark.parametrize(
    ("account", "paths", "kind", "error", "reason", "level"),
    [
        (object(), PATHS, "ok", None, "ACCOUNT_RUN", "INFO"),
        (None, ("tests/test_a.py",), "ok", None, "NO_CONFTEST_CHANGE", "INFO"),
        (None, ("tests/conftest.py", "tests/test_new.py"), "ok", None, "NO_BASE_TARGET", "INFO"),
        (None, PATHS, "ok", RuntimeError("x"), "RUN_FAULT", "WARNING"),
        (None, PATHS, "none", None, "NO_NAMES_RECORDED", "WARNING"),
        (None, PATHS, "ok", None, "OK", ""),
    ],
)
def test_a_none_carries_its_reason(
    task, monkeypatch, caplog, account, paths, kind, error, reason, level
):
    _calls(monkeypatch, None if kind == "none" else frozenset({"s"}), error=error)
    ctx = SimpleNamespace(account=account)
    with caplog.at_level("INFO", logger=bfn.LOG.name):
        names = bfn.collect(ctx, task, paths, "r-f")
    assert bfn.probe(ctx, task, paths, "r-f")[1] == reason
    assert (names is None) == (reason != "OK")
    assert [r.levelname for r in caplog.records] == ([level] if level else [])
    assert all(reason in r.getMessage() for r in caplog.records)


def test_a_run_count_other_than_one_is_logged_as_such(task, monkeypatch, caplog):
    monkeypatch.setattr(bfn, "run_verifiers", lambda *a, **k: ())
    ctx = SimpleNamespace(account=None)
    with caplog.at_level("INFO", logger=bfn.LOG.name):
        assert bfn.collect(ctx, task, PATHS, "r-f") is None
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "RUN_COUNT" in caplog.records[0].getMessage()


def test_an_empty_name_set_is_still_names_not_a_none(task, monkeypatch, caplog):
    _calls(monkeypatch, frozenset())
    ctx = SimpleNamespace(account=None)
    with caplog.at_level("INFO", logger=bfn.LOG.name):
        assert bfn.collect(ctx, task, PATHS, "r-f") == frozenset()
    assert bfn.probe(ctx, task, PATHS, "r-f") == (frozenset(), "OK")
    assert caplog.records == []


class _Meta(type):
    def __getattribute__(cls, attr):  # a candidate-shaped class whose name cannot be read
        if attr == "__name__":
            raise RuntimeError("name trap")
        return super().__getattribute__(attr)


class _Trap(Exception, metaclass=_Meta):
    pass


@pytest.mark.parametrize("hostile", ["/Users/x/secret\nINJECT", "tmp/\rpath"])
def test_an_exception_class_name_never_reaches_the_reason_or_log(
    task, monkeypatch, caplog, hostile
):
    _calls(monkeypatch, error=type(hostile, (Exception,), {})("x"))
    ctx = SimpleNamespace(account=None)
    with caplog.at_level("INFO", logger=bfn.LOG.name):
        assert bfn.collect(ctx, task, PATHS, "r-f") is None
    assert bfn.probe(ctx, task, PATHS, "r-f") == (None, "RUN_FAULT")
    assert hostile not in "".join(r.getMessage() for r in caplog.records)


def test_an_exception_whose_name_raises_still_gives_none(task, monkeypatch):
    _calls(monkeypatch, error=_Trap())
    ctx = SimpleNamespace(account=None)
    assert bfn.collect(ctx, task, PATHS, "r-f") is None
    assert bfn.probe(ctx, task, PATHS, "r-f") == (None, "RUN_FAULT")


class _Boom(logging.Handler):
    def emit(self, record):
        raise RuntimeError("handler failure")


@pytest.mark.parametrize("error", [None, RuntimeError("x")])
def test_a_log_handler_that_raises_does_not_change_the_result(task, monkeypatch, error):
    _calls(monkeypatch, None, error=error)
    ctx = SimpleNamespace(account=None)
    handler = _Boom(level=logging.DEBUG)
    bfn.LOG.addHandler(handler)
    try:
        assert bfn.collect(ctx, task, PATHS, "r-f") is None
        seen = {}
        monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: seen.update(k))
        bfn.check_with_base(ctx, task, PATHS, "r-f")
    finally:
        bfn.LOG.removeHandler(handler)
    assert seen == {"collected": None}
