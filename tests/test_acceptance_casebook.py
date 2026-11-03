"""Acceptance casebook step 1: add/list, append-only chain, default off (Issue #751 AC1,2,3,5)."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest
from attest.test_attest_cli import setup_cli_fixture
from conftest import git

from experimental.acceptance_casebook.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    cand = setup_cli_fixture(state, repo)
    return {"db": str(state / "notary.sqlite"), "base": base, "cand": cand, "dir": str(tmp_path)}


def add(ledger: dict[str, str], *extra: str, **kw: str) -> int:
    opt = {
        "candidate": ledger["cand"],
        "base": ledger["base"],
        "version": "v1",
        "kind": "accepted_bad",
        "attempt-id": "a1",
    }
    opt.update(kw)
    argv = ["add", "--casebook", str(Path(ledger["dir"]) / "cb.jsonl"), "--notary-db", ledger["db"]]
    argv += ["--task-id", "task-1", "--found-by", "reviewer-x"]
    argv += ["--evidence", "ref-1"]
    for k, v in opt.items():
        argv += [f"--{k}", v]
    return main([*argv, *extra])


def book(ledger: dict[str, str]) -> Path:
    return Path(ledger["dir"]) / "cb.jsonl"


def test_ac1_add_appends_one_line_mode_0600(ledger: dict[str, str]) -> None:
    assert add(ledger) == 0
    lines = book(ledger).read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["prev"] == "0" * 64 and entry["kind"] == "accepted_bad"
    assert stat.S_IMODE(book(ledger).stat().st_mode) == 0o600


def test_ac1_mismatch_refused_nothing_written(
    ledger: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert add(ledger, candidate="1" * 40) == 2
    assert "CASE_RECORD_MISMATCH" in capsys.readouterr().out
    assert add(ledger, base="2" * 40) == 2
    assert not book(ledger).exists()


def test_ac1_missing_row_refused(ledger: dict[str, str]) -> None:
    assert add(ledger, **{"attempt-id": "no-such-attempt"}) == 2
    assert not book(ledger).exists()


def test_ac2_duplicate_no_new_line(
    ledger: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert add(ledger) == 0
    capsys.readouterr()
    assert add(ledger) == 0
    assert "DUPLICATE" in capsys.readouterr().out
    assert len(book(ledger).read_text().splitlines()) == 1


def test_ac2_other_kind_conflict(
    ledger: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert add(ledger) == 0
    assert add(ledger, kind="refused_good") == 2
    assert "CASE_CONFLICT" in capsys.readouterr().out
    assert len(book(ledger).read_text().splitlines()) == 1
    assert add(ledger, version="v2", kind="refused_good") == 0  # other version is a new case


def test_ac3_list_intact_chain_prints_cases(
    ledger: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    add(ledger)
    add(ledger, version="v2")
    capsys.readouterr()
    assert main(["list", "--casebook", str(book(ledger)), "--json"]) == 0
    cases = json.loads(capsys.readouterr().out)["cases"]
    assert [c["version"] for c in cases] == ["v1", "v2"]


@pytest.mark.parametrize(
    "tamper", ["edit", "remove_first", "remove_last", "truncate", "remove_file"]
)
def test_ac3_tampered_refused_no_partial(
    ledger: dict[str, str], capsys: pytest.CaptureFixture[str], tamper: str
) -> None:
    for v in ("v1", "v2", "v3"):
        add(ledger, version=v)
    lines = book(ledger).read_bytes().splitlines(keepends=True)
    if tamper == "edit":
        lines[1] = lines[1].replace(b"reviewer-x", b"someone-y")
    elif tamper == "remove_first":
        lines = lines[1:]
    elif tamper == "remove_last":  # the chain alone cannot see this; the head file does
        lines = lines[:-1]
    elif tamper == "truncate":
        lines[2] = lines[2][:-5]
    if tamper == "remove_file":
        book(ledger).unlink()
    else:
        book(ledger).write_bytes(b"".join(lines))
    capsys.readouterr()
    assert main(["list", "--casebook", str(book(ledger))]) == 2
    out = capsys.readouterr().out
    assert "CASEBOOK_NOT_APPEND_ONLY" in out and "v1" not in out
    before = book(ledger).read_bytes() if book(ledger).exists() else None
    assert add(ledger, version="v9") == 2
    assert (book(ledger).read_bytes() if book(ledger).exists() else None) == before


def test_concurrent_adds_keep_one_chain(ledger: dict[str, str]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from experimental.acceptance_casebook.store import add_case, list_cases

    case = {"candidate": ledger["cand"], "base": ledger["base"], "task_id": "task-1",
            "attempt_id": "a1", "kind": "accepted_bad", "found_by": "reviewer-x",
            "evidence": "note-1"}  # fmt: skip
    with ThreadPoolExecutor(8) as pool:
        db = Path(ledger["db"])
        done = list(pool.map(lambda i: add_case(book(ledger), db, {**case, "version": f"v{i}"}),
                             range(16)))  # fmt: skip
    assert done == ["ADDED"] * 16
    assert sorted(c["version"] for c in list_cases(book(ledger))) == sorted(
        f"v{i}" for i in range(16)
    )


def test_ac5_core_never_imports_casebook() -> None:
    pattern = re.compile(r"^\s*(from|import)\s+experimental", re.M)
    hits = [p for p in (ROOT / "src").rglob("*.py") if pattern.search(p.read_text())]
    assert hits == []


def test_ac5_no_file_unless_add_called(ledger: dict[str, str], tmp_path: Path) -> None:
    import experimental.acceptance_casebook  # noqa: F401

    assert main(["list", "--casebook", str(book(ledger))]) == 0
    assert not book(ledger).exists()
    assert not list(ROOT.rglob("*.jsonl.casebook"))
