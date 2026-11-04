"""#587: pytest's collector and fixture classes are in PYTEST_CLASSES; long lines are bounded.

The class list is spelled out here so a class dropped from the module fails a test.
"""

import time

import pytest

from cairnwork.tamper_scan import scan_added_lines
from cairnwork.tamper_scan_diff import MAX_LINE
from cairnwork.tamper_scan_pytest import PYTEST_CLASSES

LIBRARY = "src/calc.py"
RULE = "pytest_public_patch"
P = "py" + "test"
NEW = (
    "Collector",
    "Class",
    "Module",
    "Package",
    "File",
    "Dir",
    "Directory",
    "DoctestItem",
    "FixtureDef",
    "TerminalReporter",
)


def _rules(snippet: str) -> set[str]:
    lines = list(enumerate(snippet.split("\n"), start=1))
    return {finding.rule for finding in scan_added_lines(LIBRARY, lines)}


def test_the_module_lists_every_new_class():
    assert set(NEW) <= set(PYTEST_CLASSES)


@pytest.mark.parametrize("cls", NEW)
def test_a_collector_or_fixture_class_is_refused_on_assignment(cls):
    assert _rules(f"{P}.{cls}.collect = run") == {RULE}


@pytest.mark.parametrize("cls", NEW)
def test_a_collector_or_fixture_class_is_refused_through_an_alias(cls):
    assert _rules(f"from {P} import {cls} as K\nK.collect = run") == {RULE}


@pytest.mark.parametrize("cls", NEW)
def test_a_parenthesised_class_is_refused(cls):
    assert _rules(f"({P}.{cls}).collect = run") == {RULE}


@pytest.mark.parametrize("cls", NEW)
def test_reads_and_type_hints_of_the_new_classes_pass(cls):
    lines = [f"x: {P}.{cls} = make()", f"ok = isinstance(y, {P}.{cls})", f"n = {P}.{cls}.name"]
    assert _rules("\n".join(lines)) == set()


def test_many_for_clauses_on_one_line_are_read_in_bounded_time():
    # Before the per-line cap this shape took seconds (#587); now it is refused as long_line.
    line = (
        "for a in b: " + f"for {P}.Item.x, " * 3 + "a in b; "
    ) * 800  # fixed size, about 40k: 14 s unbounded
    assert 40_000 <= len(line) and MAX_LINE < 40_000
    start = time.perf_counter()
    found = scan_added_lines(LIBRARY, [(1, line)], line.encode())
    assert time.perf_counter() - start < 20.0
    assert [f.rule for f in found] == ["long_line"]
