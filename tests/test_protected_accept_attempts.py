from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from cairnwork.blackbox import Declaration
from cairnwork.verify_context import VerifyContext
from experimental.protected_accept import attempts as attempts_module
from experimental.protected_accept.attempts import attempt_key
from experimental.protected_accept.corpus import declaration
from experimental.protected_accept.once import OnceStore


def test_attempt_key_ignores_salt_but_tracks_commit_and_checks() -> None:
    original = declaration("clamp")
    resalted = Declaration("b2" * 32, original.checks)
    changed_check = declaration("clamp", low_entropy=True)
    commit = "a" * 40
    assert attempt_key(original, commit) == attempt_key(resalted, commit)
    assert attempt_key(original, commit) != attempt_key(original, "b" * 40)
    assert attempt_key(original, commit) != attempt_key(changed_check, commit)
    reordered = Declaration(
        original.salt,
        tuple(replace(check, cases=check.cases[::-1]) for check in original.checks[::-1]),
    )
    assert attempt_key(original, commit) == attempt_key(reordered, commit)


def test_run_protected_once_blocks_resalt_and_low_entropy_does_not_consume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = VerifyContext(tmp_path, tmp_path / "runs", account=cast(Any, object()))
    calls: list[str] = []
    monkeypatch.setattr(
        attempts_module,
        "run_protected",
        lambda decl, *args, **kwargs: (
            calls.append(decl.salt)
            or type("Verdict", (), {"status": "PROTECTED_PASS", "code": "OK", "checks": ()})()
        ),
    )
    store = OnceStore(tmp_path / "declarations")
    attempts = OnceStore(tmp_path / "attempts")
    commit = "c" * 40
    low = declaration("clamp", low_entropy=True)
    refused = attempts_module.run_protected_once(
        low, commit, ctx=ctx, run_id="low", store=store, attempts_store=attempts
    )
    assert refused.code.startswith("LOW_ENTROPY")
    valid = declaration("clamp")
    first = attempts_module.run_protected_once(
        valid, commit, ctx=ctx, run_id="first", store=store, attempts_store=attempts
    )
    resalted = Declaration("d4" * 32, valid.checks)
    second = attempts_module.run_protected_once(
        resalted, commit, ctx=ctx, run_id="second", store=store, attempts_store=attempts
    )
    assert first.status == "PROTECTED_PASS"
    assert (second.status, second.code) == ("REFUSED", "ATTEMPT_REUSED")
    assert calls == [valid.salt]
