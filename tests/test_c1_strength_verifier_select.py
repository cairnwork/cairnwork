from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork import verifier_select
from cairnwork.intake_request import IntakeRefused
from cairnwork.verifier_account import VerifierAccount


def _account(
    monkeypatch: pytest.MonkeyPatch, root: Path, user: str = "cairnwork-verifier2"
) -> None:
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user=user, root=root),
    )


def test_unregistered_account_name_keeps_the_public_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _account(monkeypatch, root, "cairnwork-verifier2")
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _user: object())
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account("not-registered")
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT"


def test_selection_looks_up_the_configured_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _account(monkeypatch, root, "configured-verifier")
    looked_up: list[str] = []

    def get_user(user: str) -> Any:
        looked_up.append(user)
        return object()

    monkeypatch.setattr(verifier_select.pwd, "getpwnam", get_user)
    assert verifier_select.select_account("cairnwork-verifier2").user == "configured-verifier"
    assert looked_up == ["configured-verifier"]


def test_selected_intake_honors_disabled_account_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))
    selected = VerifierAccount(user="cairnwork-verifier2", root=tmp_path)
    intake = verifier_select.SelectedIntake(state, selected)

    assert intake.verifier_account() is None


def test_relative_root_is_refused_as_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _account(monkeypatch, Path("relative-root"))
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account("cairnwork-verifier2")
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT"
    assert "invalid verifier root: relative-root" in str(caught.value)


def test_missing_root_is_reported_unavailable_not_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-root"
    _account(monkeypatch, missing)
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account("cairnwork-verifier2")
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT"
    assert f"unavailable verifier root: {missing}" in str(caught.value)


def test_unknown_verifier_user_is_refused_with_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _account(monkeypatch, root, "ghost-verifier")

    def no_such_user(_user: str) -> Any:
        raise KeyError("ghost-verifier")

    monkeypatch.setattr(verifier_select.pwd, "getpwnam", no_such_user)
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account("cairnwork-verifier2")
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT"
    assert "unknown verifier user: ghost-verifier" in str(caught.value)
