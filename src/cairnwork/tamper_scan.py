"""Static scan of a candidate patch for the APIs a verdict forgery needs (``VERIFIER_TAMPER_API``).

A candidate's code runs inside the pytest process that produces the trusted verdict (see
report_guard.py), so it can in principle exit early, read the guard's hand-over, walk the
interpreter's objects, reach into pytest or redirect where bytecode is loaded from. This module
refuses a patch whose added ``.py`` lines mention those APIs, before any candidate code runs.

This raises the bar; it is not a boundary. It reads single added lines with regular
expressions, so a name built from strings (``getattr(os, "_" + "exit")``), a split line, a
rebinding by assignment (``m = sys.modules``) or a non-``.py`` file that is imported later all
pass. An alias bound by an added import (``import os as o``, ``from sys import modules as m``)
is followed on that line and the added lines after it; an alias bound only in an unchanged
(context) line of the file is not seen. It catches the straightforward forms and the PoCs seen
so far, and it fails closed with a dedicated reason so an operator can see why. The rule list
is the one place to change; the alias and library checks reuse its names and allow-lists.

To keep honest code out of the net (#221): pure comment lines are skipped; the rules that only
matter where pytest runs the candidate's test code (``sys.modules`` writes, ``_pytest`` internals,
pytest hook implementations) apply in full only to test code (``conftest.py``, ``test_*.py``,
``*_test.py`` and files under a ``tests``/``test``/``testing`` directory), so a library's own
compatibility shims and pytest plugins pass; there, a literal stub of an unrelated module
(``sys.modules["dep"] = MagicMock()``), ``sys.modules[__name__] = ...`` and a type-hint import of
a public fixture class from ``_pytest`` are allowed. A library file that a conftest loads as a
plugin runs inside pytest too, so there the most direct forgery steps are still refused: writing
a literal sensitive key (``sys.modules["pytest"] = fake``) and importing ``_pytest``. A variable
key (``sys.modules[spec.name] = mod``, as plugin loaders do), ``pluginmanager`` and hook
implementations stay allowed in a library file: a known gap of this raised bar.
"""

from __future__ import annotations

import codecs
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from cairnwork.gitcmd import run_git

CODE = "VERIFIER_TAMPER_API"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    skip_in_conftest: bool = False  # hook implementations are normal in a conftest.py
    allow: re.Pattern[str] | None = None  # a whole line of this shape is honest usage
    tests_only: bool = False  # ordinary in library code; only checked in test code (_is_test_path)


def _rule(
    name: str, regex: str, *, skip_in_conftest: bool = False, allow: str = "", tests_only=False
) -> Rule:
    compiled = re.compile(allow) if allow else None
    return Rule(name, re.compile(regex), skip_in_conftest, compiled, tests_only)


# Public pytest fixture types that honest code imports from ``_pytest`` for type hints only.
# Classes whose methods a forgery would patch (Item, Config, reports, runner) are not here.
_HINT = (
    r"(?:CaptureFixture|FixtureRequest|LogCaptureFixture|MonkeyPatch|Pytester|TempPathFactory"
    r"|TempdirFactory|Testdir)(?:\s+as\s+\w+)?"
)
_HINTS = rf"{_HINT}(?:\s*,\s*{_HINT})*"
_PYTEST_HINT_IMPORT = (  # one complete line; an open "(" continuing on the next line is not allowed
    rf"^\s*from\s+_pytest(?:\.\w+)+\s+import\s+(?:{_HINTS}|\(\s*{_HINTS}\s*,?\s*\))\s*(?:#.*)?$"
)
_COMMENT_ONLY = re.compile(r"^\s*#")
# sys.modules entries a forgery would swap; a literal key naming any other module is a test stub.
_SENSITIVE_MODULE = (
    r"(?:_?pytest\w*|pluggy|py|_?cairnwork\w*|sys|os|builtins|importlib|gc|conftest|__main__)"
)
_STUB_NAME = (
    rf"(?:'(?!{_SENSITIVE_MODULE}(?:\.|'))[\w.]+'|\"(?!{_SENSITIVE_MODULE}(?:\.|\"))[\w.]+\")"
)
_STUB_KEY = rf"\[\s*{_STUB_NAME}\s*\]"
_STUB_RHS = r"(?:(?!\bmodules\b)[^;])*"  # no second sys.modules access chained on the line
# One statement on the line: assign, delete, pop or setdefault one literal stub key.
_SYS_MODULES_STUB = (
    rf"^\s*(?:sys\s*\.\s*modules\s*{_STUB_KEY}\s*=(?!=){_STUB_RHS}"
    rf"|del\s+sys\s*\.\s*modules\s*{_STUB_KEY}"
    rf"|sys\s*\.\s*modules\s*\.\s*pop\s*\(\s*{_STUB_NAME}\s*(?:,\s*None\s*)?\)"
    rf"|sys\s*\.\s*modules\s*\.\s*setdefault\s*\(\s*{_STUB_NAME}\s*,{_STUB_RHS}\))\s*(?:#.*)?$"
)
_TEST_DIRS = frozenset({"tests", "test", "testing"})


def _is_test_path(path: str) -> bool:
    """conftest.py, test_*.py / *_test.py, or any file under a tests/ test/ testing/ directory."""
    parts = path.split("/")
    name = parts[-1]
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(part in _TEST_DIRS for part in parts[:-1])


RULES: tuple[Rule, ...] = (
    _rule("os_exit", r"\bos\s*\.\s*_exit\b|\bfrom\s+os\s+import\b[^#\n]*\b_exit\b"),
    _rule(
        "cairnwork_env",
        r"(?=.*\bCAIRNWORK_)(?=.*(?:\benviron\b|\bgetenv\b|\benvb\b))",
    ),
    _rule("gc_walk", r"\bgc\s*\.\s*(?:get_objects|get_referrers|get_referents)\b"),
    _rule(
        "sys_modules_write",
        r"\bsys\s*\.\s*modules\s*\[(?!\s*__name__\s*\])[^\]]*\]\s*=(?!=)"
        r"|\bdel\s+sys\s*\.\s*modules\b"
        r"|\bsys\s*\.\s*modules\s*\.\s*(?:pop|popitem|update|setdefault|clear)\s*\(",
        allow=_SYS_MODULES_STUB,
        tests_only=True,
    ),
    _rule(
        "pytest_internals",
        r"\b_pytest\b|\bpluginmanager\b",
        allow=_PYTEST_HINT_IMPORT,
        tests_only=True,
    ),
    _rule(
        "pytest_hook_impl",
        r"^\s*(?:async\s+)?def\s+pytest_(?:runtest|sessionfinish|sessionstart|collection|configure"
        r"|unconfigure|report|terminal_summary|exception_interact)\w*"
        r"|\bpytest\s*\.\s*hookimpl\b",
        skip_in_conftest=True,
        tests_only=True,
    ),
    _rule("guard_module", r"_cairnwork_report_guard"),
    _rule("fd_listing", r"/proc/(?:self|thread-self|\d+)/fd\b|/dev/fd\b"),
    # Setting sys.pycache_prefix at run time points imports and pytest's assertion rewriter at
    # planted bytecode for a protected test (#276). Any mention in any file, so an alias or
    # setattr(sys, "pycache_prefix", ...) is caught too; honest code rarely even reads it, so
    # refusing a read is an accepted false positive.
    _rule("pycache_prefix", r"\bpycache_prefix\b"),
)
_BY_NAME = {rule.name: rule for rule in RULES}

# Import statements that can bind another name to sys, os, sys.modules or os._exit. A statement
# may follow ``;`` or a ``:`` (``try: import sys as s``); the name list stops at a comment.
_IMPORT_STMT = re.compile(r"(?:^|[:;])\s*import\s+([^#;]+)")
_FROM_STMT = re.compile(r"(?:^|[:;])\s*from\s+(sys|os)\s+import\s+\(?([^#;)]+)")
_IMPORTED_NAME = re.compile(r"^\s*([\w.]+)(?:\s+as\s+([A-Za-z_]\w*))?\s*$")
_FROM_TARGET = {"sys": "modules", "os": "_exit"}  # the one name worth following per module

# Library files: only a literal sensitive key (the names _SYS_MODULES_STUB refuses in test code),
# so a plugin loader's sys.modules[spec.name] = mod and a stub of an unrelated module pass.
_SENSITIVE_KEY = rf"""(?:'{_SENSITIVE_MODULE}(?:\.[\w.]*)?'|"{_SENSITIVE_MODULE}(?:\.[\w.]*)?")"""
_MODULES = r"\bsys\s*\.\s*modules\s*"
_LIBRARY_MODULES_WRITE = re.compile(
    rf"{_MODULES}\[\s*{_SENSITIVE_KEY}\s*\]\s*=(?!=)"
    rf"|\bdel\s+[^#;]*{_MODULES}\[\s*{_SENSITIVE_KEY}\s*\]"
    rf"|{_MODULES}\.\s*(?:pop|setdefault)\s*\(\s*{_SENSITIVE_KEY}"
    rf"|{_MODULES}\.\s*update\s*\([^)]*{_SENSITIVE_KEY}"
)
# An import statement naming _pytest (not importlib.import_module, not a pytest_x helper).
_LIBRARY_PYTEST = Rule(  # with main's type-hint import allow
    "pytest_internals",
    re.compile(r"\bimport\s+[^#;]*?(?<![\w.])_pytest\b|\bfrom\s+_pytest\b"),
    allow=_BY_NAME["pytest_internals"].allow,
)


def _bind_aliases(text: str, aliases: dict[str, set[str]]) -> None:
    """Record in ``aliases`` the names this line's imports bind to sys, os, modules or _exit."""
    for match in _IMPORT_STMT.finditer(text):
        for name, alias in _imported_names(match.group(1)):
            if name in ("sys", "os") and alias:
                aliases[name].add(alias)
    for match in _FROM_STMT.finditer(text):
        target = _FROM_TARGET[match.group(1)]
        for name, alias in _imported_names(match.group(2)):
            if name == target:
                aliases[target].add(alias or name)


def _imported_names(clause: str) -> list[tuple[str, str | None]]:
    parsed = (_IMPORTED_NAME.match(part) for part in clause.split(","))
    return [(found.group(1), found.group(2)) for found in parsed if found]


def _canonical(text: str, aliases: dict[str, set[str]]) -> str:
    """The line with every known alias spelled as the plain name, so main's rules read it."""
    # Not after "." or a word character: obj.s.modules or ms.modules is not the alias s.
    for name in sorted(aliases["sys"]):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*modules\b)", r"sys\1", text)
    for name in sorted(aliases["os"]):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*_exit\b)", r"os\1", text)
    for name in sorted(aliases["modules"]):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}\b", "sys.modules", text)
    for name in sorted(aliases["_exit"]):  # a call; "def _exit(self):" defines a method instead
        text = re.sub(rf"(?<![\w.])(?<!def ){re.escape(name)}(\s*\()", r"os._exit\1", text)
    return text


def _hits(rule: Rule, text: str) -> bool:
    if rule.allow is not None and rule.allow.match(text):
        return False  # a whole line of an honest shape
    return bool(rule.pattern.search(text))


def _extra_rules(text: str, aliases: dict[str, set[str]], in_tests: bool) -> list[str]:
    """Rule names the RULES table misses on this line: alias spellings and library forgeries."""
    canon = _canonical(text, aliases)
    names = []
    if canon != text and _hits(_BY_NAME["os_exit"], canon):
        names.append("os_exit")
    if in_tests:  # main's full sys.modules rule, stub allow included, on the plain spelling
        if canon != text and _hits(_BY_NAME["sys_modules_write"], canon):
            names.append("sys_modules_write")
        return names
    # The original line too: an alias that shadows "sys" itself must not hide sys.modules.
    if _LIBRARY_MODULES_WRITE.search(text) or _LIBRARY_MODULES_WRITE.search(canon):
        names.append("sys_modules_write")
    if _hits(_LIBRARY_PYTEST, text):
        names.append("pytest_internals")
    return names


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}]"


def scan_added_lines(path: str, lines: list[tuple[int, str]]) -> list[Finding]:
    """Findings for the added ``(line number, text)`` pairs of one file (``.py`` files only)."""
    if not path.endswith(".py"):
        return []
    in_conftest = posixpath.basename(path) == "conftest.py"
    in_tests = _is_test_path(path)
    # Aliases bound so far by the added lines, in order (context lines are not in the diff).
    aliases: dict[str, set[str]] = {"sys": set(), "os": set(), "modules": set(), "_exit": set()}
    found = []
    for number, text in lines:
        if _COMMENT_ONLY.match(text):
            continue
        names = []
        for rule in RULES:
            if (rule.skip_in_conftest and in_conftest) or (rule.tests_only and not in_tests):
                continue
            if _hits(rule, text):
                names.append(rule.name)
        _bind_aliases(text, aliases)  # before the extra checks: "import os as o; o._exit(0)"
        names += [name for name in _extra_rules(text, aliases, in_tests) if name not in names]
        found += [Finding(path, number, name) for name in names]
    return found


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """The added lines of a ``git diff -U0`` text, by new path with their new line numbers."""
    files: dict[str, list[tuple[int, str]]] = {}
    current, number = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = _unquote(raw[4:].rstrip("\t"))  # git appends a tab to a path with a space
            current = target[2:] if target.startswith("b/") else None
            if current is not None:
                files.setdefault(current, [])
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            number = int(match.group(1)) if match else 0
        elif current is not None and raw.startswith("+"):
            files[current].append((number, raw[1:]))
            number += 1
    return files


def _unquote(name: str) -> str:
    """git C-quotes a path with special characters (``"b/a\\tb.py"``, octal escapes for bytes)."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    raw = name[1:-1].encode("utf-8", "surrogateescape")
    return codecs.escape_decode(raw)[0].decode("utf-8", "surrogateescape")


def scan_patch(repo: Path, base: str, head: str) -> list[Finding]:
    """Scan what ``head`` adds on top of ``base`` (both commits already in repo)."""
    diff = run_git(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "-U0",
        "--src-prefix=a/", "--dst-prefix=b/", base, head,
    )  # fmt: skip
    return [
        finding
        for path, lines in added_lines(diff).items()
        for finding in scan_added_lines(path, lines)
    ]
