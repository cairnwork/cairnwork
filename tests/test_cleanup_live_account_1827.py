"""#1827: a dead run's cleanup must not report "cleaned" while a candidate-side process may
still run. The sweep names the verifier account and the run's directories; leftovers under the
account's run root count; a sweep that is not clean never yields "done" on either path."""

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from test_recovery import crash, stores
from test_recovery import project as project  # noqa: F401 - fixture

from cairnwork import cleanup_sweep, confined, proc_start, recovery
from cairnwork import verify_candidate as module
from cairnwork.confined import Sweep
from cairnwork.intake import Intake
from cairnwork.verifier_account import VerifierAccount

AID = "a0123456789abcdef"
USER = "cwacct1827"
UID = 4242

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root removes any directory")


def _orphan(state: Path) -> Path:
    (state / "verify-runs").mkdir(parents=True)
    marker = state / "verify-runs" / AID
    marker.write_text(proc_start.boot_mark())
    (state / "verify-scratch" / AID).mkdir(parents=True)
    return marker


def _account(monkeypatch, tmp_path, *, uid: int | None = UID) -> Any:
    account = VerifierAccount(user=USER, root=tmp_path / "acct")
    account.run_root.mkdir(parents=True)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: account)
    monkeypatch.setattr(confined, "_account_uid", lambda name: uid)
    return account


def _table(monkeypatch, rows):
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: list(rows))
    monkeypatch.setattr(confined, "_restricted", lambda: "")
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)


FAMILY = {
    "hidden-environment": [(900001, 900001, UID, None)],
    "cleared-environment": [(900001, 900001, UID, set())],
    "two-processes": [(900001, 900001, UID, None), (900002, 900002, UID, set())],
}


@pytest.mark.parametrize("rows", FAMILY.values(), ids=FAMILY.keys())
def test_a_live_account_process_keeps_the_attempt(tmp_path, monkeypatch, rows):
    marker = _orphan(tmp_path)
    _account(monkeypatch, tmp_path)
    _table(monkeypatch, rows)
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert marker.exists()


class _Stubborn:
    """An account whose own run directories cannot be removed by the owner."""

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.user = ""

    def remove(self, path: Path) -> None:
        pass


def test_an_account_run_directory_that_stays_keeps_the_attempt(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)
    acct = _Stubborn(tmp_path / "acct" / "runs")
    (acct.run_root / f"{AID}-v-v0").mkdir(parents=True)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: acct)
    _table(monkeypatch, [])
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert marker.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="cwd attribution is macOS-only")
def test_a_cleared_environment_process_in_the_run_directory_is_not_left_running(tmp_path):
    marker = _orphan(tmp_path)
    run_dir = tmp_path / "runs" / f"{AID}-v-v0"
    run_dir.mkdir(parents=True)
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        ["/usr/bin/env", "-i", "/bin/sleep", "60"], cwd=run_dir, start_new_session=True
    )
    try:
        time.sleep(0.3)
        out = module.cleanup(Intake(tmp_path))
        deadline = time.time() + 5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None or out.get("kept") == [AID]
        assert out.get("cleaned") != [AID] or proc.poll() is not None
        assert marker.exists() or proc.poll() is not None
    finally:
        proc.kill()
        proc.wait()


def test_nothing_alive_and_nothing_left_is_cleaned(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)
    _account(monkeypatch, tmp_path)
    _table(monkeypatch, [])
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def test_an_account_user_that_does_not_exist_owns_no_process(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)
    _account(monkeypatch, tmp_path, uid=None)
    _table(monkeypatch, [])
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def test_the_default_path_looks_twice_and_names_no_account(tmp_path, monkeypatch):
    _orphan(tmp_path)
    calls: list[dict] = []

    def look(*_a, **kw):
        calls.append(kw)
        return Sweep(listed=True)

    monkeypatch.setattr(module, "sweep_marked", look)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: None)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert len(calls) == 2
    assert all(not kw.get("account") for kw in calls)


class _Acct:
    user = USER

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root

    def exclusive(self):
        return contextlib.nullcontext()

    def require_idle(self) -> None:
        pass

    def remove(self, path: Path) -> None:
        pass


def _recover(project, tmp_path, account):  # noqa: F811
    crash(project, tmp_path, "CANDIDATE")
    ledger, notary, journal = stores(tmp_path)
    return recovery.recover(
        ledger=ledger, notary=notary, journal=journal, run_root=tmp_path / "runs",
        verifier_account=account,
    )  # fmt: skip


def test_recovery_with_a_live_account_process_is_unavailable(  # noqa: F811
    project, tmp_path, monkeypatch
):
    monkeypatch.setattr(confined, "_account_uid", lambda name: UID)
    _table(monkeypatch, [(900001, 900001, UID, None)])
    (decision,) = _recover(project, tmp_path, _Acct(tmp_path / "acct"))
    assert decision.sweep_unavailable is True
    assert decision.detail.startswith("WORKER_ACCOUNT_BUSY")
    assert decision.leftover_run_dirs == 0  # nothing listed, yet the result is not clean


def test_an_unclean_sweep_with_no_leftovers_stays_unavailable(  # noqa: F811
    project, tmp_path, monkeypatch
):
    """Regression pin (already green on main): not clean + empty leftover list != clean."""
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: confined.UNLISTED)
    (decision,) = _recover(project, tmp_path, None)
    assert decision.sweep_unavailable is True and decision.leftover_run_dirs == 0


def test_recovery_default_path_is_clean(project, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: Sweep(listed=True))
    (decision,) = _recover(project, tmp_path, None)
    assert decision.detail == "" and decision.sweep_unavailable is False


def test_user_of_ignores_missing_unknown_and_owner_users(monkeypatch):
    class A:
        user = USER

    assert cleanup_sweep.user_of(None) is None
    assert cleanup_sweep.user_of(object()) is None
    monkeypatch.setattr(confined, "_account_uid", lambda name: None)
    assert cleanup_sweep.user_of(A()) is None
    monkeypatch.setattr(confined, "_account_uid", lambda name: os.getuid())
    assert cleanup_sweep.user_of(A()) is None
    monkeypatch.setattr(confined, "_account_uid", lambda name: UID)
    assert cleanup_sweep.user_of(A()) == USER


def test_merged_is_unclean_if_either_look_is():
    both = cleanup_sweep.merged(Sweep(listed=True), Sweep(listed=True, account=1))
    assert both.listed and not both.gone and both.account == 1
    assert not cleanup_sweep.merged(Sweep(listed=True), Sweep(listed=False)).listed
    assert cleanup_sweep.merged(Sweep(listed=True), Sweep(listed=True)).gone


def test_look_at_looks_once_per_distinct_account():
    seen: list[Any] = []

    def look(name):
        seen.append(name)
        return Sweep(listed=True)

    cleanup_sweep.look_at(look, None, None)
    cleanup_sweep.look_at(look, "w", "v", "w")
    assert seen == [None, "w", "v"]
