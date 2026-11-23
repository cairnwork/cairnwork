"""#1382: a verifier root must be ours alone to write; bool config values must be bools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cairnwork import intake as intake_mod
from cairnwork import verifier_root, verifier_select, verify_cli
from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused
from cairnwork.verifier_account import VerifierAccount


def _root(tmp_path: Path, mode: int = 0o750) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(mode)
    return root


def _use(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    account = VerifierAccount(user=name, root=root)
    monkeypatch.setitem(verifier_select.VERIFIER_ACCOUNTS, name, account)
    monkeypatch.setattr(verifier_select.pwd, "getpwnam", lambda _u: object())
    monkeypatch.setattr(intake_mod, "VerifierAccount", lambda: account)  # the implicit default


def _foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    other = os.getuid() + 1  # read before the patch: os.getuid is the one being replaced
    monkeypatch.setattr(verifier_root.os, "getuid", lambda: other)


BAD_ROOTS = ["foreign_owner", "group_writable", "other_writable", "group_and_other_writable"]
NAMES = ["cairnwork-verifier", "cairnwork-verifier2"]  # the default account is in the list


def _bad_root(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    modes = {"group_writable": 0o770, "other_writable": 0o757, "group_and_other_writable": 0o777}
    root = _root(tmp_path, modes.get(kind, 0o750))
    if kind == "foreign_owner":
        _foreign_owner(monkeypatch)
    return root


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("kind", BAD_ROOTS)
def test_select_account_refuses_untrusted_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, kind: str
) -> None:
    _use(monkeypatch, name, _bad_root(kind, tmp_path, monkeypatch))
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account(name)
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT" and "untrusted verifier root" in str(
        caught.value.detail
    )


@pytest.mark.parametrize("mode", [0o700, 0o750, 0o2750, 0o755, 0o555])
@pytest.mark.parametrize("name", NAMES)
def test_trusted_roots_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, mode: int
) -> None:
    root = _root(tmp_path, mode)
    _use(monkeypatch, name, root)
    assert verifier_select.select_account(name).root == root


@pytest.mark.parametrize("kind", BAD_ROOTS)
def test_implicit_default_account_is_checked_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = _bad_root(kind, tmp_path, monkeypatch)
    _use(monkeypatch, "cairnwork-verifier", root)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    with pytest.raises(IntakeRefused, match="BAD_VERIFIER_ACCOUNT"):
        Intake(state).verifier_account()
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))
    assert Intake(state).verifier_account() is None  # off means off: its root is not looked at


def test_missing_default_root_keeps_old_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, "cairnwork-verifier", tmp_path / "absent")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    assert Intake(state).verifier_account() is not None  # nothing to judge; use fails later


@pytest.mark.parametrize("kind", BAD_ROOTS)
def test_cli_refuses_untrusted_root_with_exit_2_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    _use(monkeypatch, "cairnwork-verifier2", _bad_root(kind, tmp_path, monkeypatch))
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    code = verify_cli.main(
        ["--verifier-account", "cairnwork-verifier2", "--state", str(state), "cleanup"]
    )
    captured = capsys.readouterr()
    assert code == 2 and json.loads(captured.out)["error"] == "BAD_VERIFIER_ACCOUNT"
    assert captured.err == ""


@pytest.mark.parametrize("value", ["yes", "false", "", 1, 0, None, [], {}])
@pytest.mark.parametrize("key", ["verifier_account", "require_base_on_main"])
def test_non_bool_config_value_is_refused(tmp_path: Path, key: str, value: Any) -> None:
    (tmp_path / "config.json").write_text(json.dumps({key: value}))
    with pytest.raises(IntakeRefused) as caught:
        Intake(tmp_path).config()
    assert caught.value.code == "BAD_CONFIG" and key in str(caught.value.detail)


def test_cli_refuses_non_bool_verifier_account_with_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, "cairnwork-verifier2", _root(tmp_path))
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": "false"}))
    code = verify_cli.main(
        ["--verifier-account", "cairnwork-verifier2", "--state", str(state), "cleanup"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["error"] == "BAD_CONFIG"


@pytest.mark.parametrize("value", [True, False])
def test_real_bools_still_load(tmp_path: Path, value: bool) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"verifier_account": value, "require_base_on_main": value})
    )
    config = Intake(tmp_path).config()
    assert config["verifier_account"] is value and config["require_base_on_main"] is value


def _non_dir_root(kind: str, tmp_path: Path) -> Path:
    """A root that is not a real directory: lstat sees the link or file itself."""
    root = tmp_path / "root"
    if kind == "dangling_link":
        root.symlink_to(tmp_path / "nowhere")
    elif kind == "regular_file":
        root.write_text("x")
        root.chmod(0o600)
    else:
        target = tmp_path / "target"
        target.mkdir()
        target.chmod(0o777 if kind == "link_to_open_dir" else 0o750)
        root.symlink_to(target)
    return root


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "kind", ["link_to_own_dir", "link_to_open_dir", "dangling_link", "regular_file"]
)
def test_link_or_non_directory_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, kind: str
) -> None:
    _use(monkeypatch, name, _non_dir_root(kind, tmp_path))
    with pytest.raises(IntakeRefused) as caught:
        verifier_select.select_account(name)
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT"
    state = tmp_path / "state"  # the implicit default path has no select_account in front of it
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    with pytest.raises(IntakeRefused) as caught:
        Intake(state).verifier_account()
    assert caught.value.code == "BAD_VERIFIER_ACCOUNT" and "untrusted verifier root" in str(
        caught.value.detail
    )
