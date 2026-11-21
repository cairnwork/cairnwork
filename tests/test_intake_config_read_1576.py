"""#1576: config.json is absent only when lstat says so; refusal paths go through shown()."""

from pathlib import Path

import pytest

from cairnwork.intake import Intake, IntakeRefused


def _state(tmp_path, name="state"):
    state = tmp_path / name
    state.mkdir(mode=0o700)
    return state


def test_an_lstat_failure_other_than_absent_is_bad_config_not_defaults(tmp_path, monkeypatch):
    state = _state(tmp_path)
    (state / "config.json").write_text("{}")
    real = Path.lstat

    def denied(self, *args, **kwargs):
        if self.name == "config.json":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert info.value.code == "BAD_CONFIG"
    assert "could not be read" in info.value.detail
    assert "not valid JSON" not in info.value.detail


def test_a_path_with_control_characters_is_escaped_in_the_refusal(tmp_path):
    state = _state(tmp_path, "st\n::error::x")
    (state / "config.json").write_text('{"nope": 1}')
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert info.value.code == "BAD_CONFIG"
    assert "\n" not in info.value.detail
    assert "\\x0a::error::x" in info.value.detail


def test_the_unreadable_and_invalid_json_refusals_also_escape_the_path(tmp_path):
    state = _state(tmp_path, "st\n::error::y")
    (state / "config.json").write_text("{not json")
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert "\n" not in info.value.detail and "\\x0a::error::y" in info.value.detail
    assert "not valid JSON" in info.value.detail


def test_an_absent_config_still_gives_defaults(tmp_path):
    assert Intake(_state(tmp_path)).config()


def test_a_dangling_link_with_an_odd_name_is_refused_with_the_text_escaped(tmp_path):
    state = _state(tmp_path, "st\n::error::z")
    (state / "config.json").symlink_to(tmp_path / "gone\n::error::w.json")
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert info.value.code == "BAD_CONFIG"
    assert "could not be read: '[Errno 2]" in info.value.detail  # the exception text is quoted too
    assert "\n" not in info.value.detail
    assert "\\x0a::error::z" in info.value.detail
