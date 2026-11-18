"""Additional replay output and input refusal regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_acceptance_casebook_replay import _args, _book, _confirmed

from experimental.acceptance_casebook import replay, replay_out
from experimental.acceptance_casebook.store import CasebookError

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

    inp = Path(args[args.index("--inputs") + 1])
    data = json.loads(inp.read_text())
    data[case["line_sha256"]]["allowed_file"] = ["a", "b", "c"]  # sorted("abc") would match
    inp.write_text(json.dumps(data))
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


@pytest.mark.parametrize(
    "target",
    [
        "out_hard",
        "out_symlink_unprotected",
        "stale_head_tmp",
        "lock_symlink",
        "folded",
    ],
)
def test_writer_checks_each_opened_file(tmp_path, target):
    """Calls the writer directly, so the pre-runner path check cannot stand in for it."""
    book = tmp_path / "book.jsonl"
    book.write_bytes(b"")  # an empty chain reads cleanly, so only the fd check can refuse
    other = tmp_path / "other.txt"
    other.write_bytes(b"")
    protected = (book, tmp_path / "notary.db-wal")
    out = tmp_path / "out.jsonl"
    if target == "out_hard":
        out.hardlink_to(book)
    elif target == "out_symlink_unprotected":
        out.symlink_to(other)
    elif target == "stale_head_tmp":
        out.with_name(out.name + ".head.tmp").write_bytes(b"")
    elif target == "lock_symlink":
        out.with_name(out.name + ".lock").symlink_to(book)
    else:
        out = tmp_path / "NOTARY.DB-WAL"
    with pytest.raises(CasebookError) as refused:
        replay_out.append(out, {"type": "replay_summary"}, protected)
    assert refused.value.code == "REPLAY_OUTPUT_REFUSED"
    assert book.read_bytes() == b"" and other.read_bytes() == b""
    if target == "folded":  # refused by name before any file is created (APFS folds case)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["book.jsonl", "other.txt"]


def test_writer_appends_a_readable_chain(tmp_path):
    out = tmp_path / "out.jsonl"
    replay_out.append(out, {"n": 1}, (tmp_path / "book.jsonl",))
    replay_out.append(out, {"n": 2}, (tmp_path / "book.jsonl",))
    entries, _ = replay._read_chain(out)
    assert [entry["n"] for entry, _ in entries] == [1, 2]
