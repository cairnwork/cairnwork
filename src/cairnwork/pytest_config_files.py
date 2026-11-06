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


class PytestConfigError(Exception):
    code = "PYTEST_CONFIG_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


def _section(text: str, section: str, form: str, name: str) -> dict[str, Any] | None:
    try:
        if form == "toml":
            table: Any = tomllib.loads(text)
            for part in section.split("."):
                table = table.get(part) if isinstance(table, dict) else None
            return dict(table) if isinstance(table, dict) else None
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str  # type: ignore[assignment] # pytest's ini keys are case-sensitive
        parser.read_string(text)
    except (tomllib.TOMLDecodeError, configparser.Error) as exc:
        raise PytestConfigError(f"{name} at the base cannot be parsed") from exc
    return dict(parser.items(section)) if parser.has_section(section) else None
