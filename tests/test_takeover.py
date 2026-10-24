"""#244 window 1: a takeover (supersede, then a new key) is refused while the FAILED attempt's
processes are unchecked (takeover.py). The process table is faked here; the real sweep and a
real surviving child are in test_recovery_windows.py."""

import json

import pytest
from test_pipeline import VERIFY, EditWorker
from test_recovery import crash, settle, stores
from test_recovery import project as project  # noqa: F401 - fixture
from test_recovery_leftovers import _routable

import cairnwork.recovery as recovery
from cairnwork import route, takeover
from cairnwork.confined import SWEEP_UNAVAILABLE
from cairnwork.ownership import OWNER, DuplicateLogicalTask, OwnershipLedger, TakeoverRefused
from cairnwork.pipeline import TaskSpec, run_task


def _failed_task(tmp_path, *, unswept):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    ledger.set_status(task_id, "FAILED")
    if unswept:
        ledger.mark_unswept(task_id, "a1")
    return ledger, task_id


def _sweeps(monkeypatch, *answers):
    """takeover.sweep_marked answers in turn (the last one repeats); returns the call count."""
    calls = []

    def fake(var, value, **_kw):
        calls.append((var, value))
        return answers[min(len(calls), len(answers)) - 1]

    monkeypatch.setattr(takeover, "sweep_marked", fake)
    monkeypatch.setattr(takeover, "_CONFIRM_WAIT", 0)
    return calls


def test_an_unavailable_sweep_refuses_and_the_new_attempt_never_starts(
    project, tmp_path, monkeypatch
):  # noqa: F811
    ledger, task_id = _failed_task(tmp_path, unswept=True)
    _sweeps(monkeypatch, SWEEP_UNAVAILABLE)
    with pytest.raises(TakeoverRefused) as refused:
        takeover.supersede(ledger, task_id)
    assert refused.value.code == "TAKEOVER_REFUSED" and "SWEEP_UNAVAILABLE" in str(refused.value)
    assert ledger.get(task_id=task_id).status == "FAILED"
    held = ledger.unswept_attempt(task_id)
    assert held is not None and held[0] == "a1" and "SWEEP_UNAVAILABLE" in held[1]
    started = []

    class Worker(EditWorker):
        def run(self, workdir, task):
            started.append(workdir)

    base = _spec_base(project)
    with pytest.raises(DuplicateLogicalTask):  # the card is still held
        run_task(
            base, ledger=ledger, notary=stores(tmp_path)[1], journal=stores(tmp_path)[2],
            worker=Worker({}), run_root=tmp_path / "runs",
        )  # fmt: skip
    assert not started


def _spec_base(project):  # noqa: F811
    from conftest import git

    return TaskSpec(
        "k2", "card-k1", "fix add", "x", project, git(project, "rev-parse", "HEAD"),
        ("src/calc.py",), VERIFY,
    )  # fmt: skip


def test_survivors_that_are_killed_and_gone_allow_the_takeover(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, unswept=True)
    calls = _sweeps(monkeypatch, 2, 1, 0)  # killed 2; one still listed; then none
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert len(calls) == 3 and {c[0] for c in calls} == {"CAIRNWORK_ATTEMPT"}
    assert {c[1] for c in calls} == {"a1"}  # the same tag recovery sweeps
    assert ledger.unswept_attempt(task_id) is None


def test_survivors_that_remain_refuse_the_takeover(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, unswept=True)
    _sweeps(monkeypatch, 1)  # every sweep still finds (and kills) one
    with pytest.raises(TakeoverRefused, match="SURVIVORS_REMAIN"):
        takeover.supersede(ledger, task_id)
    assert ledger.get(task_id=task_id).status == "FAILED"
    assert "SURVIVORS_REMAIN" in (ledger.unswept_attempt(task_id) or ("", ""))[1]


def test_a_sweep_that_runs_and_finds_none_allows_the_takeover(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, unswept=True)
    calls = _sweeps(monkeypatch, 0)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert len(calls) == 1 and ledger.unswept_attempt(task_id) is None


def test_a_checked_attempt_supersedes_as_before_without_sweeping(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, unswept=False)
    calls = _sweeps(monkeypatch, SWEEP_UNAVAILABLE)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert not calls  # nothing to check, nothing refused


def test_recovery_marks_only_an_attempt_whose_sweep_was_unavailable(project, tmp_path, monkeypatch):  # noqa: F811
    crash(project, tmp_path, "CANDIDATE")
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: SWEEP_UNAVAILABLE)
    (decision,), ledger = settle(tmp_path)
    held = ledger.unswept_attempt(decision.task_id)
    assert held is not None and held[0] == decision.attempt_id
    with pytest.raises(TakeoverRefused):
        ledger.set_status(decision.task_id, "SUPERSEDED")
    other = tmp_path / "clean"
    other.mkdir()
    crash(project, other, "CANDIDATE")
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: 0)
    (clean,), clean_ledger = settle(other)
    assert clean_ledger.unswept_attempt(clean.task_id) is None
    assert clean_ledger.set_status(clean.task_id, "SUPERSEDED").status == "SUPERSEDED"


def _main(capsys, state, *args):
    code = route.main(["--state", str(state), *args])
    return code, json.loads(capsys.readouterr().out)


def test_the_refusal_is_visible_through_the_route_cli(project, tmp_path, monkeypatch, capsys):  # noqa: F811
    _routable(project, tmp_path)
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: SWEEP_UNAVAILABLE)
    code, out = _main(capsys, tmp_path, "recover")
    assert code == 4 and out["sweep_unavailable"] is True
    task_id = out["decisions"][0]["task_id"]
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["unswept"] == {
        "attempt_id": out["decisions"][0]["attempt_id"],
        "last_refusal": "",
    }
    _sweeps(monkeypatch, SWEEP_UNAVAILABLE)
    code, refused = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 2 and refused["error"] == "TAKEOVER_REFUSED"
    assert "SWEEP_UNAVAILABLE" in refused["detail"]
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["status"] == "FAILED" and "SWEEP_UNAVAILABLE" in status["unswept"]["last_refusal"]
    _sweeps(monkeypatch, 0)
    code, done = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 0 and done["status"] == "SUPERSEDED"
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["unswept"] is None
    code, bad = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 2 and bad["error"] == "NOT_SUPERSEDABLE"
