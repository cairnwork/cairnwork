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
import re
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any

from tools.blackbox_suggest.pytest_values import (
    accepted,
    beyond_installed,
    ini_refused,
    toml_refused,
    warning_filter,
)

SAFE_KEYS = {
    "minversion", "testpaths", "norecursedirs", "markers", "xfail_strict",
    "console_output_style", "cache_dir", "pythonpath", "consider_namespace_packages",
    "log_cli", "log_level", "log_format", "log_date_format", "log_cli_level", "log_cli_format",
    "log_cli_date_format", "log_file_level", "log_file_format", "log_file_date_format",
    "log_auto_indent", "junit_family", "junit_suite_name", "junit_logging",
    "junit_log_passing_tests", "junit_duration_report", "doctest_optionflags",
    "doctest_encoding", "faulthandler_timeout", "verbosity_assertions", "verbosity_test_cases",
    "truncation_limit_lines", "truncation_limit_chars", "tmp_path_retention_count",
    "tmp_path_retention_policy", "empty_parameter_set_mark", "strict_markers", "strict_config",
    "strict", "strict_parametrization_ids", "strict_xfail",
}  # fmt: skip
COLLECTION_KEYS = {"python_files", "python_classes", "python_functions", "collect_imported_tests"}
TEST_FILES = {"test_*.py", "*_test.py"}  # pytest's default python_files
PYTEST_PLUGINS = {  # pytest's own plugins: -p no: on one changes how every test runs
    "pytest", "_pytest", "assertion", "capture", "debugging", "doctest", "faulthandler",
    "fixtures", "helpconfig", "junitxml", "legacypath", "logging", "main", "mark",
    "monkeypatch", "pastebin", "python", "recwarn", "reports", "runner", "setuponly",
    "setupplan", "skipping", "stepwise", "subtests", "terminal", "threadexception", "tmpdir",
    "unittest", "unraisableexception", "warnings", "python_path", "nose",
}  # fmt: skip
SAFE_FLAGS = {
    "-q", "-qq", "-v", "-vv", "-vvv", "-l", "--showlocals", "--strict-markers", "--strict-config",
    "--strict", "--no-header", "--no-summary", "-s", "--capture=no", "--capture=fd",
    "--capture=sys", "--import-mode=importlib", "--import-mode=prepend",
    "--import-mode=append",
}  # fmt: skip
SAFE_PREFIXES = (
    "--tb=", "--color=", "--durations=", "--durations-min=", "--code-highlight=",
    "--cov=", "--cov-report=", "--cov-config=", "--cov-context=",
)  # fmt: skip
SAFE_FLAGS |= {  # reporting only: coverage tracing (pytest-cov), verbosity, warning summaries
    "--verbose", "--cov", "--cov-append", "--cov-branch", "--no-cov", "--no-cov-on-fail",
    "--disable-warnings", "--disable-pytest-warnings",
}  # fmt: skip
WITH_VALUE = {"--cov-report", "--doctest-report"}  # safe options whose value is the next token
DOCTESTS = ("--doctest-modules", "--doctest-glob")  # collect doctests: items and code unread
SELECTING = ("-m", "-k", "--deselect", "--ignore", "--ignore-glob")  # change what is collected
REPORT = re.compile(r"-r[fEsxXpPaAN]+")  # -r with summary letters only (not -random-order)
VALUES: dict[str, Any] = {  # pytest's own options whose value stops pytest when bad (#370)
    "--tb": "auto|long|short|line|native|no", "--color": "yes|no|auto",
    "--code-highlight": "yes|no",
    "--durations": int, "--durations-min": float,  # read with int() / float() as pytest does
    "--doctest-report": "udiff|cdiff|ndiff|only_first_failure|none",
}  # fmt: skip
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


def strict_ids(test_dir: Path, root: Path) -> bool:
    """Whether a config file around the test makes a repeated parametrize id a collection error:
    ``strict_parametrization_ids`` on, or ``strict`` (or ``--strict`` in addopts) on in a file that
    does not set ``strict_parametrization_ids`` itself."""
    for directory in _dirs(test_dir, root):
        for name, kind, section in SECTIONS:
            try:
                options = _section(directory / name, kind, section) or {}
                tokens = [t for v in _values(options.get("addopts", "")) for t in shlex.split(v)]
            except (OSError, ValueError, configparser.Error, UnicodeDecodeError):
                return True
            own = options.get("strict_parametrization_ids")
            if _on(own) or (own is None and (_on(options.get("strict")) or "--strict" in tokens)):
                return True
    return False


def _on(value: Any) -> bool:
    return value is not None and str(value).strip().lower() in {"y", "yes", "t", "true", "on", "1"}


def import_dirs(test_dir: Path, root: Path) -> list[Path]:
    """Directories pytest may put on ``sys.path`` for test_dir: each one up to root (the first
    without ``__init__.py`` under prepend import mode, or root's parent when root has one) and
    every ``pythonpath`` entry of the config files read above, relative to the file's directory.
    """
    found = _dirs(test_dir, root)
    if (root / "__init__.py").is_file():
        found.append(root.resolve().parent)
    for directory in _dirs(test_dir, root):
        for name, kind, section in SECTIONS:
            try:
                options = _section(directory / name, kind, section) or {}
            except (OSError, ValueError, configparser.Error, UnicodeDecodeError):
                continue  # pytest_config refuses an unreadable file
            found += [directory / e for e in _paths(options.get("pythonpath", []))]
    return found


NORECURSE = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}")


def recursion_skips(test_dir: Path, root: Path) -> tuple[str, ...]:
    """Directory names pytest does not collect from: its default ``norecursedirs``, or none at
    all when a config file read above sets its own (every directory then counts)."""
    for directory in _dirs(test_dir, root):
        for name, kind, section in SECTIONS:
            try:
                options = _section(directory / name, kind, section) or {}
            except (OSError, ValueError, configparser.Error, UnicodeDecodeError):
                return ()  # pytest_config refuses an unreadable file
            if "norecursedirs" in options:
                return ()
    return NORECURSE


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
        text = path.read_text(encoding="utf-8")
        if ini_refused(text):  # pytest reads the whole file with iniconfig, which refuses it
            raise ValueError("a line pytest cannot read")
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str  # type: ignore[assignment]  # keep case as pytest does
        parser.read_string(text)
        if parser.has_section(section):
            options = dict(parser.items(section))
            if any(key != key.lower() for key in options):  # pytest: unknown, or stops (#370)
                raise ValueError("an option name pytest reads with its case")
            return options
        if parser.has_section("pytest"):  # setup.cfg: pytest refuses to start (#370, as #947)
            raise ValueError("a [pytest] section in setup.cfg")
        return None
    data: Any = tomllib.loads(path.read_text(encoding="utf-8"))
    for part in section.split("."):
        if not isinstance(data, dict):  # pytest cannot load a table of the wrong type (#947)
            raise ValueError(f"{section} is not a table")
        data = data.get(part)
        if data is None:
            return None
    if not isinstance(data, dict):
        raise ValueError(f"{section} is not a table")
    if toml_refused(data, section != "tool.pytest.ini_options", SAFE_KEYS):
        raise ValueError(f"{section} holds a value of a type pytest cannot load")
    if section == "tool.pytest":
        native = {k: v for k, v in data.items() if k != "ini_options"}  # read as its own section
        if native and data.get("ini_options"):  # pytest stops with a usage error
            raise ValueError("native [tool.pytest] beside [tool.pytest.ini_options]")
        data = native
    return data  # a sub-table (a plugin's [tool.pytest.ini_options.env]) is an unknown option


def _paths(value: Any) -> list[str]:
    """``pythonpath`` entries as pytest reads them, a list item whole and a string split like a
    shell, and each part of those split on whitespace, as the reader used to (#370)."""
    try:
        whole = [str(v) for v in value] if isinstance(value, list) else shlex.split(str(value))
    except ValueError:  # an unclosed quote: pytest stops
        whole = []
    return [*whole, *(e for v in _values(value) for e in v.split())]


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _option(key: str, value: Any) -> tuple[str | None, bool, list[str]]:
    """(problem, count_known, usefixtures names) for one option."""
    if not accepted(key, value):  # pytest refuses to start, so no test runs
        return "pytest config holds a value pytest refuses at start", False, []
    if key == "python_files":  # a module collected as tests is imported: code the reader skips
        if {p for v in _values(value) for p in v.split()} <= TEST_FILES:
            return None, False, []
        return "pytest config collects more files as tests (python_files)", False, []
    if key in COLLECTION_KEYS:
        return None, False, []
    if key == "usefixtures":
        names = [n for v in _values(value) for n in v.split()]
        return "pytest config applies fixtures to every test (usefixtures)", True, names
    if key == "filterwarnings":
        filters = [[f.strip() for f in v.split(":")] + ["", ""] for v in _values(value)]
        if not all(f[0] in FILTER_ACTIONS for f in filters):
            return "pytest config turns warnings into errors (filterwarnings)", True, []
        stdlib = sys.stdlib_module_names
        if not all("." not in f[2] or f[2].split(".")[0] in stdlib for f in filters):
            return "pytest config imports a warning category this reader does not read", True, []
        if all(warning_filter(v) for v in _values(value)):
            return None, True, []
        return "pytest config holds a warning filter pytest refuses at start", False, []
    if key == "addopts":
        try:
            tokens = [t for v in _values(value) for t in shlex.split(v)]
        except ValueError:
            return "pytest addopts cannot be read", False, []
        return _addopts(tokens)
    if key in SAFE_KEYS:
        return None, not beyond_installed(key, value), []
    return "pytest config option not known to be inert", True, []


def _addopts(tokens: list[str]) -> tuple[str | None, bool, list[str]]:
    """Reporting options pass; selection options make the count unknown; anything else (a
    plugin loaded with -p, -W, --cov-fail-under, -x, ...) refuses."""
    index, known = 0, True
    while index < len(tokens):
        token = tokens[index]
        has_next = index + 1 < len(tokens)
        nxt = tokens[index + 1] if has_next else ""
        if token == "-p" and nxt.startswith("no:") and nxt[3:].split(".")[0] not in PYTEST_PLUGINS:
            index += 2
        elif token in WITH_VALUE and has_next and not nxt.startswith("-") and _valued(token, nxt):
            index += 2
        elif token in SELECTING and has_next and not tokens[index + 1].startswith("-"):
            index, known = index + 2, False
        elif token.startswith(tuple(f"{s}=" for s in SELECTING if s.startswith("--"))):
            index, known = index + 1, False
        elif token.split("=", 1)[0] in DOCTESTS:
            return (
                "pytest addopts collects doctests, which run code the reader does not read",
                False,
                [],
            )
        elif (
            token in SAFE_FLAGS
            or REPORT.fullmatch(token)
            or (token.startswith(SAFE_PREFIXES) and _valued(*token.split("=", 1)))
        ):
            index += 1
        else:
            return "pytest addopts loads plugins or changes warnings or options", False, []
    return None, known, []


def _valued(option: str, value: str) -> bool:
    """Whether pytest takes this value for one of its own options (others are not checked here)."""
    check = VALUES.get(option)
    if check is None or isinstance(check, str):
        return check is None or bool(re.fullmatch(check, value))
    try:
        check(value)
    except ValueError:
        return False
    return True
