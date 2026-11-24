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

    def scrub_stale_runs(self, candidate_tree):
        pass


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
    monkeypatch.setattr(verifiers, "opaque_clone_id", lambda attempt_id: "run-x-v0")  # #1698
    work = tmp_path / "run-x-v0.work"
    work.mkdir()
    with pytest.raises(WorkspaceError, match="leftover work directory"):
        verifiers._run_all(_batch(tmp_path), _specs(1), "run")


def test_owner_mode_never_requires_idle(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    verifiers._run_all(_batch(tmp_path), _specs(2), "run")


def test_leftover_work_directory_message_escapes_control_characters(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    # the clone id is opaque now (#1698): the control characters come from the stubbed name
    monkeypatch.setattr(verifiers, "opaque_clone_id", lambda attempt_id: "r\n::error::x-v0")
    (tmp_path / "r\n::error::x-v0.work").mkdir()
    with pytest.raises(WorkspaceError) as caught:
        verifiers._run_all(_batch(tmp_path), _specs(1), "r\n::error::x")
    assert "\n" not in str(caught.value)


def test_blackbox_leftover_work_directory_is_workspace_error(tmp_path):
    from cairnwork import blackbox_exec

    ctx: Any = SimpleNamespace(run_root=tmp_path, account=None, attempt_id="")
    (tmp_path / "run-vbb.work").mkdir()
    with pytest.raises(WorkspaceError, match="leftover work directory"):
        blackbox_exec._run(None, "c", "run", 1, (), ctx, "/usr/bin/python3")


class _ScrubAccount(FakeAccount):
    """Refuses at the Nth scrub, as a real account does when a closed run's run/ is still there."""

    def __init__(self, run_root, refuse_at):
        super().__init__(run_root)
        self.scrub_calls = 0
        self.refuse_at = refuse_at

    def scrub_stale_runs(self, candidate_tree):
        self.scrub_calls += 1
        if self.scrub_calls == self.refuse_at:
            from cairnwork.verifier_account import VerifierAccountError

            raise VerifierAccountError("private/run left behind")


def test_account_leftover_run_after_a_spec_stops_the_remaining_specs(tmp_path, monkeypatch):
    """#437 D: v0's run/ still there after close_private: v1 and v2 never start."""
    from cairnwork.verifier_account import VerifierAccountError

    _stub_run(monkeypatch)
    account = _ScrubAccount(tmp_path, refuse_at=1)
    started = []
    monkeypatch.setattr(
        verifiers, "_run_one", lambda batch, spec, checkout: started.append(spec.verifier_id)
    )
    with pytest.raises(VerifierAccountError, match="left behind"):
        verifiers._run_all(_batch(tmp_path, account), _specs(3), "run")
    assert started == ["v0"]
    assert account.idle_calls == 1  # idle first, then the scrub, then nothing else


def test_account_scrub_runs_between_specs_only(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    account = _ScrubAccount(tmp_path, refuse_at=0)
    verifiers._run_all(_batch(tmp_path, account), _specs(3), "run")
    assert account.scrub_calls == 2  # before v1 and v2, not before v0 (the prelude did that)
