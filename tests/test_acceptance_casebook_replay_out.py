"""Additional replay output and input refusal regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_acceptance_casebook_replay import _args, _book, _confirmed

from experimental.acceptance_casebook import replay

pytest_plugins = ["test_acceptance_casebook_origin"]


@pytest.mark.parametrize(
    "target", ["head_tmp_hard_book", "head_tmp_symlink", "lock_symlink", "folded_wal"]
)
def test_out_derived_paths_cannot_overwrite_inputs(monkeypatch, tmp_path, lg, capsys, target):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    book = _book(lg)
    book_head = book.with_name(book.name + ".head")
    out = tmp_path / "safe-output"
    if target == "head_tmp_hard_book":
        out.with_name(out.name + ".head.tmp").hardlink_to(book)
    elif target == "head_tmp_symlink":
        out.with_name(out.name + ".head.tmp").symlink_to(book)
    elif target == "lock_symlink":
        lock = book.with_name(book.name + ".lock")
        lock.touch()
        out.with_name(out.name + ".lock").symlink_to(lock)
    else:
        db = Path(lg["db"])
        out = db.with_name(db.name.upper() + "-WAL")
    args[args.index("--out") + 1] = str(out)
    before = (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        book_head.read_bytes(),
        book_head.stat().st_mtime_ns,
    )
    monkeypatch.setattr(replay, "RUNNER", lambda *_: "accepted")
    assert replay.main(args) == 2
    assert capsys.readouterr().out.strip() == "REPLAY_OUTPUT_REFUSED"
    assert before == (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        book_head.read_bytes(),
        book_head.stat().st_mtime_ns,
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


@pytest.mark.parametrize("key", ["allowed_paths", "required_verifier_ids"])
def test_request_missing_binding_is_rejected(monkeypatch, tmp_path, lg, capsys, key):
    from dataclasses import replace

    from experimental.acceptance_casebook import replay as module

    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    real = module.read_origin

    def request_without(*values):
        rec, origin = real(*values)
        request = dict(rec.request)
        if key == "allowed_paths":
            request["acceptance_contract"] = dict(request["acceptance_contract"])
            request["acceptance_contract"].pop("allowed_paths", None)
        else:
            request["verification_plan"] = dict(request["verification_plan"])
            request["verification_plan"].pop("required_verifier_ids", None)
            request["acceptance_contract"].pop("required_verifier_ids", None)
            request.pop("required_verifier_ids", None)
        return replace(rec, request=request), origin

    monkeypatch.setattr(module, "read_origin", request_without)
    calls = []
    monkeypatch.setattr(module, "RUNNER", lambda *_: calls.append(True) or "accepted")
    assert module.main(args) == 3
    rows = [json.loads(x) for x in Path(args[args.index("--out") + 1]).read_text().splitlines()]
    assert rows[0]["reason"] == "REPLAY_INPUT_MISMATCH" and not calls


def test_request_binding_must_be_list(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    real = replay.read_origin

    def malformed(*values):
        from dataclasses import replace

        rec, origin = real(*values)
        request = dict(rec.request)
        request["acceptance_contract"] = dict(request["acceptance_contract"])
        request["acceptance_contract"]["allowed_paths"] = "abc"
        return replace(rec, request=request), origin

    monkeypatch.setattr(replay, "read_origin", malformed)
    monkeypatch.setattr(replay, "RUNNER", lambda *_: "accepted")
    assert replay.main(args) == 3
    row = json.loads(Path(args[args.index("--out") + 1]).read_text().splitlines()[0])
    assert row["reason"] == "REPLAY_INPUT_MISMATCH"


def test_runner_mutation_does_not_change_recorded_kind_or_result(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    monkeypatch.setattr(
        replay, "RUNNER", lambda payload, _: payload.update(kind="refused_good") or "accepted"
    )
    assert replay.main(args) == 0
    row = json.loads(Path(args[args.index("--out") + 1]).read_text().splitlines()[0])
    assert row["kind"] == "accepted_bad" and row["outcome"] == "same_mistake"


def test_runner_cannot_swap_output_to_casebook(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    book = _book(lg)
    head = book.with_name(book.name + ".head")
    before = (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        head.read_bytes(),
        head.stat().st_mtime_ns,
    )
    out = Path(args[args.index("--out") + 1])

    def swap(*_):
        out.symlink_to(book)
        out.with_name(out.name + ".head").symlink_to(head)
        return "accepted"

    monkeypatch.setattr(replay, "RUNNER", swap)
    assert replay.main(args) == 2
    assert capsys.readouterr().out.strip() == "REPLAY_OUTPUT_REFUSED"
    assert before == (
        book.read_bytes(),
        book.stat().st_mtime_ns,
        head.read_bytes(),
        head.stat().st_mtime_ns,
    )


def test_unknown_kind_is_not_run(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    case["kind"] = "mystery"
    monkeypatch.setattr(replay, "list_cases", lambda *_: [case])
    monkeypatch.setattr(replay, "_confirmation", lambda *_: "f" * 64)
    calls = []
    monkeypatch.setattr(replay, "RUNNER", lambda *_: calls.append(True) or "accepted")
    args = _args(tmp_path, lg, [case])
    assert replay.main(args) == 3 and not calls
    row = json.loads(Path(args[args.index("--out") + 1]).read_text().splitlines()[0])
    assert row["outcome"] == "UNREPLAYABLE" and row["reason"] == "INVALID_KIND"
