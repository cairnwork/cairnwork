"""Second observer: every residual class falls back to ``unknown:<CLASS>`` when unreadable."""

from types import SimpleNamespace
from typing import Any

from cairnwork import pytest_config
from experimental.second_observer import residual as res


def inp(**over: Any) -> Any:
    base = {"ini_text": "[pytest]\n", "repo": "r", "base": "b", "interpreter": "i", "file": "t.py"}
    return SimpleNamespace(**{**base, **over})


def boom(*a: Any, **k: Any) -> Any:
    raise RuntimeError("unreadable")


def quiet(monkeypatch):
    monkeypatch.setattr(
        pytest_config, "base_settings", lambda *a: SimpleNamespace(strict_args=[], import_mode=None)
    )
    monkeypatch.setattr(pytest_config, "installed_plugins", lambda *a: [])


def test_a_clean_input_has_no_residual(tmp_path, monkeypatch):
    quiet(monkeypatch)
    assert res.residual(inp(), tmp_path) == []


def test_an_unparsable_or_missing_ini_is_unknown(tmp_path, monkeypatch):
    quiet(monkeypatch)
    assert res.residual(inp(ini_text="[pytest\nx"), tmp_path) == ["unknown:INI_KEYS"]
    assert res.residual(inp(ini_text="x = 1\n"), tmp_path) == ["unknown:INI_KEYS"]


def test_unreadable_base_settings_are_unknown_strict_and_import_mode(tmp_path, monkeypatch):
    quiet(monkeypatch)
    monkeypatch.setattr(pytest_config, "base_settings", boom)
    assert res.residual(inp(), tmp_path) == ["unknown:IMPORT_MODE", "unknown:STRICT_ARGS"]


def test_unreadable_plugin_list_is_unknown(tmp_path, monkeypatch):
    quiet(monkeypatch)
    monkeypatch.setattr(pytest_config, "installed_plugins", boom)
    assert res.residual(inp(), tmp_path) == ["unknown:TABLE_PLUGINS"]


def test_an_unparsable_conftest_is_unknown_for_the_three_source_classes(tmp_path, monkeypatch):
    quiet(monkeypatch)
    (tmp_path / "conftest.py").write_text("def (:\n")
    assert res.residual(inp(), tmp_path) == [
        "unknown:CONFTEST_CODE",
        "unknown:IMPORTED_NAMES",
        "unknown:PYTEST_PLUGINS_NONLITERAL",
    ]
