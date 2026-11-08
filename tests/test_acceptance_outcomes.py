"""Acceptance follow-up ledger step 1 (Issue #774 AC1-AC7)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import setup_cli_fixture
from conftest import git
from hypothesis import given
from hypothesis import strategies as st

from experimental.acceptance_outcomes import OutcomesError, add_event, build_report, record_call
from experimental.acceptance_outcomes.cli import main
from experimental.acceptance_outcomes.report import status_of

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fx(tmp_path: Path, repo: Path) -> dict[str, Any]:
    base = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    cand = setup_cli_fixture(state, repo)
    return {"db": state / "notary.sqlite", "base": base, "cand": cand, "led": tmp_path / "l.jsonl"}


def inputs(fx: dict[str, Any], version: str = "v1", **kw: str) -> dict[str, str]:
    d = {"repo": "r", "base": fx["base"], "candidate": fx["cand"], "version": version}
    return {**d, **kw}


def verified(fx: dict[str, Any], **kw: str) -> dict[str, Any]:
    return {"status": "VERIFIED", "task_id": "task-1", "attempt_id": "a1", "reason": "", **kw}


def call_id(fx: dict[str, Any]) -> str:
    assert record_call(fx["led"], verified(fx), inputs(fx), fx["db"]) in ("ADDED", "DUPLICATE")
    return next(json.loads(x)["call_id"] for x in fx["led"].read_text().splitlines())


def finding(fx: dict[str, Any], cid: str, root: str = "r1", **kw: Any) -> None:
    ev = {"type": "finding", "source": "code_review", "finder": "alice", "root": root, **kw}
    assert add_event(fx["led"], cid, ev) == "ADDED"


def confirm(fx: dict[str, Any], cid: str, who: str = "bob", fid: str = "f1", **kw: Any) -> str:
    ev = {"type": "confirmation", "finding_id": fid, "confirmer": who, "evidence": "e",
          "outcome": "regression", **kw}  # fmt: skip
    return add_event(fx["led"], cid, ev)


def test_ac1_every_call_kind_recorded(fx: dict[str, Any]) -> None:
    led = fx["led"]
    assert record_call(led, verified(fx), inputs(fx), fx["db"]) == "ADDED"
    assert record_call(led, verified(fx), inputs(fx), fx["db"]) == "DUPLICATE"
    for i, st_ in enumerate(("REFUSED", "UNCOMPARABLE")):
        res = {"status": st_, "reason": f"why{i}"}
        assert record_call(led, res, inputs(fx, candidate=str(i) * 40)) == "ADDED"
    assert record_call(led, {"error": "BAD_SCOPE"}, inputs(fx, candidate="9" * 40)) == "ADDED"
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert [r["verdict"] for r in rows] == ["VERIFIED", "REFUSED", "UNCOMPARABLE", "PRE_CORE"]
    assert [r["no_notary_row"] for r in rows] == [False, True, True, True]
    assert rows[1]["reason"] == "why0" and rows[0]["test_commit"] == ""


def test_ac1_mismatch_writes_nothing(fx: dict[str, Any]) -> None:
    for bad in ({"candidate": "1" * 40}, {"base": "2" * 40}):
        with pytest.raises(OutcomesError) as e:
            record_call(fx["led"], verified(fx), inputs(fx, **bad), fx["db"])
        assert e.value.code == "OUTCOME_RECORD_MISMATCH"
    with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH"):
        record_call(fx["led"], verified(fx, attempt_id="nope"), inputs(fx), fx["db"])
    failed = {"status": "FAILED", "task_id": "t", "attempt_id": "none", "reason": "x"}
    with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH"):  # Core result, no row
        record_call(fx["led"], failed, inputs(fx), fx["db"])
    assert not fx["led"].exists()


def test_ac2_edit_or_removal_refused_on_read(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    finding(fx, cid)
    led = fx["led"]
    good = led.read_text()
    led.write_text(good.replace('"VERIFIED"', '"FAILED"'))
    for fn in (lambda: build_report(led), lambda: confirm(fx, cid)):
        with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
            fn()
    led.write_text(good.splitlines(keepends=True)[0])  # drop the tail
    with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
        build_report(led)
    led.unlink()
    with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
        build_report(led)


def test_ac2_event_leaves_call_line_untouched(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    first = fx["led"].read_text()
    finding(fx, cid)
    assert fx["led"].read_text().startswith(first)


def test_ac3_finding_is_only_a_suspicion(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    finding(fx, cid, clones=["alice2"])
    rep = build_report(fx["led"])["v1"]["VERIFIED"]
    assert rep["overturned"] == 0 and rep["tracked"] == 1
    cases: list[tuple[str, dict[str, Any]]] = [
        ("alice", {}),
        ("alice2", {}),
        ("carol", {"clones": ["alice"]}),
    ]
    for who, kw in cases:
        with pytest.raises(OutcomesError) as e:
            confirm(fx, cid, who, **kw)
        assert e.value.code == "SAME_CONFIRMER"
    assert confirm(fx, cid) == "ADDED"
    assert build_report(fx["led"])["v1"]["VERIFIED"]["overturned"] == 1


def test_ac3_confirmation_needs_finding_and_evidence(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    with pytest.raises(OutcomesError, match="OUTCOME_UNKNOWN_FINDING"):
        confirm(fx, cid)
    finding(fx, cid)
    with pytest.raises(OutcomesError, match="OUTCOME_INVALID"):
        confirm(fx, cid, evidence="")
    with pytest.raises(OutcomesError, match="OUTCOME_INVALID"):
        add_event(
            fx["led"], cid, {"type": "finding", "source": "gossip", "finder": "a", "root": "r"}
        )


def test_ac4_kinds_and_same_root_counts_once(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    finding(fx, cid, "root-a")
    finding(fx, cid, "root-a", finder="dave")
    finding(fx, cid, "root-b")
    with pytest.raises(OutcomesError, match="OUTCOME_KIND_INVALID"):
        confirm(fx, cid, outcome="refused_but_correct")
    with pytest.raises(OutcomesError, match="OUTCOME_KIND_INVALID"):
        confirm(fx, cid, outcome="made_up")
    confirm(fx, cid, fid="f1", outcome="regression")
    confirm(fx, cid, who="erin", fid="f2", outcome="regression")
    confirm(fx, cid, fid="f3", outcome="not_fixed")
    v = build_report(fx["led"])["v1"]["VERIFIED"]
    assert v["overturned"] == 1 and v["overturned_by_kind"] == {"not_fixed": 1, "regression": 1}


def test_ac4_refused_but_correct_only_for_non_verified(fx: dict[str, Any]) -> None:
    res = {"status": "REFUSED", "reason": "protected"}
    record_call(fx["led"], res, inputs(fx))
    cid = json.loads(fx["led"].read_text().splitlines()[0])["call_id"]
    finding(fx, cid)
    with pytest.raises(OutcomesError, match="OUTCOME_KIND_INVALID"):
        confirm(fx, cid, outcome="regression")
    confirm(fx, cid, outcome="refused_but_correct")
    r = build_report(fx["led"])["v1"]["refused_but_correct"]
    assert (r["num"], r["den"], r["coverage"]) == (1, 1, "1/1")


def test_ac5_statuses(fx: dict[str, Any]) -> None:
    assert status_of([]) == "untracked"
    assert status_of([{"type": "observed"}]) == "tracking"
    obs = {"type": "observed"}
    assert status_of([{"type": "closed", "checks_done": True}]) == "unknown"  # no window
    assert status_of([obs, {"type": "closed", "checks_done": True}]) == "no_defect_found"
    assert status_of([{"type": "closed", "checks_done": False}]) == "unknown"
    assert status_of([{"type": "closed", "checks_done": True}, {"type": "confirmation"}]) == (
        "overturned"
    )
    cid = call_id(fx)
    assert build_report(fx["led"])["v1"]["cases"]["untracked"] == [cid]
    add_event(fx["led"], cid, {"type": "observed", "start": "d1", "end": "d8"})
    assert build_report(fx["led"])["v1"]["cases"]["tracking"] == [cid]
    add_event(fx["led"], cid, {"type": "closed", "checks_done": True})
    assert build_report(fx["led"])["v1"]["cases"]["no_defect_found"] == [cid]
    with pytest.raises(OutcomesError, match="OUTCOME_INVALID"):
        add_event(fx["led"], cid, {"type": "observed"})


def test_ac6_report_counts_coverage_cases_unknown(
    fx: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cid = call_id(fx)
    record_call(fx["led"], {"status": "REFUSED", "reason": "x"}, inputs(fx, "v2"))
    record_call(fx["led"], {"error": "BAD_SCOPE"}, inputs(fx, "v2", candidate="8" * 40))
    rep = build_report(fx["led"])
    assert rep["v1"]["VERIFIED"]["text"] == "unknown" and rep["v1"]["VERIFIED"]["coverage"] == "0/1"
    assert rep["v2"]["total"] == 2 and rep["v2"]["reached_core"] == 0
    assert rep["v2"]["counts"]["PRE_CORE"] == 1 and rep["v2"]["counts"]["REFUSED"] == 1
    finding(fx, cid)
    confirm(fx, cid)
    assert main(["report", "--ledger", str(fx["led"]), "--cases"]) == 0
    out = capsys.readouterr().out
    assert "overturn rate: 1/1 = 1.000, coverage 1/1" in out and cid in out
    assert main(["report", "--ledger", str(fx["led"]), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["v1"]["VERIFIED"]["overturned"] == 1


@given(
    closed=st.lists(st.booleans(), max_size=8),
    untracked=st.integers(min_value=0, max_value=20),
)
def test_ac6_untracked_never_lowers_rate(closed: list[bool], untracked: int) -> None:
    from experimental.acceptance_outcomes.report import _verified

    def rate(extra: int) -> float | None:
        ids = [f"c{i}" for i in range(len(closed) + extra)]
        by_call = {i: ("overturned" if closed[n] else "no_defect_found") if n < len(closed)
                   else "untracked" for n, i in enumerate(ids)}  # fmt: skip
        events = {i: [{"type": "finding", "finding_id": "f1", "root": "r"},
                      {"type": "confirmation", "finding_id": "f1", "outcome": "regression"}]
                  for i, s in by_call.items() if s == "overturned"}  # fmt: skip
        return _verified(ids, by_call, events)["rate"]

    before, after = rate(0), rate(untracked)
    assert before == after


def test_failed_chain_gives_no_partial_numbers(fx: dict[str, Any], capsys) -> None:
    call_id(fx)
    fx["led"].write_text(fx["led"].read_text().replace('"r"', '"q"'))
    fx["led"].with_name("l.jsonl.head").write_text("0" * 64 + "\n")
    assert main(["report", "--ledger", str(fx["led"])]) == 2
    out = capsys.readouterr().out
    assert "OUTCOMES_NOT_APPEND_ONLY" in out and "VERIFIED" not in out.replace("OUTCOMES", "")


def test_cli_record_event_roundtrip(fx: dict[str, Any], tmp_path: Path) -> None:
    res = tmp_path / "res.json"
    res.write_text(json.dumps(verified(fx)))
    base = ["--ledger", str(fx["led"])]
    rec = ["record", *base, "--result", str(res), "--repo", "r", "--base", fx["base"]]
    rec += ["--candidate", fx["cand"], "--version", "v1", "--notary-db", str(fx["db"])]
    assert main(rec) == 0
    cid = json.loads(fx["led"].read_text().splitlines()[0])["call_id"]
    f = ["event", *base, "--call", cid, "--type", "finding", "--source", "code_review"]
    assert main([*f, "--finder", "alice", "--root", "r1"]) == 0
    c = ["event", *base, "--call", cid, "--type", "confirmation", "--finding-id", "f1"]
    c += ["--confirmer", "alice", "--outcome", "regression", "--evidence", "e"]
    assert main(c) == 2
    assert main(["event", *base, "--call", "cnone", "--type", "closed"]) == 2


def test_ac7_default_off_and_core_never_imports_it() -> None:
    pat = re.compile(r"acceptance_outcomes")
    for p in (ROOT / "src").rglob("*.py"):
        assert not pat.search(p.read_text()), p
    toml = (ROOT / "pyproject.toml").read_text()
    assert "experimental" in toml  # import-linter contract names it (lint-imports gate)


def test_unknown_outside_denominator_and_coverage(fx: dict[str, Any]) -> None:
    from experimental.acceptance_outcomes.report import _verified

    ids = ["a", "b", "c", "d"]
    by_call = {"a": "overturned", "b": "no_defect_found", "c": "unknown", "d": "unknown"}
    ev = {"a": [{"type": "finding", "finding_id": "f1", "root": "r"},
                {"type": "confirmation", "finding_id": "f1", "outcome": "regression"}]}  # fmt: skip
    v = _verified(ids, by_call, ev)
    assert (v["closed"], v["unknown"], v["den"], v["coverage"]) == (2, 2, 2, "2/4")
    assert v["text"] == "1/2 = 0.500"


@given(base=st.lists(st.sampled_from(["overturned", "no_defect_found"]), max_size=8),
       unk=st.integers(min_value=0, max_value=10))  # fmt: skip
def test_unknown_never_lowers_rate_or_raises_coverage(base: list[str], unk: int) -> None:
    from experimental.acceptance_outcomes.report import _verified

    def run(extra: int) -> dict[str, Any]:
        ids = [f"c{i}" for i in range(len(base) + extra)]
        by_call = {i: base[n] if n < len(base) else "unknown" for n, i in enumerate(ids)}
        ev = {i: [{"type": "finding", "finding_id": "f1", "root": "r"},
                  {"type": "confirmation", "finding_id": "f1", "outcome": "regression"}]
              for i, s_ in by_call.items() if s_ == "overturned"}  # fmt: skip
        return _verified(ids, by_call, ev)

    a, b = run(0), run(unk)
    assert a["rate"] == b["rate"]
    assert (b["coverage_value"] or 0) <= (a["coverage_value"] or 0) or not base


def test_identical_inputs_different_reasons_are_distinct_calls(fx: dict[str, Any]) -> None:
    led = fx["led"]
    one, two = {"error": "BAD_SCOPE", "reason": "r1"}, {"error": "BAD_SCOPE", "reason": "r2"}
    assert record_call(led, one, inputs(fx)) == "ADDED"
    assert record_call(led, two, inputs(fx)) == "ADDED"
    assert record_call(led, one, inputs(fx)) == "DUPLICATE"  # byte-identical replay
    assert record_call(led, {**one, "call_ref": "x"}, inputs(fx, call_ref="2")) == "ADDED"
    assert build_report(led)["v1"]["total"] == 3


def test_verdict_must_match_notary_status_and_row_presence(fx: dict[str, Any]) -> None:
    led = fx["led"]
    # the fixture row is VERIFIED: a FAILED claim against it, or a pre-Core label with its ids
    with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH"):
        record_call(led, verified(fx, status="FAILED"), inputs(fx), fx["db"])
    for res in ({"status": "REFUSED"}, {"error": "BAD_SCOPE"}):
        with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH"):
            record_call(led, {**res, "task_id": "task-1", "attempt_id": "a1"}, inputs(fx), fx["db"])
    assert not led.exists()


def test_result_not_a_json_object_exits_2(
    fx: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    res = tmp_path / "r.json"
    res.write_text("[1]")
    rec = ["record", "--ledger", str(fx["led"]), "--result", str(res), "--repo", "r"]
    rec += ["--base", fx["base"], "--candidate", fx["cand"], "--version", "v1"]
    assert main(rec) == 2 and "OUTCOME_INVALID" in capsys.readouterr().out
    with pytest.raises(OutcomesError, match="OUTCOME_INVALID"):
        record_call(fx["led"], [1], inputs(fx))  # type: ignore[arg-type]


def test_tail_truncation_refused_on_append_and_read(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    finding(fx, cid)
    led = fx["led"]
    lines = led.read_text().splitlines(keepends=True)
    led.write_text(lines[0])  # a clean cut at a line boundary: chain fine, head disagrees
    before = led.read_text()
    with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
        build_report(led)
    with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
        finding(fx, cid, "r2")
    with pytest.raises(OutcomesError, match="OUTCOMES_NOT_APPEND_ONLY"):
        record_call(led, {"error": "X"}, inputs(fx))
    assert led.read_text() == before


def test_refused_but_correct_denominator_is_closed_failed_or_refused(fx: dict[str, Any]) -> None:
    led = fx["led"]
    ids = []
    for n in range(4):
        # #809: refused before Core, so no notary row; a notary-lost call is outside every rate
        record_call(led, {"status": "REFUSED", "reason": f"w{n}"}, inputs(fx))
    ids = [json.loads(x)["call_id"] for x in led.read_text().splitlines()]
    add_event(led, ids[0], {"type": "observed", "start": "a", "end": "b"})
    add_event(led, ids[0], {"type": "closed", "checks_done": True})  # no_defect_found
    add_event(led, ids[1], {"type": "closed", "checks_done": True})  # unknown (no window)
    finding(fx, ids[2])
    confirm(fx, ids[2], outcome="refused_but_correct")  # overturned
    add_event(led, ids[3], {"type": "observed", "start": "a", "end": "b"})  # tracking only
    r = build_report(led)["v1"]["refused_but_correct"]
    assert (r["num"], r["den"], r["coverage"]) == (1, 2, "2/4")


def test_appends_wait_for_the_lock(fx: dict[str, Any]) -> None:
    import fcntl
    import threading

    led = fx["led"]
    cid = call_id(fx)
    lock = led.with_name(led.name + ".lock")
    assert lock.exists()
    fd = lock.open("wb")
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()

    def work() -> None:
        finding(fx, cid)
        done.set()

    thread = threading.Thread(target=work)
    thread.start()
    assert not done.wait(0.5)  # blocked while another writer holds the lock
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()
    thread.join(10)
    assert done.is_set()
    assert len(build_report(led)["v1"]["cases"]["tracking"]) == 1
