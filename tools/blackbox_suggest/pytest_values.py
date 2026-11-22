"""Configuration pytest will not load (#370): text, types and values it refuses at start.

A configuration pytest refuses runs no test, so a draft under it protects nothing. configparser
and TOML read more than pytest does: pytest reads ini files with iniconfig, takes no coercion in
a native TOML table, and checks some values of options otherwise known to be inert.
"""

from __future__ import annotations

import importlib.metadata
import logging
import re
from typing import Any

LISTS = {
    "testpaths", "norecursedirs", "doctest_optionflags", "python_files", "python_classes",
    "python_functions", "usefixtures", "addopts", "markers", "filterwarnings", "pythonpath",
}  # fmt: skip
BOOLS = {
    "xfail_strict", "log_cli", "consider_namespace_packages", "junit_log_passing_tests", "strict",
    "strict_config", "strict_markers", "strict_parametrization_ids", "strict_xfail",
    "collect_imported_tests",
}  # fmt: skip
TRUTH = {"y", "yes", "t", "true", "on", "1", "n", "no", "f", "false", "off", "0"}
LEVELS = {"log_level", "log_cli_level", "log_file_level"}
COUNTS = {"tmp_path_retention_count", "truncation_limit_lines", "truncation_limit_chars"}
CHOICES = {
    "empty_parameter_set_mark": {"", "skip", "xfail", "fail_at_collect"},
    "tmp_path_retention_policy": {"all", "failed", "none"},
}


def ini_refused(text: str) -> bool:
    """Lines iniconfig refuses although configparser reads them: a section line with more after
    its closing bracket, or an indented line with no value above it in its section."""
    has_value = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip()[:1] in ("#", ";"):
            continue
        if line[0] == "[":
            if not re.split("[#;]", line)[0].rstrip().endswith("]"):
                return True
            has_value = False
        elif line[0].isspace():
            if not has_value:
                return True
        else:
            has_value = True
    return False


def toml_refused(data: dict[str, Any], native: bool, known: set[str]) -> bool:
    """A known option whose TOML type pytest refuses (a native table takes no coercion)."""
    for key, value in data.items():
        if key not in known | LISTS | BOOLS:
            continue  # refused later as an unknown option
        if isinstance(value, list):
            ok = key in LISTS and all(isinstance(v, str) for v in value)
        elif key in LISTS:
            ok = not native and isinstance(value, str)
        elif key in BOOLS:
            ok = isinstance(value, bool) or (not native and isinstance(value, str))
        else:
            ok = isinstance(value, str) or (not native and isinstance(value, (int, float)))
        if not ok:
            return True
    return False


def accepted(key: str, value: Any) -> bool:
    """Whether pytest accepts this value (an inline comment is part of an ini value)."""
    if isinstance(value, list):
        return True
    text = str(value).strip()
    if key in BOOLS:
        return text.lower() in TRUTH
    if key == "minversion":
        return not text or _minversion(text)
    if key in LEVELS:
        level = getattr(logging, text.upper(), text.upper())
        return not text or isinstance(level, int) or bool(re.fullmatch(r"\d+", str(level)))
    if key in COUNTS:
        return text.isdigit()
    if key == "faulthandler_timeout":
        return bool(re.fullmatch(r"\d+(\.\d*)?|\.\d+", text))
    return text in CHOICES.get(key, {text})


def _minversion(text: str) -> bool:
    try:  # packaging comes with pytest; without either the version cannot be compared
        from packaging.version import Version

        return Version(text) <= Version(importlib.metadata.version("pytest"))
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        return False  # InvalidVersion is a ValueError
