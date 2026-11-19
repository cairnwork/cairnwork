"""#1324: exception text that reaches a refusal is escaped (newline, ::error::, bidi)."""

import json

import pytest

from cairnwork import intake as intake_mod
from cairnwork import verify_candidate_checks as checks
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.pipeline import TaskRejected

HOSTILE = "\n::error::x‮"


def _assert_escaped(text: str) -> None:
    assert "\\x0a" in text and "\\u202e" in text
    assert "\n" not in text and "‮" not in text


def _raiser(exc):
    def raise_it(*_args, **_kwargs):
        raise exc

    return raise_it


def test_verifier_environment_error_text_is_escaped(monkeypatch):
    monkeypatch.setattr(checks, "check_task", _raiser(OSError(HOSTILE)))
    with pytest.raises(TaskRejected) as info:
        checks.check_task_for_verify(object(), "python")
    assert info.value.code == "VERIFIER_ENVIRONMENT_ERROR"
    _assert_escaped(str(info.value))


def test_bad_import_path_text_is_escaped(monkeypatch):
    monkeypatch.setattr(checks, "check_verify_import_paths", _raiser(ValueError(HOSTILE)))
    with pytest.raises(TaskRejected) as info:
        checks.validate_import_paths(None, "base", [], ())
    assert info.value.code == "BAD_IMPORT_PATH"
    _assert_escaped(str(info.value))


def test_bad_config_text_is_escaped(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text("{}")
    monkeypatch.setattr(intake_mod.json, "loads", _raiser(ValueError(HOSTILE)))
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert info.value.code == "BAD_CONFIG"
    _assert_escaped(str(info.value))
