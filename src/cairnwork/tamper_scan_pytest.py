"""The ``pytest_public_patch`` rule of tamper_scan.py: source changes to pytest's public classes.

The verifier's pytest process also imports the candidate's library code, so a library file can
replace a method of a public pytest class (``pytest.TestReport.from_item_and_call``) and change
what pytest reports for a protected test. tamper_scan therefore refuses, in any file (library
code too, not only test code), an added line that assigns, augments or deletes an attribute of
one of ``PYTEST_CLASSES`` reached through ``pytest.``, that passes one of them to ``setattr`` or
``delattr``, or that mentions ``from_item_and_call`` at all. An alias bound by an added import
(``import pytest as p``, ``from pytest import TestReport as R``) is followed by tamper_scan's
alias machinery, which spells it as ``pytest.<Class>`` with ``canonical_pytest`` below. Type
hints, ``isinstance`` checks, reads of these attributes and comparisons pass.

A raised bar, not a boundary. Still passing, as known residuals: a name built from strings
(``getattr(pytest, "Test" + "Report")``), a rebinding by assignment (``R = pytest.TestReport``),
a subclass that overrides a method (a custom ``Item.runtest``), ``from pytest import *`` and an
alias bound only in an unchanged line.
"""

from __future__ import annotations

import re

PYTEST_CLASSES = (
    "TestReport",
    "CollectReport",
    "CallInfo",
    "Session",
    "Item",
    "Function",
    "Config",
    "PytestPluginManager",
)
_CLASS = rf"(?:{'|'.join(PYTEST_CLASSES)})\b"
_OBJECT = rf"\bpytest\s*\.\s*{_CLASS}"
PYTEST_PUBLIC_PATCH = (
    # assign or augment an attribute (``=``, ``+=``, ``>>=``; not ``==``, ``<=``, ``>=``)
    rf"{_OBJECT}(?:\s*\.\s*\w+)+\s*(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"
    # setattr/delattr on the class, also a "pytest.TestReport.x" string target (monkeypatch)
    rf"|\b_*(?:set|del)attr_*\s*\(\s*['\"]?{_OBJECT}"
    rf"|\bdel\s+[^#;]*{_OBJECT}\s*\."
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
