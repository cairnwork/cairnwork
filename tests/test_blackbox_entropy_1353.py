"""#1353 S4-2: entropy preflight is strict-mode-only and precedes candidate execution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import blackbox, blackbox_entropy, protection, verify_cli
from cairnwork.intake import Intake
from cairnwork.pipeline import TaskRejected
from experimental.protected_accept.corpus import ENTRIES, TASKS
from experimental.protected_accept.corpus import declaration as corpus_declaration


def _declaration(values: list[dict]):
    return blackbox.parse(
        json.dumps(
            {
                "salt": "ab" * 32,
                "checks": [
                    {
                        "id": "first-check",
                        "module": "sample",
                        "function": "f",
                        "cases": [
                            {"id": f"case-{i}", "args": [], "kwargs": {}, **value}
                            for i, value in enumerate(values)
                        ],
                    }
                ],
            }
        ).encode()
    )


def _args(*, strict: bool, path: Path):
    return SimpleNamespace(
        require_protection=strict,
        blackbox=str(path),
        verifier=(),
        repo_path=".",
        base="0" * 40,
        candidate="1" * 40,
        allowed_file=["src/sample.py"],
        test_commit="deferred-commit",
        import_path=[],
    )


def _prepare(monkeypatch, tmp_path, declared, candidate):
    monkeypatch.setattr(verify_cli, "_require_unmasked_state", lambda _intake: None)
    monkeypatch.setattr(blackbox, "load", lambda _path: declared)
    monkeypatch.setattr(verify_cli, "verify_candidate", candidate)
    monkeypatch.setattr(verify_cli, "account_can_read", lambda *_args: False)
    return Intake(tmp_path / "state")


@pytest.mark.parametrize(
    ("declared", "code"),
    [
        (_declaration([{"expected": True}]), "BLACKBOX_LOW_ENTROPY"),
        (_declaration([{"expected": 5}, {"expected": 5}]), "BLACKBOX_TOO_FEW_CASES"),
    ],
)
def test_strict_rejects_before_candidate_or_deferred_tests(monkeypatch, tmp_path, declared, code):
    called = False

    def candidate(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"verified": True}

    intake = _prepare(monkeypatch, tmp_path, declared, candidate)
    with pytest.raises(TaskRejected) as got:
        verify_cli._run(intake, _args(strict=True, path=tmp_path / "decl.json"))
    assert got.value.code == code
    assert "'first-check'" in str(got.value)
    assert not called
    assert not (intake.run_root).exists()


def test_strict_good_declaration_reaches_candidate(monkeypatch, tmp_path):
    declared = _declaration([{"expected": 5}, {"expected": 9}])
    seen = {}

    def candidate(*args, **kwargs):
        seen.update(kwargs)
        return {"verified": True}

    intake = _prepare(monkeypatch, tmp_path, declared, candidate)
    result = verify_cli._run(intake, _args(strict=True, path=tmp_path / "decl.json"))
    assert result == {"verified": True}
    assert seen["blackbox"] is declared
    assert seen["require_protection"] == protection.WEAKEST


def test_non_strict_low_entropy_reaches_candidate_unchanged(monkeypatch, tmp_path):
    declared = _declaration([{"expected": True}])
    calls = []

    def candidate(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "FAILED", "reason": "same downstream result"}

    intake = _prepare(monkeypatch, tmp_path, declared, candidate)
    result = verify_cli._run(intake, _args(strict=False, path=tmp_path / "decl.json"))
    assert result == {"status": "FAILED", "reason": "same downstream result"}
    assert calls[0]["blackbox"] is declared
    assert calls[0]["require_protection"] == ""


def test_core_entropy_keeps_the_stage3_corpus_results():
    """Stage 3 measured the rule on this corpus: no benign task refused, only C6 entries."""
    for task in TASKS:
        assert not blackbox_entropy.declaration_problems(corpus_declaration(task)), task
    for entry in ENTRIES:
        refused = bool(blackbox_entropy.declaration_problems(entry["declaration"]))
        assert refused == (entry["cls"] == "C6"), entry["cls"]
