"""PAUSE (#476) step 1: the one schema module, the OWNER/EXPIRES files, the core-import contract
and the shared interfaces."""

import datetime
import shutil
import tomllib
from pathlib import Path

import pytest
from pause_fakes import FakeGate, FakeGuard, FakePlanner, MemoryEvents

from experimental.pause import interfaces, schema
from experimental.pause.schema import Reason

ROOT = Path(__file__).resolve().parents[1]
PAUSE_DIR = ROOT / "experimental" / "pause"

# #476 AC3 as written (amendment 1 point 3 fixes PAUSE_PLANNER_REFUSED as the spelling)
SPEC_AC3 = {
    "PAUSE_PLANNER_TIMEOUT",
    "PAUSE_PLANNER_REFUSED",
    "PAUSE_GATE_BAD_OUTPUT",
    "PAUSE_LOCAL_UNREACHABLE",
    "PAUSE_WORKER_CRASH",
    "PAUSE_PLANNER_REFUSAL",
    "PAUSE_GATE_REFUSAL",
}
SPEC_OTHER = {"PAUSE_NO_CUT", "PAUSE_SECRET_BLOCKED"}  # AC2, AC4
# an empty or malformed plan (PLANNER_REFUSED stays the API layer's), as the gate's BAD_OUTPUT
PLANNER_OUTPUT = {"PAUSE_PLANNER_BAD_OUTPUT"}
AMENDMENT_2 = {  # the five fallback classes' missing codes, and AC8
    "PAUSE_LOCAL_OUT_OF_MEMORY",
    "PAUSE_PLANNER_QUOTA_EXHAUSTED",
    "PAUSE_GATE_REJECT_LIMIT",
    "PAUSE_GUARD_ROLLED_BACK",
    "PAUSE_GUARD_UNAVAILABLE",
}
GATE_API = {"PAUSE_GATE_TIMEOUT", "PAUSE_GATE_REFUSED", "PAUSE_GATE_QUOTA_EXHAUSTED"}
STEP_3 = {"PAUSE_SCOPE_VIOLATION"}  # the final patch left write_scope or is not regular files
AC5_FIELDS = {  # AC5's list, plus what AC2 and AC8 need
    "task_id",
    "checkpoint_sha256",
    "plan_sha256",
    "planner_model_id",
    "gate_model_id",
    "planner_effort",
    "gate_effort",
    "gate_verdict",
    "reason",
    "intervened",
    "cut_turn_index",
    "event_seq",
    "guard_rollbacks",
}


def test_every_reason_code_is_listed_by_the_spec_or_an_amendment():
    assert SPEC_AC3 <= set(Reason) and SPEC_OTHER <= set(Reason)
    assert set(Reason) == SPEC_AC3 | SPEC_OTHER | AMENDMENT_2 | GATE_API | STEP_3 | PLANNER_OUTPUT
    assert Reason.PAUSE_PLANNER_BAD_OUTPUT in schema.FALLBACK_REASONS
    assert Reason.PAUSE_SCOPE_VIOLATION in schema.FALLBACK_REASONS
    assert all(r.value == r.name and r.startswith("PAUSE_") for r in Reason)


def test_every_code_is_either_a_fallback_or_the_one_trigger():
    assert set(schema.FALLBACK_REASONS) | set(schema.TRIGGER_REASONS) == set(Reason)
    assert not set(schema.FALLBACK_REASONS) & set(schema.TRIGGER_REASONS)
    assert schema.TRIGGER_REASONS == (Reason.PAUSE_GUARD_ROLLED_BACK,)


def test_receipt_fields_cover_ac5_and_ac2():
    assert AC5_FIELDS <= set(schema.RECEIPT_FIELDS)
    assert set(schema.AC2_FIELDS) <= set(schema.RECEIPT_FIELDS)
    assert schema.RECEIPT_FIELDS[-1] == "receipt_sha256"
    assert len(set(schema.RECEIPT_FIELDS)) == len(schema.RECEIPT_FIELDS)
    assert not set(schema.RECEIPT_VOLATILE_FIELDS) & set(schema.AC2_FIELDS)


def marker_problems(directory: Path) -> list[str]:
    """What an experimental capability's OWNER and EXPIRES files lack (README rule)."""
    problems = []
    owner, expires = directory / "OWNER", directory / "EXPIRES"
    if not owner.is_file() or not owner.read_text().strip():
        problems.append("OWNER missing or empty")
    try:
        datetime.date.fromisoformat(expires.read_text().strip())
    except (OSError, ValueError):
        problems.append("EXPIRES missing or not an ISO date")
    return problems


def test_pause_has_owner_and_expires():
    assert marker_problems(PAUSE_DIR) == []
    assert (PAUSE_DIR / "OWNER").read_text().strip() == "mini-layer1-0c79"
    assert (PAUSE_DIR / "EXPIRES").read_text().strip() == "2026-11-20"


@pytest.mark.parametrize(
    ("name", "text"), [("OWNER", None), ("EXPIRES", None), ("EXPIRES", "soon")]
)
def test_a_missing_or_bad_marker_file_fails(tmp_path, name, text):
    copy = tmp_path / "pause"
    shutil.copytree(PAUSE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / name).unlink()
    if text is not None:
        (copy / name).write_text(text)
    assert marker_problems(copy) != []


def test_the_core_import_contract_covers_pause():
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    forbid = [
        c
        for c in contracts["contracts"]
        if c["type"] == "forbidden" and "cairnwork" in c["source_modules"]
    ]
    assert any(
        "experimental" in c["forbidden_modules"] and not c["allow_indirect_imports"] for c in forbid
    )
    hits = [p for p in (ROOT / "src").rglob("*.py") if "experimental.pause" in p.read_text()]
    assert hits == []


def test_the_fakes_satisfy_the_interfaces():
    assert isinstance(FakePlanner([]), interfaces.Planner)
    assert isinstance(FakeGate([]), interfaces.Gate)
    assert isinstance(FakeGuard(), interfaces.RollbackGuard)
    assert isinstance(MemoryEvents(), interfaces.EventLog)


def test_a_read_only_guard_need_not_roll_back():
    class DigestOnly:
        def digest(self) -> str:
            return "0" * 64

    assert isinstance(DigestOnly(), interfaces.CheckoutGuard)
    assert not isinstance(DigestOnly(), interfaces.RollbackGuard)


def test_a_broken_guard_fails_closed():
    guard = FakeGuard()
    before = guard.digest()
    guard.files["src/app.py"] = ("x = 2\n", 0o644)
    guard.rollback(before)
    assert guard.digest() == before
    guard.broken = True
    with pytest.raises(interfaces.GuardUnavailable):
        guard.digest()
    assert interfaces.GuardUnavailable.reason is Reason.PAUSE_GUARD_UNAVAILABLE
