"""#244 window 1: a takeover (supersede, then a new key) is refused while the FAILED attempt's
processes are not shown gone (takeover.py). The sweep's answers are faked here; the real sweep
below that interface is in test_sweep_result.py, the ledger fence in test_takeover_fence.py,
and a real surviving child in test_recovery_windows.py."""

import json
import re
from pathlib import Path

import pytest
from test_pipeline import VERIFY, EditWorker
from test_recovery import crash, settle, stores
from test_recovery import project as project  # noqa: F401 - fixture
from test_recovery_leftovers import _routable

import cairnwork.confined as confined
import cairnwork.recovery as recovery
from cairnwork import route, takeover, unswept
from cairnwork.confined import UNLISTED, Sweep
from cairnwork.ownership import OWNER, DuplicateLogicalTask, OwnershipLedger, TakeoverRefused
from cairnwork.pipeline import TaskSpec, run_task

GONE = Sweep(listed=True)
DOC = Path(__file__).parent.parent / "docs" / "GATEWAY_ROUTE.md"


def _failed_task(tmp_path, *, unswept_attempt: str | None = "a1", account=""):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    fence = None if unswept_attempt is None else (unswept_attempt, account)
    ledger.set_status(task_id, "FAILED", fence=fence)
    return ledger, task_id


def _sweeps(monkeypatch, *answers):
    """takeover.sweep_marked answers in turn (the last one repeats); returns the calls."""
    calls = []

    def fake(var, value, **kw):
        calls.append((var, value, kw.get("account")))
        return answers[min(len(calls), len(answers)) - 1]

    monkeypatch.setattr(takeover, "sweep_marked", fake)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    return calls


def test_an_unavailable_sweep_refuses_and_the_new_attempt_never_starts(
    project, tmp_path, monkeypatch
):  # noqa: F811
    ledger, task_id = _failed_task(tmp_path)
    _sweeps(monkeypatch, UNLISTED)
    with pytest.raises(TakeoverRefused) as refused:
        takeover.supersede(ledger, task_id)
    assert refused.value.code == "TAKEOVER_REFUSED" and "SWEEP_UNAVAILABLE" in str(refused.value)
    assert ledger.get(task_id=task_id).status == "FAILED"
    held = ledger.unswept_attempt(task_id)
    assert held is not None and held.attempt_id == "a1" and "SWEEP_UNAVAILABLE" in held.refusal
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
    ledger, task_id = _failed_task(tmp_path)
    calls = _sweeps(monkeypatch, Sweep(True, killed=2), Sweep(True, killed=1), GONE)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert len(calls) == 3 and {c[0] for c in calls} == {"CAIRNWORK_ATTEMPT"}
    assert {c[1] for c in calls} == {"a1"}  # the same tag recovery sweeps
    assert {c[2] for c in calls} == {None}  # the attempt ran as the owner
    assert ledger.unswept_attempt(task_id) is None


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (Sweep(True, killed=1), "SURVIVORS_REMAIN"),  # every look still lists (and kills) one
        (Sweep(True, denied=1), "SURVIVORS_REMAIN"),  # the kill is refused
        (Sweep(True, unreadable=1), "UNREADABLE_PROCESSES"),  # one may carry the marker
        (Sweep(True, account=1), "WORKER_ACCOUNT_BUSY"),
    ],
)
def test_processes_not_shown_gone_refuse_the_takeover(tmp_path, monkeypatch, answer, code):
    ledger, task_id = _failed_task(tmp_path)
    calls = _sweeps(monkeypatch, answer)
    with pytest.raises(TakeoverRefused, match=code):
        takeover.supersede(ledger, task_id)
    assert len(calls) == 1 + confined.CONFIRM_TRIES  # looked again for the whole window
    assert ledger.get(task_id=task_id).status == "FAILED"
    assert (ledger.unswept_attempt(task_id) or unswept.Mark("", "", "")).refusal.startswith(code)


def test_a_sweep_that_runs_and_finds_none_allows_the_takeover(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path)
    calls = _sweeps(monkeypatch, GONE)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert len(calls) == 1 and ledger.unswept_attempt(task_id) is None


def test_a_checked_attempt_supersedes_as_before_without_sweeping(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, unswept_attempt=None)
    calls = _sweeps(monkeypatch, UNLISTED)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert not calls  # nothing to check, nothing refused


def test_a_worker_account_mark_is_swept_with_that_account(tmp_path, monkeypatch):
    ledger, task_id = _failed_task(tmp_path, account="cw-worker")
    calls = _sweeps(monkeypatch, Sweep(True, account=2), GONE)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert [c[2] for c in calls] == ["cw-worker", "cw-worker"]


def test_a_re_mark_names_the_newest_attempt(tmp_path, monkeypatch):
    """#244 NIT: a second mark of one task replaces the attempt (and account), and clears the
    old refusal: the takeover must sweep the attempt the FAILED status is about."""
    ledger, task_id = _failed_task(tmp_path)
    ledger.note_refusal(task_id, "SWEEP_UNAVAILABLE: old")
    ledger._on_conn(unswept.mark, task_id, "a2", "cw-worker")
    assert ledger.unswept_attempt(task_id) == unswept.Mark("a2", "cw-worker", "")
    calls = _sweeps(monkeypatch, GONE)
    takeover.supersede(ledger, task_id)
    assert [(c[1], c[2]) for c in calls] == [("a2", "cw-worker")]


def test_a_raw_refusal_leaves_the_last_refusal_to_the_takeover(tmp_path):
    ledger, task_id = _failed_task(tmp_path)
    with pytest.raises(TakeoverRefused, match="UNSWEPT_ATTEMPT"):
        ledger.set_status(task_id, "SUPERSEDED")
    assert ledger.unswept_attempt(task_id) == unswept.Mark("a1", "", "")


def test_recovery_marks_only_an_attempt_not_shown_gone(project, tmp_path, monkeypatch):  # noqa: F811
    crash(project, tmp_path, "CANDIDATE")
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
    (decision,), ledger = settle(tmp_path)
    held = ledger.unswept_attempt(decision.task_id)
    assert held is not None and held.attempt_id == decision.attempt_id
    with pytest.raises(TakeoverRefused):
        ledger.set_status(decision.task_id, "SUPERSEDED")
    other = tmp_path / "clean"
    other.mkdir()
    crash(project, other, "CANDIDATE")
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: GONE)
    (clean,), clean_ledger = settle(other)
    assert clean_ledger.unswept_attempt(clean.task_id) is None
    assert clean_ledger.set_status(clean.task_id, "SUPERSEDED").status == "SUPERSEDED"


def _main(capsys, state, *args):
    code = route.main(["--state", str(state), *args])
    return code, json.loads(capsys.readouterr().out)


def _documented_codes():
    """The reason codes docs/GATEWAY_ROUTE.md promises for a TAKEOVER_REFUSED detail."""
    row = next(line for line in DOC.read_text().splitlines() if "`supersede --task-id" in line)
    return set(re.findall(r"`([A-Z_]+)`", row.split("原因代碼：", 1)[1].split("。", 1)[0]))


def test_the_refusal_is_visible_through_the_route_cli(project, tmp_path, monkeypatch, capsys):  # noqa: F811
    _routable(project, tmp_path)
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
    code, out = _main(capsys, tmp_path, "recover")
    assert code == 4 and out["sweep_unavailable"] is True
    task_id = out["decisions"][0]["task_id"]
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["unswept"] == {
        "attempt_id": out["decisions"][0]["attempt_id"],
        "last_refusal": "",
    }
    _sweeps(monkeypatch, UNLISTED)
    code, refused = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 2 and refused["error"] == "TAKEOVER_REFUSED"
    # the Gateway's contract (docs/GATEWAY_ROUTE.md): "TAKEOVER_REFUSED: <task id>: <CODE>: ..."
    shape = re.fullmatch(rf"TAKEOVER_REFUSED: {task_id}: ([A-Z_]+): .*", refused["detail"])
    assert shape is not None and shape.group(1) == "SWEEP_UNAVAILABLE"
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["status"] == "FAILED"
    assert status["unswept"]["last_refusal"].startswith("SWEEP_UNAVAILABLE")
    _sweeps(monkeypatch, GONE)
    code, done = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 0 and done["status"] == "SUPERSEDED"
    code, status = _main(capsys, tmp_path, "status", "--task-id", task_id)
    assert status["unswept"] is None
    code, bad = _main(capsys, tmp_path, "supersede", "--task-id", task_id)
    assert code == 2 and bad["error"] == "NOT_SUPERSEDABLE"


def test_the_documented_reason_codes_are_the_ones_produced():
    produced = {
        Sweep(listed=False).problem().split(":")[0],
        Sweep(True, killed=1).problem().split(":")[0],
        Sweep(True, unreadable=1).problem().split(":")[0],
        Sweep(True, account=1).problem().split(":")[0],
        "UNSWEPT_ATTEMPT",  # a raw SUPERSEDED (ownership.py)
        "LEGACY_ATTEMPT_UNKNOWN",  # a LEGACY mark without the journal (takeover.py)
    }
    assert _documented_codes() == produced
    assert Sweep(listed=True).problem() == ""
