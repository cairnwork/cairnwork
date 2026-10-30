"""#466: the intake stores the request durably before it binds the task, and a missing requests/
is damaged state on every pass, never an empty directory again."""
# ruff: noqa: F811

import errno
import json
import os
import shutil
import sqlite3

import pytest
from test_issue_runner import FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.ownership import OwnershipLedger

REPO = "James3014/demo"
PAYLOAD = {  # the shape the issue runner submits: strings, lists, escapes, a non-ASCII title
    "allowed_files": ["src/calc.py"],
    "base_commit": "a" * 40,
    "card_hash": "gh:James3014/demo#7:" + "b" * 64,
    "idempotency_key": "gh:James3014/demo#7:" + "b" * 16,
    "instructions": 'line one\nsay "hi" \\ back',
    "repo": "/srv/clone",
    "title": "fix é",
    "verifiers": [["pytest-0", ["python3", "-m", "pytest", "-q", "tests/test_calc.py"]]],
}


def _rows(intake: Intake) -> list[tuple[str, str]]:
    with sqlite3.connect(intake.ledger.path) as conn:
        return conn.execute("SELECT task_id, status FROM task_ownership").fetchall()


def _requests(intake: Intake) -> list[str]:
    return sorted(p.name for p in (intake.state / "requests").iterdir())


def _fail_store(monkeypatch, where: str) -> list[bool]:
    """Fail the request store once (ENOSPC) while ``fail[0]``: at its open or at its fsync."""
    fail, real_open, real_fsync = [True], os.open, os.fsync

    def opener(path, flags, *args, **kwargs):
        if fail[0] and os.fspath(path).endswith(".json") and flags & os.O_EXCL:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, flags, *args, **kwargs)

    def fsync(fd):
        if fail[0]:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_fsync(fd)

    monkeypatch.setattr(
        os, "open" if where == "open" else "fsync", opener if where == "open" else fsync
    )
    return fail


@pytest.mark.parametrize("where", ["open", "fsync"])
def test_a_failed_store_binds_nothing_and_the_next_pass_takes_the_issue(setup, monkeypatch, where):
    fail = _fail_store(monkeypatch, where)
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])
    assert first[0]["error"].startswith("OSError") and github.labels[7] == {runner.QUEUE}
    assert _rows(setup["intake"]) == [] and _requests(setup["intake"]) == []  # no orphan either
    assert setup["started"] == [] and github.comments == []
    fail[0] = False
    second = runner.take(setup["intake"], github, setup["clone"])  # same key, no human needed
    assert second[0]["duplicate"] is False and len(setup["started"]) == 1
    assert github.labels[7] == {runner.RUNNING}
    assert _rows(setup["intake"]) == [(second[0]["task_id"], "BOUND")]
    assert _requests(setup["intake"]) == [f"{second[0]['task_id']}.json"]


def test_the_request_is_whole_and_synced_on_disk_before_the_bind(setup, monkeypatch):
    """A crash right after the bind commits (here: an interrupt raised after the real bind) still
    leaves a bound task whose request loads, so recovery's FAILED settles as a plain failure."""
    synced, real_fsync, real_bind = [], os.fsync, OwnershipLedger.bind
    seen = {}

    def fsync(fd):
        info = os.fstat(fd)
        synced.append((info.st_ino, info.st_size))  # the size the sync made durable
        return real_fsync(fd)

    def bind(self, key, request, owner, card_hash, *args, **kwargs):
        requests = self.path.parent / "requests"
        (name,) = os.listdir(requests)  # exactly one: the request about to be bound
        seen["name"], seen["text"] = name, (requests / name).read_text()
        inodes = {ino for ino, _ in synced}
        whole = (os.stat(requests / name).st_ino, len(seen["text"].encode())) in synced
        seen["synced"] = whole and os.stat(requests).st_ino in inodes  # file flushed, then synced
        record = real_bind(self, key, request, owner, card_hash, *args, **kwargs).record
        seen["task_id"] = record.task_id
        raise KeyboardInterrupt  # the process dies after the commit, before the runner starts

    github = FakeGitHub([issue()])
    with monkeypatch.context() as patch, pytest.raises(KeyboardInterrupt):
        patch.setattr(os, "fsync", fsync)
        patch.setattr(OwnershipLedger, "bind", bind)
        runner.take(setup["intake"], github, setup["clone"])
    task_id = seen["task_id"]
    assert seen["name"] == f"{task_id}.json" and seen["synced"]
    assert json.loads(seen["text"])["idempotency_key"].startswith(f"gh:{REPO}#7:")
    assert _rows(setup["intake"]) == [(task_id, "BOUND")] and setup["started"] == []
    assert setup["intake"].load_request(task_id).title == "fix x"
    setup["intake"].ledger.set_status(task_id, "FAILED")  # what recovery decides for it
    again = runner.take(setup["intake"], github, setup["clone"])  # adopted, not needs-fix
    assert again == [{"issue": 7, "task_id": task_id, "already": True}]
    settled = runner.settle(setup["intake"], github, setup["clone"])
    assert settled == [{"issue": 7, "status": "FAILED"}] and github.labels[7] == {
        runner.FAILED_LABEL
    }


def test_a_resend_or_a_refused_bind_leaves_no_request_behind(setup):
    intake = setup["intake"]
    github = FakeGitHub([issue()])
    (taken,) = runner.take(intake, github, setup["clone"])
    stored = intake.state / "requests" / f"{taken['task_id']}.json"
    payload = json.loads(stored.read_text())
    assert intake.submit(payload)["duplicate"] is True  # the same key: the bound task
    intake.ledger.set_status(taken["task_id"], "FAILED")
    other = {**payload, "idempotency_key": "other-key", "card_hash": "other-card"}
    with pytest.raises(Exception, match="REPLAY_FORBIDDEN"):
        intake.submit(other)  # the same request under another key: refused at the bind
    assert _requests(intake) == [stored.name] and stored.read_text() == json.dumps(
        payload, sort_keys=True
    )
    assert len(_rows(intake)) == 1


def _no_requests(requests) -> None:
    shutil.rmtree(requests)


def _symlinked_requests(requests) -> None:
    requests.rename(requests.with_name("requests-real"))
    requests.symlink_to(requests.with_name("requests-real"))


@pytest.mark.parametrize("damage", [_no_requests, _symlinked_requests])
def test_a_damaged_requests_dir_stays_bad_state_on_every_pass(setup, capsys, damage):
    """Once the ledger exists, requests/ is never recreated empty: prepare() refuses BAD_STATE and
    run-once exits 3 on this pass and the next, touching no Issue."""
    intake = setup["intake"]
    github = FakeGitHub([issue()])
    runner.take(intake, github, setup["clone"])
    damage(intake.state / "requests")
    argv = ["--state", str(intake.state), "--repo", REPO, "--clone", str(setup["clone"])]
    for _ in range(2):
        with pytest.raises(IntakeRefused) as refused:
            intake.prepare()
        assert refused.value.code == "BAD_STATE"
        assert runner.main([*argv, "--gh", "/bin/false", "run-once"]) == 3
        assert json.loads(capsys.readouterr().out)["error"] == "BAD_STATE"
        assert not (intake.state / "requests").is_dir() or (intake.state / "requests").is_symlink()


def test_a_state_with_no_bound_task_still_gets_its_requests_dir(tmp_path):
    """Nothing bound, nothing lost: no ledger, or an empty one (a status query made it), and
    prepare() creates requests/ as before."""
    intake = Intake(tmp_path / "state")
    intake.prepare()
    assert (intake.state / "requests").is_dir()
    shutil.rmtree(intake.state / "requests")
    intake.ledger.in_flight()  # the ledger file now exists, with no task in it
    intake.check_state()
    intake.prepare()
    assert (intake.state / "requests").is_dir() and not (intake.state / "requests").is_symlink()


def test_read_request_names_only_the_request_s_own_failures(tmp_path):
    from cairnwork.intake_request import read_request
    from cairnwork.issue_task import RequestUnreadable

    requests = tmp_path / "requests"
    requests.mkdir()
    (requests / "whole.json").write_text('{"a": 1}')
    assert read_request(requests, "whole") == {"a": 1}
    (requests / "empty.json").write_text("")
    (requests / "cut.json").write_text('{"a": ')
    for task_id in ("absent", "empty", "cut"):
        with pytest.raises(RequestUnreadable, match=f"^{task_id}: "):
            read_request(requests, task_id)
    os.mkfifo(requests / "fifo.json")  # O_NONBLOCK: refused at once, never waited on
    (requests / "dir.json").mkdir()
    (requests / "link.json").symlink_to(requests / "whole.json")
    (requests / "byte.json").write_bytes(b'{"a": "\xff"}')
    (requests / "utf8.json").write_bytes('{"a": "é"}'.encode())  # valid UTF-8, still not ASCII
    for task_id, error in (
        ("fifo", OSError),
        ("dir", OSError),
        ("link", OSError),
        ("byte", UnicodeDecodeError),
        ("utf8", UnicodeDecodeError),
    ):
        with pytest.raises(error) as raised:
            read_request(requests, task_id)
        assert not isinstance(raised.value, RequestUnreadable)
    for bad in (b"not json", b'{"a":???', b'{"a": 1}}', b'{"a": "\\uZZZZ"}', b'{"a": tr'):
        (requests / "bad.json").write_bytes(bad)  # complete but malformed: damage, not a cut
        with pytest.raises(json.JSONDecodeError) as raised:
            read_request(requests, "bad")
        assert not isinstance(raised.value, RequestUnreadable)
    text = json.dumps(PAYLOAD, sort_keys=True)  # what store_request writes, cut at every byte
    for end in range(len(text)):
        (requests / "cut.json").write_text(text[:end])
        with pytest.raises(RequestUnreadable):
            read_request(requests, "cut")
    with pytest.raises(FileNotFoundError):
        read_request(tmp_path / "gone", "whole")  # requests/ itself missing: not the request's
    (tmp_path / "linked").symlink_to(requests)
    with pytest.raises(OSError):
        read_request(tmp_path / "linked", "whole")  # requests/ a symlink: never followed


def test_store_request_never_replaces_or_follows_and_cleans_up_on_failure(tmp_path, monkeypatch):
    from cairnwork.intake_request import store_request

    requests = tmp_path / "requests"
    requests.mkdir()
    store_request(requests, "task-1", {"title": "é"})
    assert (requests / "task-1.json").read_bytes() == b'{"title": "\\u00e9"}'  # ASCII only
    with pytest.raises(FileExistsError):
        store_request(requests, "task-1", {"title": "other"})  # O_EXCL: never replaced
    (requests / "task-2.json").symlink_to(tmp_path / "target.json")
    with pytest.raises(FileExistsError):
        store_request(requests, "task-2", {})  # a symlink at the name is not written through
    assert not (tmp_path / "target.json").exists()
    real = os.fsync
    calls = []

    def fsync(fd):
        calls.append(fd)
        if len(calls) == 2:  # the file synced, requests/ not: still not durable, so not kept
            raise OSError(errno.EIO, "Input/output error")
        return real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="Input/output"):
        store_request(requests, "task-3", {"a": 1})
    assert not (requests / "task-3.json").exists() and len(calls) == 2


def test_a_ledger_that_cannot_be_read_counts_as_naming_tasks(tmp_path):
    intake = Intake(tmp_path / "state")
    intake.prepare()
    shutil.rmtree(intake.state / "requests")
    (intake.state / "ownership.sqlite").write_bytes(b"not a database")
    with pytest.raises(IntakeRefused, match="BAD_STATE"):
        intake.check_state()  # fail closed: it may name tasks whose requests are gone


def test_a_bind_that_fails_after_its_commit_keeps_the_request(setup, monkeypatch):
    """Only an OwnershipError is known to come before the commit; any other bind failure (here an
    OperationalError after the real commit) leaves the request for the row it may have bound."""
    real_bind = OwnershipLedger.bind

    def bind(self, *args, **kwargs):
        real_bind(self, *args, **kwargs)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(OwnershipLedger, "bind", bind)
    github = FakeGitHub([issue()])
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out[0]["error"].startswith("OperationalError") and github.labels[7] == {runner.QUEUE}
    ((task_id, status),) = _rows(setup["intake"])
    assert status == "BOUND" and setup["started"] == []
    assert setup["intake"].load_request(task_id).title == "fix x"


def test_a_ledger_of_finished_tasks_still_needs_its_requests_dir(setup):
    """A FAILED (or COMPLETED) task still has its request read later (adopt, settle): any task in
    the ledger, not only a live one, makes a missing requests/ BAD_STATE."""
    intake = setup["intake"]
    (taken,) = runner.take(intake, FakeGitHub([issue()]), setup["clone"])
    intake.ledger.set_status(taken["task_id"], "FAILED")
    shutil.rmtree(intake.state / "requests")
    with pytest.raises(IntakeRefused, match="BAD_STATE"):
        intake.prepare()
    assert not (intake.state / "requests").exists()


def test_a_cut_unicode_escape_is_a_cut_whichever_position_the_decoder_names():
    """CPython builds differ on where an "Invalid \\uXXXX escape" error points (the backslash on
    Ubuntu's 3.12.3, the "u" elsewhere): the rule must hold for both."""
    from cairnwork.intake_request import _cut_short

    def cut(text: str, pos: int) -> bool:
        return _cut_short(text, json.JSONDecodeError("Invalid \\uXXXX escape", text, pos))

    for text in ('"ab\\u', '"ab\\u0', '"ab\\u00e'):
        assert cut(text, 3) and cut(text, 4)  # the backslash, or the "u"
    for text in ('"ab\\uZZZZ"', '"ab\\u0Z', '"ab\\u00e9zz\\uZ"'):
        assert not cut(text, 3) and not cut(text, 4)  # bad hex before the end: damage
