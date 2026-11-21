"""#1381: a dangling or looping config.json symlink is BAD_CONFIG, never an empty config."""

import pytest

from cairnwork.intake import Intake, IntakeRefused


def _config_link(tmp_path, target):
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").symlink_to(target)
    return Intake(state)


def test_dangling_config_symlink_is_bad_config(tmp_path):
    intake = _config_link(tmp_path, tmp_path / "missing.json")
    with pytest.raises(IntakeRefused, match="BAD_CONFIG"):
        intake.config()


def test_looping_config_symlink_is_bad_config(tmp_path):
    intake = _config_link(tmp_path, "config.json")
    with pytest.raises(IntakeRefused, match="BAD_CONFIG"):
        intake.config()


def test_absent_config_still_gives_defaults(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    assert Intake(state).config()
