"""Static scan of a candidate patch for the APIs a verdict forgery needs (``VERIFIER_TAMPER_API``).

A candidate's code runs inside the pytest process that produces the trusted verdict (see
report_guard.py), so it can in principle exit early, read the guard's hand-over, walk the
interpreter's objects, reach into pytest or redirect where bytecode is loaded from. This module
refuses a patch whose added ``.py`` lines mention those APIs, before any candidate code runs.

This raises the bar; it is not a boundary. It reads single added lines with regular
expressions, so a name built from strings (``getattr(os, "_" + "exit")``, and likewise
``getattr(pytest, "Test" + "Report")`` for the ``pytest_public_patch`` rule), a split line, a
rebinding by assignment (``m = sys.modules``) or a non-``.py`` file that is imported later all
pass: known residuals. Changes to the pytest classes listed in tamper_scan_pytest.py
(``pytest_public_patch``) are refused in every file, library code included. An alias bound by an
added import (``import os as o``, ``from sys import modules as m``, ``import pytest as p``)
is followed on that line and the added lines after it; an alias bound only in an unchanged
(context) line, by a parenthesised import split over lines, a backslash continuation or
``from sys import *`` is not seen. Text inside string literals is scanned like code, as for
every rule (an aliased name in a string can be flagged). Which ``#`` starts a comment and which
``;`` ends a statement is Python's own reading of the whole committed file (tamper_scan_tokens.py):
each line is also scanned with every ``#`` and ``;`` inside a string literal masked, so one in a
string, or a string opened on an earlier line, does not cut a statement short or hide one, and a
foreign encoding declared on line 1 or 2 of the committed file is refused even when that line is
unchanged. The diff is cut into lines only at a
newline and file headers are read only between hunks, so no text inside an added line ends it
early; a carriage return, which Python also reads as a line end, starts a new piece to scan.
Python reads identifiers in NFKC form, so each piece is also matched with its identifier
characters in that form (the rest as written), and a source encoding other than UTF-8 declared
on an added line 1 or 2 is refused (``source_encoding``). It catches the straightforward forms
and the PoCs seen so far, and it fails closed with a dedicated reason so an operator can see why.
The rule list is the one place to change; the alias and library checks reuse its names and
allow-lists.

To keep honest code out of the net (#221): a line that starts with ``#`` and holds no code for
Python (only a comment or plain string text) is skipped; the rules that only
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

import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from cairnwork.gitcmd import OID, GitCommandError, run_git_bytes
from cairnwork.tamper_scan_diff import added_lines
from cairnwork.tamper_scan_pytest import PYTEST_CLASSES, PYTEST_PUBLIC_PATCH, canonical_pytest
from cairnwork.tamper_scan_tokens import added_file, file_views

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
_UTF8 = re.compile(r"utf[-_]8(?:[-_][-\w.]*)?|utf8", re.ASCII | re.IGNORECASE)
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
    _rule("pytest_public_patch", PYTEST_PUBLIC_PATCH),
)
_BY_NAME = {rule.name: rule for rule in RULES}

# Import statements that can bind another name to sys, os, gc, sys.modules or os._exit. A statement
# may follow ``;`` or a ``:`` (``try: import sys as s``); the name list stops at a comment.
_IMPORT_STMT = re.compile(r"(?:^|[:;])\s*import\s+([^#;]+)")
_FROM_STMT = re.compile(r"(?:^|[:;])\s*from\s+(sys|os|pytest)\s+import\s+\(?([^#;)]+)")
_IMPORTED_NAME = re.compile(r"^\s*([\w.]+)(?:\s+as\s+([^\W\d]\w*))?\s*$")
_FROM_TARGET = {"sys": ("modules",), "os": ("_exit",), "pytest": PYTEST_CLASSES}  # to follow
_MODULE_ALIAS = {name: name for name in ("sys", "os", "gc", "pytest")} | {"posix": "os", "nt": "os"}
_ALIAS_RULES = ("os_exit", "gc_walk", "pytest_public_patch")  # read on the alias spelling anywhere

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
                aliases.setdefault(_MODULE_ALIAS[name], set()).add(alias)
    for match in _FROM_STMT.finditer(text):
        targets = _FROM_TARGET[match.group(1)]
        for name, alias in _imported_names(match.group(2)):
            if name in targets:
                aliases.setdefault(name, set()).add(alias or name)


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
    return canonical_pytest(text, aliases)


def _hits(rule: Rule, text: str) -> bool:
    if rule.allow is not None and rule.allow.match(text):
        return False  # a whole line of an honest shape
    return bool(rule.pattern.search(text))


def _extra_rules(text: str, aliases: dict[str, set[str]], in_tests: bool) -> list[str]:
    """Rule names the RULES table misses on this line: alias spellings and library forgeries."""
    canon = _canonical(text, aliases)
    names = [name for name in _ALIAS_RULES if canon != text and _hits(_BY_NAME[name], canon)]
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


def scan_added_lines(
    path: str, lines: list[tuple[int, str]], data: bytes | None = None
) -> list[Finding]:
    """Findings for the added ``(line number, text)`` pairs of one file (``.py`` files only).

    ``data`` is the whole file as committed (scan_patch passes it): Python's reading of its
    comments and strings comes from there (tamper_scan_tokens.py). Without it, a file made of the
    added lines alone stands in.
    """
    if not path.endswith(".py"):
        return []
    in_conftest = posixpath.basename(path) == "conftest.py"
    in_tests = _is_test_path(path)
    # Aliases bound so far by the added lines, in order (context lines are not in the diff).
    aliases: dict[str, set[str]] = {key: set() for key in ("sys", "os", "gc", "modules", "_exit")}
    masked_aliases = {key: set(names) for key, names in aliases.items()}  # also from masked lines
    source = data if data is not None else added_file(lines)
    views = (file_views(source) if source is not None else None) or {}  # none: no comment skip
    # Aliases bound by code only (no string text, no comment), when the file tokenizes.
    code_aliases = {key: set(names) for key, names in aliases.items()} if views else None
    found = []
    # Python also ends a source line at "\r" (CRLF included), so each piece is scanned on its own.
    for number, line in lines:
        for index, piece in enumerate(line.split("\r")):
            view = views.get((number, index))
            if number <= 2 and _foreign_coding(piece):
                found.append(Finding(path, number, "source_encoding"))
            if _COMMENT_ONLY.match(piece) and view and view.skip and len(view.code) == len(piece):
                continue  # a comment to Python, or string text with no code on the line
            # Main's reading, with its own alias table; a line main skips as a comment has none.
            plain = () if _COMMENT_ONLY.match(piece) else (piece, _nfkc_names(piece))
            names = _rule_names(plain, aliases, masked_aliases, in_tests, in_conftest)
            if code_aliases is not None and view is not None:
                for text in dict.fromkeys((view.bound, _nfkc_names(view.bound))):
                    _bind_aliases(text, code_aliases)
                names += [n for t in plain for n in _extra_rules(t, code_aliases, in_tests)]
                names = list(dict.fromkeys(names))
            code = piece if view is None else view.code  # "#", ";" in a string masked
            masked = (code, _nfkc_names(code))  # read with a table of its own: only adds names
            if masked != plain or masked_aliases != aliases:
                more = _rule_names(masked, masked_aliases, None, in_tests, in_conftest)
                names += [name for name in more if name not in names]
            found += [Finding(path, number, name) for name in names]
    return found + _committed_coding(path, data, found)


def _rule_names(
    texts: tuple[str, ...],
    aliases: dict[str, set[str]],
    also: dict[str, set[str]] | None,
    in_tests: bool,
    in_conftest: bool,
) -> list[str]:
    """Rule names for ``texts`` read with ``aliases``; their imports also bind into ``also``."""
    names: list[str] = []
    # Python compiles identifiers in NFKC form (PEP 3131): each text, then that form.
    for text in dict.fromkeys(texts):
        for rule in RULES:
            if (rule.skip_in_conftest and in_conftest) or (rule.tests_only and not in_tests):
                continue
            if _hits(rule, text) and rule.name not in names:
                names.append(rule.name)
        _bind_aliases(text, aliases)  # before the extra checks: "import os as o; o._exit(0)"
        if also is not None:
            _bind_aliases(text, also)
        names += [name for name in _extra_rules(text, aliases, in_tests) if name not in names]
    return names


def _committed_coding(path: str, data: bytes | None, found: list[Finding]) -> list[Finding]:
    """A foreign source encoding declared on line 1 or 2 of the committed file, unchanged too."""
    if data is None:
        return []
    seen = {finding.line for finding in found if finding.rule == "source_encoding"}
    head = enumerate(data.decode("latin-1").split("\n")[:2], start=1)  # the cookie is ASCII
    return [
        Finding(path, number, "source_encoding")
        for number, line in head
        if number not in seen and any(_foreign_coding(piece) for piece in line.split("\r"))
    ]


def _nfkc_names(text: str) -> str:
    """Identifier characters in NFKC form, the rest as written (no new operator or separator)."""
    pairs = ((char, unicodedata.normalize("NFKC", char)) for char in text)
    return "".join(new if ("a" + new).isidentifier() else old for old, new in pairs)


def _foreign_coding(text: str) -> bool:
    """A comment declaring a source encoding other than UTF-8 (read by Python on line 1 or 2)."""
    found = _CODING.match(text)
    return found is not None and not _UTF8.fullmatch(found.group(1))


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
    if not OID.fullmatch(head):  # read as "<commit>:<path>" below, so never an option
        raise GitCommandError("head is not a full commit id")
    found = []
    for path, lines in added_lines(diff).items():
        if path.endswith(".py") and lines:  # the whole file as committed, for Python's reading
            data = run_git_bytes(repo, "cat-file", "blob", f"{head}:{path}")
            found += scan_added_lines(path, lines, data)
    return found
