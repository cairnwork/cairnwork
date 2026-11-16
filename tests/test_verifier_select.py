from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork import verifier_select, verify_cli
from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused
from cairnwork.verifier_account import VerifierAccount


def _config(state: Path, enabled: bool = True) -> None:
    state.mkdir(exist_ok=True)
    (state / "config.json").write_text(json.dumps({"verifier_account": enabled}))


def test_option_absent_keeps_base_intake_and_default_account(tmp_path: Path) -> None:
    _config(tmp_path)
    intake = Intake(tmp_path)
    assert type(intake) is Intake
    assert intake.verifier_account() == VerifierAccount()


def test_select_second_account_uses_its_root_and_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "second-root"
    root.mkdir()
    account = VerifierAccount(user="cairnwork-verifier2", root=root)
    monkeypatch.setitem(verifier_select.VERIFIER_ACCOUNTS, "cairnwork-verifier2", account)
    monkeypatch.setattr(
        verifier_select.pwd, "getpwnam", lambda user: type("Pw", (), {"pw_uid": 902})()
    )
    selected = verifier_select.select_account("cairnwork-verifier2")
    assert selected == account
    _config(tmp_path / "state")
    intake = verifier_select.SelectedIntake(tmp_path / "state", selected)
    assert intake.verifier_account() == account
    chosen = intake.verifier_account()
    assert chosen is not None
    with chosen.exclusive(timeout=1.0):  # the real lock path: files appear under the new root
        assert (root / ".verifier.lock").exists()
        assert (root / ".verifier.queue").exists()
    assert chosen.user == "cairnwork-verifier2"  # uid lookups go by this user, not the default


def test_unknown_account_is_refused() -> None:
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        verifier_select.select_account("unknown")


def test_parent_component_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="other", root=tmp_path / ".." / "outside"),
    )
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        verifier_select.select_account("cairnwork-verifier2")


def test_symlink_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="other", root=link),
    )
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        verifier_select.select_account("cairnwork-verifier2")


def test_nonexistent_user_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="missing", root=root),
    )

    def missing(_user: str) -> Any:
        raise KeyError("missing")

    monkeypatch.setattr(verifier_select.pwd, "getpwnam", missing)
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        verifier_select.select_account("cairnwork-verifier2")


def test_cli_rejects_bad_name_before_default_account_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> VerifierAccount:
        raise AssertionError("default account accessed")

    monkeypatch.setattr(Intake, "verifier_account", forbidden)
    code = verify_cli.main(
        ["--verifier-account", "bad", "--state", str(tmp_path / "state"), "cleanup"]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "BAD_VERIFIER_ACCOUNT"
