"""What runs around a test besides its own body: module-level code, pytest hooks and plugins, and
the project's pytest configuration. Any of it can change what a test sees (or what is collected),
and a blackbox case repeats only the call, so it refuses the tests in its scope. Read statically:
config files are parsed, never executed.
"""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from pathlib import Path

from .resolve import dotted

COUNT_HOOKS = {  # change what is collected
    "pytest_generate_tests",
    "pytest_collection_modifyitems",
    "pytest_pycollect_makeitem",
    "pytest_collect_file",
    "pytest_ignore_collect",
    "pytest_collection",
}
HARMLESS_HOOKS = {
    "pytest_addoption",
    "pytest_report_header",
    "pytest_assertrepr_compare",
    "pytest_make_parametrize_id",
}
COMPOUND = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While, ast.Match)
COLLECTION_KEYS = ("python_files", "python_classes", "python_functions")


def _calls_outside_pytest(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and (dotted(n.func) or [""])[0] not in ("pytest", "mark")
        for n in ast.walk(node)
    )


def import_time(tree: ast.Module) -> tuple[str | None, bool]:
    """(reason, count_known): code that runs when pytest imports the module (calls, attribute or
    item assignments, plugins, hooks, compound statements) can change what every test sees, or
    what is collected."""
    reason, known = None, True
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in COUNT_HOOKS:
                reason, known = reason or f"hook {node.name} changes what is collected", False
            elif node.name.startswith("pytest_") and node.name not in HARMLESS_HOOKS:
                reason = reason or f"hook {node.name} runs around the tests"
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "pytest_plugins" in names:
                reason = reason or "pytest_plugins loads plugins (fixtures, hooks)"
            elif len(names) != len(targets):
                reason = reason or "module-level assignment to an attribute or item"
            elif node.value is not None and _calls_outside_pytest(node.value):
                reason = reason or "module-level call at import time"
            known = known and not any(n.startswith("test") for n in names)  # test_b = test_a
        elif isinstance(node, COMPOUND):
            reason = reason or f"module-level {type(node).__name__} at import time"
            known = False  # it may define tests
        elif isinstance(node, ast.Expr):
            if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                reason = reason or "module-level statement at import time"
        elif not isinstance(node, ast.Import | ast.ImportFrom | ast.ClassDef):
            reason = reason or f"module-level {type(node).__name__} at import time"
    return reason, known


def pytest_config(root: Path) -> tuple[str | None, bool]:
    """(reason, count_known) from the project's pytest configuration (the first file pytest
    would use: pytest.ini, .pytest.ini, pyproject.toml, tox.ini, setup.cfg)."""
    options = _options(root)
    if options is None:
        return "unreadable pytest configuration", False
    known = not any(key in options for key in COLLECTION_KEYS)
    text = {k: " ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in options.items()}
    if re.search(r"(^|\s|')error\b", text.get("filterwarnings", "")):
        return "pytest config turns warnings into errors (filterwarnings)", known
    if text.get("usefixtures", "").strip():
        return "pytest config applies fixtures to every test (usefixtures)", known
    if text.get("required_plugins", "").strip():
        return "pytest config requires plugins", known
    if re.search(r"(^|\s)(-p|-W|--pythonwarnings|-c|--override-ini|-o)\b", text.get("addopts", "")):
        return "pytest addopts loads plugins or changes warnings or options", known
    return None, known


def _options(root: Path) -> dict | None:
    try:
        for name in ("pytest.ini", ".pytest.ini"):
            if (root / name).is_file():
                return _ini(root / name, ("pytest",)) or {}
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            found = data.get("tool", {}).get("pytest", {}).get("ini_options")
            if isinstance(found, dict):
                return found
        for name, sections in (("tox.ini", ("pytest",)), ("setup.cfg", ("tool:pytest",))):
            if (root / name).is_file():
                found = _ini(root / name, sections)
                if found is not None:
                    return found
    except (OSError, ValueError, configparser.Error, UnicodeDecodeError):
        return None
    return {}


def _ini(path: Path, sections: tuple[str, ...]) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(path.read_text(encoding="utf-8"))
    for section in sections:
        if parser.has_section(section):
            return dict(parser.items(section))
    return None
