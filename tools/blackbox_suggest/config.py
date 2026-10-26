"""The project's pytest configuration, read statically, as an allow-list.

pytest takes its options from the first config file it finds (pytest.ini, .pytest.ini,
pytest.toml, .pytest.toml, pyproject.toml, tox.ini, setup.cfg) in the directories it searches,
and options can change every test: warnings as errors, fixtures applied to every test, plugins.
Rather than guess which file pytest picks, every such file from the test's directory up to the
project root is read, and each must hold only options known to leave a test's outcome alone.
An unknown option (a plugin's, say), an unsafe value or an unreadable file refuses the tests.
"""

from __future__ import annotations

import configparser
import shlex
import tomllib
from pathlib import Path
from typing import Any

SAFE_KEYS = {
    "minversion", "testpaths", "norecursedirs", "markers", "xfail_strict",
    "console_output_style", "cache_dir", "pythonpath", "consider_namespace_packages",
    "log_cli", "log_level", "log_format", "log_date_format", "log_cli_level", "log_cli_format",
    "log_cli_date_format", "log_file", "log_file_level", "log_file_format", "log_file_date_format",
    "log_auto_indent", "junit_family", "junit_suite_name", "junit_logging",
    "junit_log_passing_tests", "junit_duration_report", "doctest_optionflags",
    "doctest_encoding", "faulthandler_timeout", "verbosity_assertions", "verbosity_test_cases",
    "truncation_limit_lines", "truncation_limit_chars", "tmp_path_retention_count",
    "tmp_path_retention_policy", "empty_parameter_set_mark", "strict_markers", "strict_config",
    "strict", "strict_parametrization_ids", "strict_xfail",
}  # fmt: skip
COLLECTION_KEYS = {"python_files", "python_classes", "python_functions", "collect_imported_tests"}
SAFE_FLAGS = {
    "-q", "-qq", "-v", "-vv", "-vvv", "-l", "--showlocals", "--strict-markers", "--strict-config",
    "--strict", "--no-header", "--no-summary", "-s", "--capture=no", "--capture=fd",
    "--capture=sys", "--doctest-modules", "--import-mode=importlib", "--import-mode=prepend",
    "--import-mode=append",
}  # fmt: skip
SAFE_PREFIXES = (
    "-r", "--tb=", "--color=", "--durations=", "--durations-min=", "--code-highlight=",
    "--cov=", "--cov-report=", "--cov-config=", "--cov-context=", "--doctest-glob=",
)  # fmt: skip
SAFE_FLAGS |= {  # reporting only: coverage tracing (pytest-cov), verbosity, warning summaries
    "--verbose", "--cov", "--cov-append", "--cov-branch", "--no-cov", "--no-cov-on-fail",
    "--disable-warnings", "--disable-pytest-warnings",
}  # fmt: skip
WITH_VALUE = {"--cov-report", "--doctest-report"}  # safe options whose value is the next token
SELECTING = ("-m", "-k", "--deselect", "--ignore", "--ignore-glob")  # change what is collected
FILTER_ACTIONS = ("ignore", "default", "always", "module", "once")
SECTIONS = (
    ("pytest.ini", "ini", "pytest"),
    (".pytest.ini", "ini", "pytest"),
    ("pytest.toml", "toml", "pytest"),
    (".pytest.toml", "toml", "pytest"),
    ("pyproject.toml", "toml", "tool.pytest.ini_options"),
    ("pyproject.toml", "toml", "tool.pytest"),
    ("tox.ini", "ini", "pytest"),
    ("setup.cfg", "ini", "tool:pytest"),
)


def pytest_config(test_dir: Path, root: Path) -> tuple[str | None, bool, list[str]]:
    """(reason, count_known, usefixtures) over every config file from test_dir up to root."""
    reason, known, fixtures = None, True, []
    for directory in _dirs(test_dir, root):
        for name, kind, section in SECTIONS:
            try:
                options = _section(directory / name, kind, section)
            except (OSError, ValueError, configparser.Error, UnicodeDecodeError):
                return "unreadable pytest configuration", False, fixtures
            if options is None:
                continue
            for key, value in options.items():
                problem, count_known, names = _option(key, value)
                reason = reason or problem
                known = known and count_known
                fixtures += names
    return reason, known, fixtures


def _dirs(test_dir: Path, root: Path) -> list[Path]:
    found, directory, top = [], test_dir.resolve(), root.resolve()
    while True:
        found.append(directory)
        if directory == top or top not in directory.parents:
            return found
        directory = directory.parent


def _section(path: Path, kind: str, section: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    if kind == "ini":
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(path.read_text(encoding="utf-8"))
        return dict(parser.items(section)) if parser.has_section(section) else None
    data: Any = tomllib.loads(path.read_text(encoding="utf-8"))
    for part in section.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if not isinstance(v, dict)}  # [tool.pytest.ini_options]


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _option(key: str, value: Any) -> tuple[str | None, bool, list[str]]:
    """(problem, count_known, usefixtures names) for one option."""
    if key in COLLECTION_KEYS:
        return None, False, []
    if key == "usefixtures":
        names = [n for v in _values(value) for n in v.split()]
        return "pytest config applies fixtures to every test (usefixtures)", True, names
    if key == "filterwarnings":
        actions = [v.split(":", 1)[0].strip() for v in _values(value)]
        if all(a in FILTER_ACTIONS for a in actions):
            return None, True, []
        return "pytest config turns warnings into errors (filterwarnings)", True, []
    if key == "addopts":
        try:
            tokens = [t for v in _values(value) for t in shlex.split(v)]
        except ValueError:
            return "pytest addopts cannot be read", False, []
        return _addopts(tokens)
    if key in SAFE_KEYS:
        return None, True, []
    return "pytest config option not known to be inert", True, []


def _addopts(tokens: list[str]) -> tuple[str | None, bool, list[str]]:
    """Reporting options pass; selection options make the count unknown; anything else (a
    plugin loaded with -p, -W, --cov-fail-under, -x, ...) refuses."""
    index, known = 0, True
    while index < len(tokens):
        token = tokens[index]
        has_next = index + 1 < len(tokens)
        if token == "-p" and has_next and tokens[index + 1].startswith("no:"):
            index += 2
        elif token in WITH_VALUE and has_next:
            index += 2
        elif token in SELECTING and has_next:
            index, known = index + 2, False
        elif token.startswith(tuple(f"{s}=" for s in SELECTING if s.startswith("--"))):
            index, known = index + 1, False
        elif token in SAFE_FLAGS or token.startswith(SAFE_PREFIXES):
            index += 1
        else:
            return "pytest addopts loads plugins or changes warnings or options", False, []
    return None, known, []
