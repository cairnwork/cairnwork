"""The ``pytest_public_patch`` rule of tamper_scan.py: source changes to listed pytest classes.

The verifier's pytest process also imports the candidate's library code, so a library file can
replace a method of a pytest class (``pytest.TestReport.from_item_and_call``) and change what
pytest reports for a protected test. tamper_scan therefore refuses, in any file (library code
too, not only test code), an added line that changes an attribute of one of ``PYTEST_CLASSES``
below (the report, item, session, config, plugin-manager, exception-info and ``raises`` matcher
classes, nothing else) reached through ``pytest.``: as the target of ``=`` or an augmented
assignment, with an annotation, inside a tuple, list or parentheses, as a ``for`` or ``with``
target (a keyword may touch a bracket or digit, as Python allows), in ``del``, passed to
``setattr``/``delattr`` or to ``unittest.mock``'s ``patch``, ``patch.object`` or
``patch.multiple``; and any line that mentions ``from_item_and_call``. An annotation may hold
``==`` and its target may be in parentheses. An alias bound by an added import
(``import pytest as p``, ``from pytest import TestReport as R``, also ``import(`` with no
space) is followed by tamper_scan's alias machinery, which spells it as ``pytest.<Class>`` with
``canonical_pytest`` below. Type hints, ``isinstance``
checks, reads, comparisons, calls and keyword arguments pass. Accepted costs (honest code also
refused): a self-documenting f-string (``f"{pytest.Item.name=}"``), ``del`` of a mapping entry
keyed by such an attribute, and a plugin that builds reports with ``from_item_and_call``.

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
_CHAIN = rf"{_OBJECT}(?:\s*\.\s*\w+)+"  # an attribute of a listed class, at any depth
_ASSIGN = r"(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"  # ``=``, ``+=``, ``>>=``; not ``==``, ``<=``
# One character of an assignment target list (names, dots, commas, stars, brackets), but not a
# call or subscript ("(" or "[" after a name or closer), so a keyword argument is no target.
_TARGET = r"(?:(?![\w.)\]]\s*[(\[])[\w\s.,*()\[\]])"
_FOR_TARGET = rf"(?:(?!\bin\b){_TARGET})"  # a ``for`` target ends at ``in``
_START = r"(?:^|[;:])\s*"  # where a statement starts on the line
_KEYWORD = r"(?<![^\W\d])"  # a keyword may follow a digit or bracket directly ("0for", ")as(")
PYTEST_PUBLIC_PATCH = (
    rf"{_CHAIN}\s*{_ASSIGN}"
    # in a tuple, list or parentheses: a run of target characters up to "=" that holds the chain
    # (the possessive run first, so a long line is read once per statement, not once per offset)
    rf"|{_START}(?={_TARGET}*+{_ASSIGN}){_TARGET}*?{_CHAIN}"
    rf"|{_START}[(\s]*{_CHAIN}[\s)]*:(?:==|[^=#;])*=(?!=)"  # with an annotation ("==" inside)
    rf"|{_KEYWORD}for\b(?={_FOR_TARGET}*+\bin\b){_FOR_TARGET}*?{_CHAIN}"
    rf"|{_KEYWORD}as\b\s*[(\[\s]*{_CHAIN}\s*[:,)\]]"  # a ``with`` target
    # unittest.mock's patch, patch.object and patch.multiple, which set the attribute for the caller
    rf"|\bpatch(?:\s*\.\s*(?:object|multiple))?\s*\(\s*['\"]?{_OBJECT}"
    # setattr/delattr on the class, also unpacked or a "pytest.TestReport.x" string target
    rf"|\b_*(?:set|del)attr_*\s*\(\s*[*(\s]*['\"]?{_OBJECT}"
    rf"|\bdel\b[^#;]*{_OBJECT}\s*\."
    r"|\bfrom_item_and_call\b"
)


def canonical_pytest(text: str, aliases: dict[str, set[str]]) -> str:
    """The line with an alias of pytest or of one of PYTEST_CLASSES spelled ``pytest.<Class>``."""
    # Not after "." or a word character: obj.p.TestReport or xR is not the alias p or R.
    for name in sorted(aliases.get("pytest", ())):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*{_CLASS})", r"pytest\1", text)
    for cls in PYTEST_CLASSES:
        for name in sorted(aliases.get(cls, ())):
            text = re.sub(rf"(?<![\w.]){re.escape(name)}\b", f"pytest.{cls}", text)
    return text
