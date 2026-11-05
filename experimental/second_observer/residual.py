"""Known sources of false disagreement, recorded per row and never used to change a verdict.

The observer is deliberately independent of pytest: it does not imitate these behaviours. The
row lists which of them are present so a pilot can stratify disagreements. When an item cannot
be read the row says ``unknown:<CLASS>`` instead of guessing."""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

from cairnwork import pytest_config
from experimental.second_observer.inputs import ShadowInput

NEUTRAL_INI = frozenset(
    {
        "testpaths", "python_files", "python_classes", "python_functions", "markers",
        "norecursedirs", "minversion", "pythonpath", "console_output_style", "cache_dir",
    }
)  # fmt: skip
INI_KEYS, STRICT_ARGS, TABLE_PLUGINS = "INI_KEYS", "STRICT_ARGS", "TABLE_PLUGINS"
IMPORTED_NAMES, CONFTEST_CODE = "IMPORTED_NAMES", "CONFTEST_CODE"
PLUGINS_NONLITERAL, IMPORT_MODE = "PYTEST_PLUGINS_NONLITERAL", "IMPORT_MODE"
# Version of the classification rules above; each row records it so old and new rows compare.
# 2: IMPORTED_NAMES only for a from-import in a conftest or a star-import anywhere (#883).
RULES_VERSION = 2


def _unknown(label: str) -> str:
    return f"unknown:{label}"


def _ini_keys(ini_text: str) -> list[str] | None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read_string(ini_text)
    except configparser.Error:
        return None
    return list(parser["pytest"]) if parser.has_section("pytest") else None


def _is_literal_plugins(value: ast.AST) -> bool:
    items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
    return all(isinstance(i, ast.Constant) and isinstance(i.value, str) for i in items)


def _module_flags(tree: ast.Module, conftest: bool) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if conftest or any(alias.name == "*" for alias in node.names):
            found.add(IMPORTED_NAMES)
    for node in tree.body:
        names = []
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if "pytest_plugins" in names and not (
            isinstance(node, ast.Assign) and _is_literal_plugins(node.value)
        ):
            found.add(PLUGINS_NONLITERAL)
        if not conftest:
            continue
        docstring = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        plain = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        if not (isinstance(node, plain) or docstring):
            found.add(CONFTEST_CODE)
    return found


def _files(checkout: Path, test_file: str) -> list[tuple[Path, bool]]:
    files, directory = [(checkout / "conftest.py", True)], checkout
    for part in Path(test_file).parent.parts:
        directory = directory / part
        files.append((directory / "conftest.py", True))
    return [*files, (checkout / test_file, False)]


def residual(inp: ShadowInput, checkout: Path) -> list[str]:
    out: list[str] = []
    keys = _ini_keys(inp.ini_text)
    if keys is None:
        out.append(_unknown(INI_KEYS))
    elif set(keys) - NEUTRAL_INI:
        out.append(INI_KEYS)
    try:
        settings = pytest_config.base_settings(inp.repo, inp.base)
        if settings.strict_args:
            out.append(STRICT_ARGS)
        if settings.import_mode:
            out.append(IMPORT_MODE)
    except Exception:  # noqa: BLE001 - an unreadable item is recorded, not guessed
        out += [_unknown(STRICT_ARGS), _unknown(IMPORT_MODE)]
    try:
        installed = set(pytest_config.installed_plugins(inp.interpreter))
        if installed & {module for _, module, _ in pytest_config.PLUGINS}:
            out.append(TABLE_PLUGINS)
    except Exception:  # noqa: BLE001
        out.append(_unknown(TABLE_PLUGINS))
    flags: set[str] = set()
    try:
        for path, is_conftest in _files(checkout, inp.file):
            if path.is_file():
                flags |= _module_flags(ast.parse(path.read_bytes()), is_conftest)
    except (OSError, SyntaxError, ValueError):
        out += [_unknown(IMPORTED_NAMES), _unknown(CONFTEST_CODE), _unknown(PLUGINS_NONLITERAL)]
    return sorted({*out, *flags})
