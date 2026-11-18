"""Adversarial replay regressions from the independent review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_acceptance_casebook_replay import _args, _book, _confirmed

from experimental.acceptance_casebook import replay
from experimental.acceptance_casebook.store import _read_chain

pytest_plugins = ["test_acceptance_casebook_origin"]


def _execute(monkeypatch, tmp_path, lg, cases, result):
    args = _args(tmp_path, lg, cases)
    monkeypatch.setattr(replay, "RUNNER", result)
    rc = replay.main(args)
    out = Path(args[args.index("--out") + 1])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    return rc, args, rows


@pytest.mark.parametrize(
    "result",
    [
        {"exit_code": 0, "verdict": "REFUSED"},
        {"exit_code": 2, "verdict": "UNCOMPARABLE"},
        TimeoutError(),
        RuntimeError(),
        *[
            {"exit_code": code, "verdict": verdict}
            for code in ("x", [1], float("nan"), False, None)
            for verdict in ("VERIFIED", "FAILED", "REFUSED")
        ],
        None,
        ["accepted"],
        "VERIFIED",
    ],
)
def test_bad_runner_family_isolated_and_summarized(monkeypatch, tmp_path, lg, capsys, result):
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    calls = []

    def runner(case, _args):
        calls.append(case["line_sha256"])
        if case["attempt_id"] == "a1" and isinstance(result, BaseException):
            raise type(result)()
        return result if case["attempt_id"] == "a1" else "refused"

    rc, _, rows = _execute(monkeypatch, tmp_path, lg, cases, runner)
    results = rows[:-1]
    assert rc == 3 and rows[-1]["complete"] is False
    assert results[0]["outcome"] == "UNREPLAYABLE"
    assert results[0]["reason"]
    assert results[0]["outcome"] not in ("corrected", "same_mistake")
    assert results[1]["outcome"] == "same_mistake"
    assert calls.count(cases[1]["line_sha256"]) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(allowed_file=[1, "a"]),
        lambda row: row.update(allowed_file=5),
        lambda row: row.update(allowed_file="a"),
        lambda row: row.update(allowed_file=[{"a": 1}]),
        lambda row: row.update(allowed_files=row["allowed_file"], allowed_file=["bad"]),
        lambda row: row.update(state="/real/operator/state"),
        lambda row: row.update(timeout=3),
        lambda row: row.update(base="deadbeef"),
        lambda row: row.update(repo_path="/operator/repository"),
        lambda row: row.pop("allowed_file"),
    ],
)
def test_bad_input_family_isolated(monkeypatch, tmp_path, lg, capsys, mutation):
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _args(tmp_path, lg, cases)
    inp = Path(args[args.index("--inputs") + 1])
    data = json.loads(inp.read_text())
    mutation(data[cases[0]["line_sha256"]])
    inp.write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr(
        replay, "RUNNER", lambda case, _: calls.append(case["line_sha256"]) or "accepted"
    )
    assert replay.main(args) == 3
    rows = [json.loads(x) for x in Path(args[args.index("--out") + 1]).read_text().splitlines()]
    bad = next(r for r in rows if r.get("case") == cases[0]["line_sha256"])
    assert bad["outcome"] == "UNREPLAYABLE" and bad["reason"] in (
        "REPLAY_INPUT_MISMATCH",
        "REPLAY_INPUT_INVALID",
    )
    assert cases[0]["line_sha256"] not in calls
    assert calls.count(cases[1]["line_sha256"]) == 2


@pytest.mark.parametrize("version_case", ["branch", "head", "uppercase", "unknown", "tree"])
def test_ac4b_versions_refuse_before_running(monkeypatch, tmp_path, lg, capsys, version_case):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    out = Path(args[args.index("--out") + 1])
    original = Path(args[args.index("--versions") + 1])
    versions = json.loads(original.read_text())
    version = args[args.index("--version") + 1]
    if version_case in ("branch", "head"):
        args[args.index("--version") + 1] = "HEAD" if version_case == "head" else "main"
    elif version_case == "uppercase":
        args[args.index("--version") + 1] = version.upper()
    elif version_case == "unknown":
        args[args.index("--version") + 1] = "b" * 40
    else:
        versions[version]["tree"] = "f" * 40
        original.write_text(json.dumps(versions))
    called = []
    monkeypatch.setattr(replay, "RUNNER", lambda *a: called.append(a) or "accepted")
    assert replay.main(args) == 2 and not called
    assert not out.exists() or out.read_bytes() == b""


@pytest.mark.parametrize(
    "target",
    [
        "book",
        "head",
        "db",
        "symlink",
        "hard_book",
        "head_hard_book",
        "hard_db",
        "head_symlink",
        "lock_hard_book",
        "wal",
        "shm",
        "journal",
    ],
)
def test_out_cannot_overwrite_inputs(monkeypatch, tmp_path, lg, capsys, target):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    book = _book(lg)
    out = {"book": book, "head": book.with_name(book.name + ".head"), "db": Path(lg["db"])}.get(
        target
    )
    book_head = book.with_name(book.name + ".head")
    if target == "symlink":
        out = tmp_path / "book-link"
        out.symlink_to(book)
    elif target == "hard_book":
        out = tmp_path / "book-hardlink"
        out.hardlink_to(book)
    elif target == "head_hard_book":
        out = tmp_path / "fresh-output"
        out.with_name(out.name + ".head").hardlink_to(book_head)
    elif target == "hard_db":
        out = tmp_path / "db-hardlink"
        out.hardlink_to(Path(lg["db"]))
    elif target == "head_symlink":
        out = tmp_path / "fresh-output"
        out.with_name(out.name + ".head").symlink_to(book_head)
    elif target == "lock_hard_book":
        book_lock = book.with_name(book.name + ".lock")
        book_lock.touch()
        out = tmp_path / "fresh-lock-output"
        out.with_name(out.name + ".lock").hardlink_to(book_lock)
    elif target in ("wal", "shm", "journal"):
        sidecar = Path(lg["db"]).with_name(Path(lg["db"]).name + "-" + target)
        sidecar.touch(exist_ok=True)
        out = tmp_path / f"db-{target}-hardlink"
        out.hardlink_to(sidecar)
    idx = args.index("--out") + 1
    args[idx] = str(out)
    before = (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        book_head.read_bytes(),
        book_head.stat().st_mtime_ns,
    )
    calls = []
    monkeypatch.setattr(replay, "RUNNER", lambda *a: calls.append(a) or "accepted")
    assert replay.main(args) == 2
    assert capsys.readouterr().out.strip() == "REPLAY_OUTPUT_REFUSED"
    assert not calls
    assert before == (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        book_head.read_bytes(),
        book_head.stat().st_mtime_ns,
    )


def test_case_recorded_commits_are_passed_to_runner(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    received = []
    monkeypatch.setattr(
        replay, "RUNNER", lambda payload, _: received.append(payload.copy()) or "accepted"
    )
    assert replay.main(args) == 0
    assert len(received) == 2
    assert all(row["candidate"] == case["candidate"] for row in received)
    assert all(row["base"] == case["base"] for row in received)


@pytest.mark.parametrize("key", ["candidate", "base"])
@pytest.mark.parametrize("value", ["A" * 40, "a" * 39, "--upload-pack=x", 7])
def test_invalid_recorded_candidate_is_rejected_before_runner(
    monkeypatch, tmp_path, lg, capsys, key, value
):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])  # a valid inputs row: only the recorded commit is bad
    case[key] = value
    monkeypatch.setattr(replay, "list_cases", lambda *_: [case])
    monkeypatch.setattr(replay, "_confirmation", lambda *_: "f" * 64)
    calls = []
    monkeypatch.setattr(replay, "RUNNER", lambda *_: calls.append(True) or "accepted")
    assert replay.main(args) == 3
    result = json.loads(Path(args[args.index("--out") + 1]).read_text().splitlines()[0])
    assert result["outcome"] == "UNREPLAYABLE"
    assert result["reason"] == "REPLAY_INPUT_MISMATCH"
    assert not calls


def test_confirmation_is_the_counted_chain_line_after_a_non_counting_one(
    monkeypatch, tmp_path, lg, capsys
):
    from experimental.acceptance_casebook.identity import confirmation_problem
    from experimental.acceptance_casebook.store import ZERO, _append

    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    book = _book(lg)
    (first, _), (good, _) = _read_chain(book)[0]
    bad = {**good, "confirmer": case["adder"]}  # same lineage as the adder: does not count
    assert confirmation_problem(case, bad) is not None
    book.unlink()
    book.with_name(book.name + ".head").unlink()
    prev = ZERO
    for entry in (first, bad, good):  # rebuild the chain with the bad line first
        prev = _append(book, {k: v for k, v in entry.items() if k != "prev"}, prev)
    entries = _read_chain(book)[0]
    assert entries[0][1] == case["line_sha256"]
    args = _args(tmp_path, lg, [case])
    monkeypatch.setattr(replay, "RUNNER", lambda *_: "accepted")
    assert replay.main(args) == 0
    result = json.loads(Path(args[args.index("--out") + 1]).read_text().splitlines()[0])
    assert result["confirmation"] == entries[2][1]
    assert (
        result["inputs_sha256"]
        == hashlib.sha256(Path(args[args.index("--inputs") + 1]).read_bytes()).hexdigest()
    )


def test_missing_confirmation_is_found_before_runner(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    called = []

    def missing(*_args):
        raise replay.CasebookError("CONFIRMATION_MISSING")

    monkeypatch.setattr(replay, "_confirmation", missing)
    monkeypatch.setattr(replay, "RUNNER", lambda *_: called.append(True) or "accepted")
    assert replay.main(args) == 3 and not called


def test_surviving_mutant_semantics(monkeypatch):
    monkeypatch.setattr(replay, "RUNNER", lambda *_: {"exit_code": 0, "verdict": "REFUSED"})
    assert replay._run_one({"kind": "accepted_bad"}, {}, 0)[0] == "UNREPLAYABLE"
    monkeypatch.setattr(replay, "RUNNER", lambda *_: (_ for _ in ()).throw(TimeoutError()))
    assert replay._run_one({"kind": "accepted_bad"}, {}, 0)[1] == "TIMEOUT"
