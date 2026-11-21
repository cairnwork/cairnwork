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


def _use_account(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Whitelist entry with an EXISTING user (lookup patched) so only the root can refuse."""
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="cairnwork-verifier2", root=root),
    )
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _u: object())


def _root_shapes(tmp_path: Path) -> dict[str, Path]:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "file").write_text("x")
    (tmp_path / "lnk").symlink_to(real, target_is_directory=True)
    (tmp_path / "parentlink").symlink_to(tmp_path, target_is_directory=True)
    return {
        "relative": Path("rel/root"),
        "dotdot": real / ".." / "real",
        "symlink_root": tmp_path / "lnk",
        "symlink_parent": tmp_path / "parentlink" / "real",
        "missing": tmp_path / "missing",
        "not_a_directory": tmp_path / "file",
    }


def test_good_root_with_existing_user_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root_shapes(tmp_path)
    _use_account(monkeypatch, tmp_path / "real")
    assert verifier_select.select_account("cairnwork-verifier2").root == tmp_path / "real"


@pytest.mark.parametrize(
    "shape",
    ["relative", "dotdot", "symlink_root", "symlink_parent", "missing", "not_a_directory"],
)
def test_root_shapes_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    root = _root_shapes(tmp_path)[shape]
    _use_account(monkeypatch, root)
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        verifier_select.select_account("cairnwork-verifier2")


def test_parent_component_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_account(monkeypatch, _root_shapes(tmp_path)["dotdot"])
    with pytest.raises(IntakeRefused, match="invalid verifier root"):
        verifier_select.select_account("cairnwork-verifier2")


def test_symlink_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_account(monkeypatch, _root_shapes(tmp_path)["symlink_root"])
    with pytest.raises(IntakeRefused, match="unsafe verifier root"):
        verifier_select.select_account("cairnwork-verifier2")


def test_unknown_name_never_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _root_shapes(tmp_path)
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier",
        VerifierAccount(user="cairnwork-verifier", root=tmp_path / "real"),
    )
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _u: object())
    for name in ["", "cairnwork-verifier3", "Cairnwork-Verifier2", "cairnwork-verifier2 "]:
        with pytest.raises(IntakeRefused, match="unknown verifier account"):
            verifier_select.select_account(name)


def test_whitelist_accounts_are_distinct() -> None:
    accounts = verifier_select.VERIFIER_ACCOUNTS
    assert accounts["cairnwork-verifier"] == VerifierAccount()
    first, second = accounts["cairnwork-verifier"], accounts["cairnwork-verifier2"]
    assert first.user != second.user
    assert first.root != second.root
    assert not first.root.is_relative_to(second.root)
    assert not second.root.is_relative_to(first.root)


def test_cli_run_passes_selected_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root_shapes(tmp_path)["symlink_root"].parent / "real"
    _use_account(monkeypatch, root)
    state = tmp_path / "state"
    _config(state)
    seen: list[Any] = []

    def fake_verify(intake: Any, **_kwargs: Any) -> dict[str, Any]:
        seen.append(intake)
        return {"verified": True}

    monkeypatch.setattr(verify_cli, "verify_candidate", fake_verify)
    code = verify_cli.main(
        ["--verifier-account", "cairnwork-verifier2", "--state", str(state), "run",
         "--repo-path", "r", "--base", "a", "--candidate", "b",
         "--allowed-file", "f", "--verifier", "v"]
    )  # fmt: skip
    capsys.readouterr()
    assert code == 0
    assert len(seen) == 1
    assert seen[0].verifier_account() == VerifierAccount(user="cairnwork-verifier2", root=root)


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


def test_cli_refuses_explicit_account_when_config_disables_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="cairnwork-verifier2", root=root),
    )
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _u: object())
    state = tmp_path / "state"
    _config(state, enabled=False)
    code = verify_cli.main(
        ["--verifier-account", "cairnwork-verifier2", "--state", str(state), "cleanup"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["error"] == "VERIFIER_ACCOUNT_NOT_ENABLED"


def test_cli_returns_json_when_selected_account_config_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setitem(
        verifier_select.VERIFIER_ACCOUNTS,
        "cairnwork-verifier2",
        VerifierAccount(user="cairnwork-verifier2", root=root),
    )
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _u: object())

    def unreadable(_self: Intake) -> dict[str, Any]:
        raise PermissionError("config is unreadable")

    monkeypatch.setattr(Intake, "config", unreadable)
    code = verify_cli.main(
        ["--verifier-account", "cairnwork-verifier2", "--state", str(tmp_path), "cleanup"]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert json.loads(captured.out)["error"] == "BAD_CONFIG"
    assert captured.err == ""
