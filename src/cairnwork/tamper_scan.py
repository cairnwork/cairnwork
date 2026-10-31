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
(context) line, by a parenthesised import split over lines, a backslash continuation or
``from sys import *`` is not seen. Text inside string literals is scanned like code, as for
every rule (an aliased name in a string can be flagged). The diff is cut into lines only at a
newline and file headers are read only between hunks, so no text inside an added line ends it
early; a carriage return, which Python also reads as a line end, starts a new piece to scan.
Python reads identifiers in NFKC form, so each piece is also matched in that form, and a source
encoding other than UTF-8 declared on an added line 1 or 2 is refused (``source_encoding``). It
catches the straightforward forms and the PoCs seen so far, and it fails closed with a dedicated
reason so an operator can see why.
The rule list is the one place to change; the alias and library checks reuse its names and
allow-lists.

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
implementations stay allowed in a library file: a known gap of this raised bar, as are literal
key variants there (f-strings, implicit concatenation, ``update(pytest=...)``). The library
``_pytest`` import rule also refuses a pytest plugin's own non-fixture type imports
(``from _pytest.config import Config``): an accepted false-block cost, the same one test code
already pays.
"""

from __future__ import annotations

import codecs
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git_bytes

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
# A PEP 263 encoding declaration (Python reads it on line 1 or 2) and the UTF-8 names it accepts.
_CODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.ASCII)
_UTF8 = re.compile(r"utf[-_]?8(?:[-_][-\w.]*)?", re.ASCII | re.IGNORECASE)
# sys.modules entries a forgery would swap; a literal key naming any other module is a test stub.
_SENSITIVE_MODULE = (
    r"(?:_?pytest\w*|pluggy|py|_?cairnwork\w*|sys|os|builtins|importlib|gc|conftest|__main__)"
)
# Test code by module name: a test root package, a conftest or a test module, at any depth.
_TEST_MODULE = r"(?:\w+\.)*(?:tests|test|testing|conftest|test_\w*|\w+_test)"
_STUB_NAME = (
    rf"(?:'(?!{_SENSITIVE_MODULE}(?:\.|')|{_TEST_MODULE}(?:\.|'))[\w.]+'"
    rf"|\"(?!{_SENSITIVE_MODULE}(?:\.|\")|{_TEST_MODULE}(?:\.|\"))[\w.]+\")"
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
    _rule(
        "os_exit",
        r"\b(?:os|posix|nt)\s*\.\s*_exit\b|\bfrom\s+(?:os|posix|nt)\s+import\b[^#\n]*\b_exit\b",
    ),
    _rule(
        "cairnwork_env",
        r"(?=.*\bCAIRNWORK_)(?=.*(?:\benviron\b|\bgetenv\b|\benvb\b))",
    ),
    _rule(
        "gc_walk",
        r"\bgc\s*\.\s*(?:get_objects|get_referrers|get_referents)\b"
        r"|\bfrom\s+gc\s+import\b[^#\n]*\b(?:get_objects|get_referrers|get_referents)\b",
    ),
    _rule(
        "sys_modules_write",
        r"\bsys\s*\.\s*modules\s*\[(?!\s*__name__\s*\])[^\]]*\]\s*=(?!=)"
        r"|\bdel\s+sys\s*\.\s*modules\b"
        r"|\bsys\s*\.\s*modules\s*\.\s*(?:pop|popitem|update|setdefault|clear"
        r"|__setitem__|__delitem__|__ior__)\s*\(|\bsys\s*\.\s*modules\s*\|=",
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
    # Redirecting where later imports resolve, so a protected test/conftest/helper loads a module
    # the candidate controls instead of the checkout file (#290). A meta_path finder answers for a
    # name from memory or a planted file; a path_hooks entry or a cleared/replaced
    # path_importer_cache makes the import system re-ask a candidate finder for a directory on the
    # path (including the protected tests/ dir). Matched by the bare attribute name in any file, so
    # an alias (``import sys as s; s.meta_path``) is caught too. Honest code rarely touches these
    # import internals, so a flagged read is an accepted false positive, as for ``pycache_prefix``.
    # This is a higher bar, not a boundary (a name built from strings or a non-.py file still
    # passes; a ``sys.modules`` entry under a non-sensitive key is not reached). A runtime observer
    # that watches import resolution is tracked separately (Tier-3, out-of-process). ``sys.path``
    # writes are deliberately not here: an honest src-layout conftest prepends ``src``, and pytest
    # restores ``tests/`` to ``sys.path[0]`` per module, so a bare prepend loses to pytest.
    _rule("meta_path", r"\bmeta_path\b"),
    _rule("path_hooks", r"\bpath_hooks\b"),
    _rule("path_importer_cache", r"\bpath_importer_cache\b"),
)
_BY_NAME = {rule.name: rule for rule in RULES}

# Import statements that can bind another name to sys, os, gc, sys.modules or os._exit. A statement
# may follow ``;`` or a ``:`` (``try: import sys as s``); the name list stops at a comment.
_IMPORT_STMT = re.compile(r"(?:^|[:;])\s*import\s+([^#;]+)")
_FROM_STMT = re.compile(r"(?:^|[:;])\s*from\s+(sys|os)\s+import\s+\(?([^#;)]+)")
_IMPORTED_NAME = re.compile(r"^\s*([\w.]+)(?:\s+as\s+([A-Za-z_]\w*))?\s*$")
_FROM_TARGET = {"sys": "modules", "os": "_exit"}  # the one name worth following per module
_MODULE_ALIAS = {"sys": "sys", "os": "os", "posix": "os", "nt": "os", "gc": "gc"}  # posix is os

# Library files: only a literal sensitive key (the names _SYS_MODULES_STUB refuses in test code),
# so a plugin loader's sys.modules[spec.name] = mod and a stub of an unrelated module pass.
_SENSITIVE_NAME = rf"(?:{_SENSITIVE_MODULE}|{_TEST_MODULE})(?:\.[\w.]*)?"
_SENSITIVE_KEY = rf"""(?:'{_SENSITIVE_NAME}'|"{_SENSITIVE_NAME}")"""
_MODULES = r"\bsys\s*\.\s*modules\s*"
_LIBRARY_MODULES_WRITE = re.compile(
    rf"{_MODULES}\[\s*{_SENSITIVE_KEY}\s*\]\s*=(?!=)"
    rf"|\bdel\s+[^#;]*{_MODULES}\[\s*{_SENSITIVE_KEY}\s*\]"
    rf"|{_MODULES}\.\s*(?:pop|setdefault|__setitem__|__delitem__)\s*\(\s*{_SENSITIVE_KEY}"
    rf"|{_MODULES}(?:\.\s*(?:update|__ior__)\s*\(|\|=)[^)]*{_SENSITIVE_KEY}"
)
# An import statement naming _pytest (not importlib.import_module, not a pytest_x helper).
_LIBRARY_PYTEST = Rule(  # with main's type-hint import allow
    "pytest_internals",
    re.compile(r"\bimport\s+[^#;]*?(?<![\w.])_pytest\b|\bfrom\s+_pytest\b"),
    allow=_BY_NAME["pytest_internals"].allow,
)


def _bind_aliases(text: str, aliases: dict[str, set[str]]) -> None:
    """Record in ``aliases`` the names this line's imports bind to sys, os, gc, modules or _exit."""
    for match in _IMPORT_STMT.finditer(text):
        for name, alias in _imported_names(match.group(1)):
            if name in _MODULE_ALIAS and alias:
                aliases[_MODULE_ALIAS[name]].add(alias)
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
    for name in sorted(aliases["gc"]):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*get_)", r"gc\1", text)
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
    names = [
        name for name in ("os_exit", "gc_walk") if canon != text and _hits(_BY_NAME[name], canon)
    ]
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
    aliases: dict[str, set[str]] = {key: set() for key in ("sys", "os", "gc", "modules", "_exit")}
    found = []
    # Python also ends a source line at "\r" (CRLF included), so each piece is scanned on its own.
    pieces = [(number, piece) for number, line in lines for piece in line.split("\r")]
    for number, piece in pieces:
        if number <= 2 and _foreign_coding(piece):
            found.append(Finding(path, number, "source_encoding"))
        if _COMMENT_ONLY.match(piece):
            continue
        names: list[str] = []
        # Python compiles identifiers in NFKC form (PEP 3131): the piece as written, then that form.
        for text in dict.fromkeys((piece, unicodedata.normalize("NFKC", piece))):
            for rule in RULES:
                if (rule.skip_in_conftest and in_conftest) or (rule.tests_only and not in_tests):
                    continue
                if _hits(rule, text) and rule.name not in names:
                    names.append(rule.name)
            _bind_aliases(text, aliases)  # before the extra checks: "import os as o; o._exit(0)"
            names += [name for name in _extra_rules(text, aliases, in_tests) if name not in names]
        found += [Finding(path, number, name) for name in names]
    return found


def _foreign_coding(text: str) -> bool:
    """A comment declaring a source encoding other than UTF-8 (read by Python on line 1 or 2)."""
    found = _CODING.match(text)
    return found is not None and not _UTF8.fullmatch(found.group(1))


_HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX = re.compile(r"index [0-9a-f]+\.\.([0-9a-f]+)")
# A new side with no lines to show: a deleted file (all zeros) or the empty blob (SHA-1, SHA-256).
_NO_LINES = re.compile(
    r"0+|e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    r"|473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813"
)


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """The added lines of a ``git diff -U0`` text, by new path with their new line numbers.

    A line ends only at "\\n" (git ends every diff line there; any other line break inside is
    content). Inside a hunk, its header's counts say how many old and new lines follow, so an
    added line that starts with "++ " or "@@" is content, not a file or hunk header. A diff whose
    lines do not match those counts raises GitCommandError (unreadable, so the change is refused),
    as does a file whose ``index`` line names new content but whose lines the diff does not show.
    """
    files: dict[str, list[tuple[int, str]]] = {}
    current, number, old_left, new_left = None, 0, 0, 0
    owed = False  # an "index" line named new content; its "+++" header has not come yet
    for raw in diff.removesuffix("\n").split("\n"):
        if old_left > 0 or new_left > 0:
            # "" is a blank context line (diff.suppressBlankEmpty); "\ No newline" counts for none
            kind = raw[:1] or " "
            if kind not in "+- \\":
                raise GitCommandError("diff line inside a hunk has no +, -, space or \\ prefix")
            if kind == "+" and current is not None:
                files[current].append((number, raw[1:]))
            old_left -= kind in "- "  # a bool counts as 0 or 1
            new_left -= kind in "+ "
            number += kind in "+ "
            if old_left < 0 or new_left < 0:
                raise GitCommandError("diff hunk has more lines than its header counts")
        elif raw.startswith(("+", "-", " ")) and not raw.startswith(("+++ ", "--- ")):
            raise GitCommandError("diff line outside a hunk")
        elif raw.startswith("diff --git ") and owed:
            raise GitCommandError("diff shows a changed file without its lines")
        elif index := _INDEX.match(raw):
            owed = not _NO_LINES.fullmatch(index.group(1))
        elif raw.startswith("+++ "):
            owed = False
            target = _unquote(raw[4:].rstrip("\t"))  # git appends a tab to a path with a space
            current = target[2:] if target.startswith("b/") else None
            if current is not None:
                files.setdefault(current, [])
        elif hunk := _HUNK.match(raw):
            old_left, number = int(hunk.group(1) or 1), int(hunk.group(2))
            new_left = int(hunk.group(3) or 1)
    if old_left > 0 or new_left > 0 or owed:
        raise GitCommandError("diff ends inside a hunk or without a changed file's lines")
    return files


def _unquote(name: str) -> str:
    """git C-quotes a path with special characters (``"b/a\\tb.py"``, octal escapes for bytes)."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    raw = name[1:-1].encode("utf-8", "surrogateescape")
    return codecs.escape_decode(raw)[0].decode("utf-8", "surrogateescape")


def scan_patch(repo: Path, base: str, head: str) -> list[Finding]:
    """Scan what ``head`` adds on top of ``base`` (both commits already in repo).

    Only ``.py`` files are read, as text whatever git attributes or size settings say.
    """
    raw = run_git_bytes(  # bytes: no newline translation, so a lone "\r" stays inside its line
        repo, "-c", "diff.suppressBlankEmpty=false", "-c", "diff.interHunkContext=0",
        "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "-U0", "--text",
        "--full-index", "--src-prefix=a/", "--dst-prefix=b/", base, head, "--", "*.py",
    )  # fmt: skip
    try:
        diff = raw.decode("utf-8")
    except UnicodeDecodeError as exc:  # as run_git: a change that is not UTF-8 is unreadable
        raise GitCommandError("git diff output is not UTF-8") from exc
    return [
        finding
        for path, lines in added_lines(diff).items()
        for finding in scan_added_lines(path, lines)
    ]
