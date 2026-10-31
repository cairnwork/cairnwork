"""#303 (council must-fix 3, James's option A): what a VERIFIED rests on, and strict mode.

Each verify says whether its verdict rests on protected checks (protection.py): ``partial`` when
declared blackbox checks are among the observations Core requires, ``same_process_only`` when
none are. Strict mode makes an unprotected run not VERIFIED by adding one more required observation,
so the real Core decides; Core's definition of VERIFIED is unchanged. The end-to-end tests run
verify_candidate in-process against the real Core, with the blackbox run replaced by a fixed
verdict as in test_blackbox_core.py (the account path itself is test_blackbox_account.py).
"""

import sys
from typing import Any

import pytest
from test_blackbox_core import DECLARED, fixed
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from verify_helpers import FIX, VERIFY, base_of, candidate, cli, declaration, verify

from cairnwork import pipeline_judge, protection
from cairnwork.intake import Intake
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verify_candidate import verify_candidate
from cairnwork.workers import WorkerDefaults

# what this host labels a verdict that rests partly on blackbox checks (macOS: #316's grade)
PARTIAL = protection.level(("pytest-0", "blackbox:add"))


def test_levels():
    linux = {"macos": False}
    assert protection.level((), **linux) == "same_process_only"
    assert protection.level(("pytest-0", "p2p-direct"), **linux) == "same_process_only"
    assert protection.level(("pytest-0", "blackbox:add"), **linux) == "partial"
    assert protection.level(("blackbox:add", "blackbox:mul"), **linux) == "blackbox"
    assert protection.level(("blackbox:add", "protection"), **linux) == "blackbox"  # gate: never
    assert protection.at_least("partial", "partial") and protection.at_least("blackbox", "partial")
    assert not protection.at_least("same_process_only", "partial")
    assert not protection.at_least("partial", "blackbox")
    assert not protection.at_least("full", "partial") and not protection.at_least("partial", "x")


def test_the_macos_grade_is_a_level_of_its_own():
    """3e1d 2026-10-27 05:43: on macOS a protected verdict carries the #316 residual, so it is not
    labelled as the Linux one; a minimum without the suffix wants the Linux grade."""
    mac = {"macos": True}
    assert protection.level((), **mac) == "same_process_only"
    assert protection.level(("pytest-0", "blackbox:add"), **mac) == "partial_macos"
    assert protection.level(("blackbox:add",), **mac) == "blackbox_macos"
    assert protection.level(("blackbox:add",)) == protection.level(
        ("blackbox:add",), macos=sys.platform == "darwin"
    )
    assert protection.at_least("partial_macos", "partial_macos")
    assert protection.at_least("partial", "partial_macos")
    assert protection.at_least("blackbox_macos", "partial_macos")
    assert not protection.at_least("partial_macos", "partial")
    assert not protection.at_least("blackbox_macos", "partial")
    assert not protection.at_least("partial_macos", "blackbox_macos")
    assert not protection.at_least("blackbox_macos", "blackbox")
    assert protection.at_least("blackbox", "blackbox_macos")
    assert not protection.at_least("same_process_only", "partial_macos")
    assert protection.at_least("blackbox_macos", "same_process_only")
    assert protection.coverage("blackbox_macos") == "blackbox" and protection.coverage("x") == ""
    assert protection.WEAKEST == "partial_macos"


def test_the_gate_observation_names_the_level():
    passed, artifact, digest = protection.gate("same_process_only", "partial")
    assert not passed and artifact == "protection#same_process_only>=partial"
    assert digest.startswith("sha256:") and len(digest) == 71
    assert protection.gate("partial", "partial")[0]
    assert protection.reason("same_process_only", "partial") == (
        "protection: VERIFICATION_INCOMPLETE: UNPROTECTED [same_process_only; required partial]"
    )


@pytest.mark.parametrize("verifier_id", ["blackbox:add", "blackbox:", "protection"])
def test_a_task_may_not_name_its_verifier_like_a_protected_check(project, verifier_id):
    task = TaskSpec(
        "k",
        "h",
        "t",
        "i",
        project,
        "a" * 40,
        ("src/calc.py",),
        ((verifier_id, tuple(VERIFY.split())),),
    )
    with pytest.raises(TaskRejected, match="BAD_VERIFIER"):
        check_task(task)


@pytest.mark.parametrize("value", ["same_process_only", "same_process_only_macos", "full", "yes"])
def test_an_unknown_strict_level_is_refused(project, value):
    task = TaskSpec(
        "k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), (("v", tuple(VERIFY.split())),),
        require_protection=value,
    )  # fmt: skip
    with pytest.raises(TaskRejected, match="BAD_PROTECTION"):
        check_task(task)


class NeverRuns(WorkerDefaults):
    def run(self, workdir: Any, task: Any) -> None:
        raise AssertionError("a refused or duplicate request must not reach the worker")


def test_a_strict_resend_under_the_same_key_is_not_the_old_result(project, tmp_path):
    """#322 item 1: strict mode is part of the request, so a strict resend under a key a non-strict
    run completed is refused, not answered with that run. Without it the request is unchanged
    (its digest is pinned in test_bind_import_paths.py)."""
    from cairnwork.journal import Journal
    from cairnwork.notary import Notary
    from cairnwork.ownership import OWNER, KeyReusedWithDifferentRequest, OwnershipLedger
    from cairnwork.pipeline import run_task

    def spec(level: str = "") -> TaskSpec:
        return TaskSpec(
            "k", "h", "t", "i", project, base_of(project), ("src/calc.py",),
            (("v", tuple(VERIFY.split())),), require_protection=level,
        )  # fmt: skip

    strict = spec(protection.WEAKEST)
    assert "require_protection" not in spec().request()
    assert strict.request() == spec().request() | {"require_protection": protection.WEAKEST}
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k", spec().request(), OWNER, "h").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    ledger.set_status(task_id, "COMPLETED")
    stores = {"ledger": ledger, "notary": Notary(tmp_path / "n.sqlite")}
    stores |= {"journal": Journal(tmp_path / "j.sqlite"), "worker": NeverRuns()}
    with pytest.raises(KeyReusedWithDifferentRequest):
        run_task(strict, **stores, run_root=tmp_path / "runs")
    resend = run_task(spec(), **stores, run_root=tmp_path / "runs")
    assert resend.reason == "DUPLICATE_REQUEST" and resend.status == "COMPLETED"
    assert ledger.get(task_id=task_id).status == "COMPLETED"


def run(state, project, **kwargs: Any) -> dict[str, Any]:
    base = base_of(project)
    fix = candidate(project, base, {"src/calc.py": FIX})
    return verify_candidate(
        Intake(state), repo=project, base=base, candidate=fix, allowed_files=("src/calc.py",),
        verifiers=(("pytest-0", tuple(VERIFY.split())),), **kwargs,
    )  # fmt: skip


@pytest.mark.sandbox
def test_a_same_process_only_verified_is_labelled(state, project):
    out = run(state, project)
    assert out["status"] == "VERIFIED" and out["core_status"] == "VERIFIED", out
    assert out["protection"] == "same_process_only"


@pytest.mark.sandbox
def test_strict_mode_keeps_it_from_verified(state, project):
    out = run(state, project, require_protection="partial")
    assert not out["verified"] and out["core_status"] == "FAILED_VERIFICATION", out
    assert out["protection"] == "same_process_only"
    gate = "protection: VERIFICATION_INCOMPLETE: UNPROTECTED [same_process_only; required partial]"
    assert f"[{gate}]" in out["reason"]


@pytest.mark.sandbox
def test_a_protected_verdict_passes_strict_mode(state, project, monkeypatch):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(True))
    out = run(state, project, blackbox=DECLARED, require_protection=protection.WEAKEST)
    assert out["status"] == "VERIFIED" and out["core_status"] == "VERIFIED", out
    assert out["protection"] == PARTIAL and f"[protection: PASS [{PARTIAL}]]" in out["reason"]


@pytest.mark.sandbox
def test_on_macos_a_linux_minimum_is_not_met(state, project, monkeypatch):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(True))
    monkeypatch.setattr(protection, "on_macos", lambda: True)
    out = run(state, project, blackbox=DECLARED, require_protection="partial")
    assert not out["verified"] and out["core_status"] == "FAILED_VERIFICATION", out
    assert "UNPROTECTED [partial_macos; required partial]" in out["reason"]
    monkeypatch.setattr(protection, "on_macos", lambda: False)
    out = run(state, project, blackbox=DECLARED, require_protection="partial")
    assert out["status"] == "VERIFIED" and out["protection"] == "partial", out


@pytest.mark.sandbox
def test_partial_does_not_meet_a_blackbox_minimum(state, project, monkeypatch):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(True))
    out = run(state, project, blackbox=DECLARED, require_protection=protection.FULL + "_macos")
    assert not out["verified"] and out["core_status"] == "FAILED_VERIFICATION", out
    assert f"UNPROTECTED [{PARTIAL}; required blackbox_macos]" in out["reason"]


@pytest.mark.sandbox
def test_without_strict_mode_a_failed_check_still_decides(state, project, monkeypatch):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(False))
    out = run(state, project, blackbox=DECLARED)
    assert not out["verified"] and out["protection"] == PARTIAL, out
    assert "protection:" not in out["reason"]  # no gate when strict mode is off


@pytest.mark.sandbox
def test_the_receipt_binding_carries_the_level(state, project, monkeypatch):
    """With deferred tests the binding (what a receipt is bound to) names the level, and
    judge_candidate checks that it is the level of the ids it gives Core."""
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(True))
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    fix = candidate(project, base, {"src/calc.py": FIX})
    common: dict[str, Any] = {
        "repo": project, "base": base, "candidate": fix, "allowed_files": ("src/calc.py",),
        "verifiers": (("v", tuple(" ".join(CHECK[0][1]).split())),), "test_commit": test_commit,
    }  # fmt: skip
    out = verify_candidate(
        Intake(state), **common, blackbox=DECLARED, require_protection=protection.WEAKEST
    )
    assert out["status"] == "VERIFIED", out
    assert out["binding"]["protection"] == PARTIAL and out["protection"] == PARTIAL


def test_a_binding_names_the_level_only_when_protected(project):
    from types import SimpleNamespace

    from cairnwork.notary_verify import binding_extras

    plain = TaskSpec("k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), (("v", ()),))
    declared = TaskSpec(
        "k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), (("v", ()),), blackbox=DECLARED
    )
    only = TaskSpec("k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), (), blackbox=DECLARED)
    assert "protection" not in binding_extras(plain, None)
    assert binding_extras(declared, None)["protection"] == PARTIAL
    assert binding_extras(only, None)["protection"] == protection.level(("blackbox:add",))
    # #322 item 2: the P2P verifier is same-process, so it pulls a blackbox-only task to partial
    p2p_plan = SimpleNamespace(spec=SimpleNamespace(verifier_id="p2p-module-dir"))
    assert binding_extras(only, p2p_plan)["protection"] == PARTIAL


def test_a_binding_that_names_another_level_stops_the_run(monkeypatch):
    """A slip between the binding and the ids Core gets is an error, never a receipt."""
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setattr(pipeline_judge, "run_verifiers", lambda *a, **k: [])
    task = SimpleNamespace(
        blackbox=None, repo=Path("r"), base_commit="b" * 40, import_paths=(), allowed_files=(),
        allow_deletions=False, require_protection="",
    )  # fmt: skip
    result = SimpleNamespace(candidate_commit="j", task_id="t", attempt_id="a", logs=[])
    with pytest.raises(RuntimeError, match="protection"):
        pipeline_judge.judge_candidate(
            task, (), "d", None, Path("x"), result, (), (None, None), lambda phase: None,
            binding={"head": "h", "protection": PARTIAL},
        )  # fmt: skip


def test_the_cli_refuses_strict_mode_without_a_declaration(state, project):
    base = base_of(project)
    fix = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, fix, "--require-protection")
    assert code == 2 and out["error"] == "UNPROTECTED", out
    assert not (state / "ownership.sqlite").exists()  # nothing ran


@pytest.mark.sandbox
def test_the_cli_reports_the_level(state, project, tmp_path):
    base = base_of(project)
    fix = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, fix)
    assert code == 0 and out["protection"] == "same_process_only", out
    declared = str(declaration(tmp_path))
    code, out = verify(
        state,
        project,
        base,
        fix,
        "--blackbox",
        declared,
        "--require-protection",
        "--import-path",
        "src",
    )
    assert code == 2 and out["protection"] == PARTIAL, out  # owner mode: the check never passes
    assert (
        "BLACKBOX_NEEDS_VERIFIER_ACCOUNT" in out["reason"] and "protection: PASS" in out["reason"]
    )
    assert cli(state, "cleanup")[0] == 0
