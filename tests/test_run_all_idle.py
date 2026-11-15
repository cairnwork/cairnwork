from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import verifiers
from cairnwork.workspace import WorkspaceError


class FakeAccount:
    def __init__(self, run_root):
        self.run_root = run_root
        self.idle_calls = 0

    def require_idle(self):
        self.idle_calls += 1


def _batch(tmp_path, account=None):
    ctx: Any = SimpleNamespace(
        run_root=tmp_path,
        repo=tmp_path,
        account=account,
        attempt_id="attempt",
    )
    settings: Any = None
    return verifiers._Batch(ctx, "/usr/bin/python3", "candidate", 1, settings, "", False)


def _specs(count):
    return [SimpleNamespace(verifier_id=f"v{i}") for i in range(count)]


def _stub_run(monkeypatch):
    @contextmanager
    def uninterrupted(*args, **kwargs):
        yield

    monkeypatch.setattr(verifiers, "unless_interrupted", uninterrupted)
    monkeypatch.setattr(verifiers, "_remove_checkout", lambda *args: None)
    monkeypatch.setattr(
        verifiers, "make_account_checkout", lambda account, py, repo, commit, work, cid: work / cid
    )
    monkeypatch.setattr(
        verifiers,
        "create_attempt_clone",
        lambda repo, commit, work, cid: SimpleNamespace(path=work / cid),
    )
    monkeypatch.setattr(verifiers, "_run_one", lambda *args: object())


def test_account_requires_idle_before_each_spec_after_first(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    account = FakeAccount(tmp_path)
    seen = []
    monkeypatch.setattr(
        verifiers,
        "_run_one",
        lambda batch, spec, checkout: seen.append(account.idle_calls),
    )
    verifiers._run_all(_batch(tmp_path, account), _specs(3), "run")
    assert seen == [0, 1, 2]
    assert account.idle_calls == 2


def test_leftover_work_directory_is_workspace_error(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    work = tmp_path / "run-v0.work"
    work.mkdir()
    with pytest.raises(WorkspaceError, match="leftover work directory"):
        verifiers._run_all(_batch(tmp_path), _specs(1), "run")


def test_owner_mode_never_requires_idle(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    verifiers._run_all(_batch(tmp_path), _specs(2), "run")
