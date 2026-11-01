"""The ``pytest_public_patch`` rule of tamper_scan.py: source changes to listed pytest classes.

The verifier's pytest process also imports the candidate's library code, so a library file can
replace a method of a pytest class (``pytest.TestReport.from_item_and_call``) and change what
pytest reports for a protected test. tamper_scan therefore refuses, in any file (library code
too, not only test code), an added line that changes an attribute of one of ``PYTEST_CLASSES``
below (the report, item, session, config, plugin-manager, exception-info and ``raises`` matcher
classes, nothing else) reached through ``pytest.``, at any depth and through a subscript
(``__mro__[0].runtest``, an item of a method's ``__globals__``), with a name or chain in brackets of
its own read without them (``(pytest.Item).runtest``): as the target of ``=`` or an augmented
assignment, with an annotation, inside a tuple, list or parentheses, as a ``for`` or ``with``
target (a keyword may touch a bracket or digit, as Python allows), in ``del``, passed to
``setattr``/``delattr`` or to ``unittest.mock``'s ``patch``, ``patch.object`` or
``patch.multiple`` (the first argument or ``target=``, a string with any prefix or quotes); and
any line that mentions ``from_item_and_call``. An annotation may hold
``==`` and its target may be in parentheses. An alias bound by an added import
(``import pytest as p``, ``from pytest import TestReport as R``, also ``import(`` with no
space) is followed by tamper_scan's alias machinery, which spells it as ``pytest.<Class>`` with
``canonical_pytest`` below. Type hints, ``isinstance``
checks, reads, comparisons, calls and keyword arguments pass. Accepted costs (honest code also
refused): a self-documenting f-string (``f"{pytest.Item.name=}"``), ``del`` of a mapping entry
keyed by such an attribute, a plugin that builds reports with ``from_item_and_call``, and a slice
whose bound is such an attribute between two colons, followed on the line by ``==`` and later a
``=`` (a keyword argument): the colon reads as an annotation, which may hold ``==``.

A raised bar, not a boundary. Known residuals that still pass: a name built from strings
(``getattr(pytest, "Test" + "Report")``), a rebinding by assignment (``R = pytest.TestReport``),
the class reached through an instance (``type(item).runtest = f``, ``report.__class__``), a
subclass that overrides a method (a custom ``Item.runtest``), pytest's other classes and its
module-level functions (``pytest.raises``, ``pytest.approx``), ``setattr`` with arguments built
elsewhere, a ``with`` target inside a tuple, a target split over lines, ``from pytest import *``
and an alias bound only in an unchanged line.
"""

from __future__ import annotations

import re

PYTEST_CLASSES = (
    "TestReport",
    "CollectReport",
    "SubtestReport",
    "CallInfo",
    "ExceptionInfo",
    "RaisesExc",
    "RaisesGroup",
    "Session",
    "Item",
    "Function",
    "Metafunc",
    "Config",
    "PytestPluginManager",
)
_CLASS = rf"(?:{'|'.join(PYTEST_CLASSES)})\b"
_OBJECT = rf"\bpytest\s*\.\s*{_CLASS}"
# An attribute of a listed class at any depth, also through a subscript (``__mro__[0].runtest``,
# ``runtest.__globals__["x"]``): an item assignment there is refused as its ``del`` is.
_CHAIN = rf"{_OBJECT}(?:\s*(?:\.\s*\w+|\[[^\[\]]*\]))+"
_ASSIGN = r"(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"  # ``=``, ``+=``, ``>>=``; not ``==``, ``<=``
# One character of an assignment target list (names, dots, commas, stars, brackets), but not a
# call or subscript ("(" or "[" after a name or closer), so a keyword argument is no target.
_TARGET = r"(?:(?![\w.)\]]\s*[(\[])[\w\s.,*()\[\]])"
_FOR_TARGET = rf"(?:(?!\bin\b){_TARGET})"  # a ``for`` target ends at ``in``
_START = r"(?:^|[;:])\s*"  # where a statement starts on the line
_KEYWORD = r"(?<![^\W\d])"  # a keyword may follow a digit or bracket directly ("0for", ")as(")
# An annotated target and its colon. An annotation run stops where another one starts, which then
# reads on from there itself: the same matches, but each part of a long line is read once.
_ANNOTATED = rf"[(\s]*{_CHAIN}[\s)]*:"
_NOT_FOR = rf"(?:(?!{_KEYWORD}for\b){_FOR_TARGET})"  # a ``for`` target up to a later ``for``
# The target of a patch or set/delattr call: its first argument or ``target=`` (a keyword of mock's
# patch and of monkeypatch), also in brackets, unpacked or a string with any prefix or quotes.
_ARGS = r"(?:[^#;()]|\([^()]*\))*?"  # earlier arguments, holding at most one level of brackets
_TARGET_ARG = rf"\(\s*(?:{_ARGS}\btarget\s*=(?!=))?[*(\s]*[rRuUfFbB]{{0,2}}['\"]{{0,3}}\s*"
_PATCH_TARGET = (
    rf"(?:\bpatch(?:\s*\.\s*(?:object|multiple))?|\b_*(?:set|del)attr_*)\s*{_TARGET_ARG}"
)
# Such a target naming a ``_pytest`` module, which the call imports as an import statement would.
PATCHED_INTERNALS = rf"{_PATCH_TARGET}_pytest\b"
PYTEST_PUBLIC_PATCH = (
    rf"{_CHAIN}\s*{_ASSIGN}"
    # in a tuple, list or parentheses: a run of target characters up to "=" that holds the chain
    # (a chain, subscripts included, is one target there unless a call follows it)
    # (the possessive run first, so a long line is read once per statement, not once per offset)
    rf"|{_START}(?=(?:{_CHAIN}(?!\s*\()|{_TARGET})*+{_ASSIGN}){_TARGET}*?{_CHAIN}"
    rf"|(?:^|[;:]){_ANNOTATED}(?:==|(?!:{_ANNOTATED})[^=#;])*=(?!=)"  # annotated ("==" inside)
    # a ``for`` target: only its first chain is tried (not every chain on a long line), and the
    # ``in`` is looked for from the chain's first attribute on, which may itself be named ``in``
    rf"|{_KEYWORD}for\b(?>{_NOT_FOR}*?(?={_CHAIN}){_OBJECT}\s*\.)(?={_FOR_TARGET}*+\bin\b)"
    rf"|{_KEYWORD}as\b[(\[\s]*{_CHAIN}\s*[:,)\]]"  # a ``with`` target
    # mock's patch, patch.object and patch.multiple, which set the attribute for the caller, and
    # setattr/delattr (monkeypatch's too) on the class or a "pytest.TestReport.x" string target
    rf"|{_PATCH_TARGET}{_OBJECT}"
    rf"|\bdel\b[^#;]*{_OBJECT}\s*\."
    r"|\bfrom_item_and_call\b"
)


# Public pytest fixture types that honest code imports from ``_pytest`` for type hints only.
# The classes whose methods decide a verdict (Item, Config, reports, runner) are not here.
_HINT = (
    r"(?:CaptureFixture|FixtureRequest|LogCaptureFixture|MonkeyPatch|Pytester|TempPathFactory"
    r"|TempdirFactory|Testdir)(?:\s+as\s+\w+)?"
)
_HINTS = rf"{_HINT}(?:\s*,\s*{_HINT})*"
PYTEST_HINT_IMPORT = (  # one complete line; an open "(" continuing on the next line is not allowed
    rf"^\s*from\s+_pytest(?:\.\w+)+\s+import\s+(?:{_HINTS}|\(\s*{_HINTS}\s*,?\s*\))\s*(?:#.*)?$"
)

# A name, attribute chain or subscript in brackets of its own, not a call's: Python reads
# ``(pytest.Item).runtest`` as ``pytest.Item.runtest``. Brackets right after a name or a closing
# bracket (spaces between included) are a call's or a subscript's and stay; after a keyword they
# are the expression's own. Nested ones are read from the inside out, to the depth Python's parser
# allows (200).
_KEYWORDS = r"and|as|assert|await|del|elif|else|for|from|if|in|is|not|or|raise|return|while|with"
_BEFORE = rf"(^|[^\w\s)\]]|(?<![\w.])(?:{_KEYWORDS}|yield))(\s*)"
_BRACKETED = re.compile(rf"{_BEFORE}\(\s*([^\W\d]\w*(?:\s*(?:\.\s*\w+|\[[^\[\]()]*\]))*)\s*\)")

# A pytest hook implementation in test code: a hook's name bound by ``def``, an assignment or an
# import, or pytest's own hookimpl marker: ``pytest.hookimpl``, ``hookimpl`` imported from pytest
# (under any name) and pluggy's marker made for the ``pytest`` project; a project's own pluggy
# marker and the word in its text pass. Not ``pytest_generate_tests``,
# which pytest reads from a test module, nor ``pytest_addoption`` or the ``pytest_plugins`` list.
_HOOK = (
    r"pytest_(?:runtest|sessionstart|sessionfinish|collect|configure|unconfigure|report"
    r"|terminal_summary|exception_interact|pyfunc_call|pycollect|make_collect_report"
    r"|itemcollected|fixture_|assertrepr|assertion_pass|make_parametrize_id|ignore_collect"
    r"|deselected|cmdline|load_initial_conftests|plugin_registered|internalerror"
    r"|keyboard_interrupt|warning_recorded|enter_pdb|leave_pdb)\w*"
)
PYTEST_HOOK_IMPL = (
    rf"^\s*(?:async\s+)?def\s+{_HOOK}|(?:^|[;:])\s*{_HOOK}\s*(?::[^=:;#]*)?=(?!=)"
    rf"|\bas\s+{_HOOK}|\bfrom\s+[\w.]+\s+import\b(?:(?!\bfrom\b)[^#;])*?(?<![\w.]){_HOOK}"
    r"|\bpytest\s*\.\s*hookimpl\b|\bfrom\s+pytest\s+import\b(?:(?!\bfrom\b)[^#;])*?\bhookimpl\b"
    r"|\bHookimplMarker\s*\(\s*(?:project_name\s*=\s*)?[rRuU]?['\"]pytest['\"]"
)


def canonical_pytest(text: str, aliases: dict[str, set[str]], unwrap: bool = True) -> str:
    """The line with an alias of pytest or of one of PYTEST_CLASSES spelled ``pytest.<Class>``.

    With ``unwrap``, a name or chain in brackets of its own is read without them first.
    """
    for _ in range(200 if unwrap else 0):
        text, count = _BRACKETED.subn(r"\1\2\3", text)
        if not count:
            break
    # Not after "." or a word character: obj.p.TestReport or xR is not the alias p or R.
    for name in sorted(aliases.get("pytest", ())):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*{_CLASS})", r"pytest\1", text)
    for cls in PYTEST_CLASSES:
        for name in sorted(aliases.get(cls, ())):
            text = re.sub(rf"(?<![\w.]){re.escape(name)}\b", f"pytest.{cls}", text)
    return text
