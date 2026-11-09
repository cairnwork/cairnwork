"""Where the base's pytest configuration is found and how one file is parsed (a pure move out of
pytest_config.py, near the 400-line cap). pytest_config imports these; its own names stay as
they were (``pytest_config.PytestConfigError``)."""

from __future__ import annotations

import configparser
import tomllib
from typing import Any

# pytest's discovery order: (file, section or TOML table path, format).
_CANDIDATES = (
    ("pytest.ini", "pytest", "ini"),
    (".pytest.ini", "pytest", "ini"),
    ("pytest.toml", "pytest", "toml"),
    (".pytest.toml", "pytest", "toml"),
    ("pyproject.toml", "tool.pytest.ini_options", "toml"),
    ("pyproject.toml", "tool.pytest", "toml"),
    ("tox.ini", "pytest", "ini"),
    ("setup.cfg", "tool:pytest", "ini"),
)
_ALWAYS_FOUND = ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml")
_INI_FILES, _TOML_FILES = _ALWAYS_FOUND[:2], _ALWAYS_FOUND[2:]


class PytestConfigError(Exception):
    code = "PYTEST_CONFIG_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


def _section(text: str, section: str, form: str, name: str) -> dict[str, Any] | None:
    try:
        if form == "toml":
            table: Any = tomllib.loads(text)
            if name == "pyproject.toml":
                return _pyproject(table, section, name)
            for part in section.split("."):
                table = table.get(part) if isinstance(table, dict) else None
            return dict(table) if isinstance(table, dict) else None
        # #928: [DEFAULT] is a plain section, as pytest's iniconfig reads it (by default
        # configparser merges its keys into every section, [pytest] too)
        parser = configparser.ConfigParser(interpolation=None, default_section="\0")
        parser.optionxform = str  # type: ignore[assignment] # pytest's ini keys are case-sensitive
        parser.read_string(text.removeprefix("\ufeff"))
    except (tomllib.TOMLDecodeError, configparser.Error) as exc:
        raise PytestConfigError(f"{name} at the base cannot be parsed") from exc
    return dict(parser.items(section)) if parser.has_section(section) else None


def _pyproject(doc: Any, section: str, name: str) -> dict[str, Any] | None:
    """#928: pyproject.toml as pytest 9 reads it. [tool.pytest] counts only with a key besides
    ``ini_options`` (an empty table is not a config: discovery goes on to tox.ini, setup.cfg)
    and then wins over an empty ``ini_options``; with both non-empty, pytest stops with a usage
    error, so the base is refused."""
    tool = doc.get("tool")
    tool_pytest = tool.get("pytest") if isinstance(tool, dict) else None
    if not isinstance(tool_pytest, dict):
        return None
    native = {k: v for k, v in tool_pytest.items() if k != "ini_options"}
    ini = tool_pytest.get("ini_options")
    if native and ini:  # as pytest tests it: an empty ini_options table beside them is no error
        raise PytestConfigError(f"{name} sets both [tool.pytest] and [tool.pytest.ini_options]")
    if section == "tool.pytest":
        return native or None
    return dict(ini) if isinstance(ini, dict) and not native else None  # native keys come first


def refuse_both_kinds(names) -> None:
    """#928: pytest 9 reads pytest.toml before pytest.ini, an older pytest only pytest.ini; with
    one of each at the base, which one holds depends on the version, so the base is refused."""
    if any(n in names for n in _TOML_FILES) and any(n in names for n in _INI_FILES):
        raise PytestConfigError("a pytest.toml and a pytest.ini at the base: one would be ignored")
