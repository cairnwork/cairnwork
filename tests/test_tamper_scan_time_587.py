"""#587: a rule of tamper_scan reads a long run of one shape in time linear in its length.

The scan budget counts characters, not time, so a rule that is quadratic on a long added line runs
on every capped line of a patch until the budget is spent (a blank-led line took about 0.35 s at
the 4096 cap, about a minute for a patch). Each shape below made one rule quadratic: a run of
blanks before an assignment target, and a start word (``import``, ``del``, ``from os import``,
``sys.modules[``, ``sys.modules.update(``) repeated before the thing it looks for. The shapes are
read at 100k characters, far past the cap, so a quadratic rule is seconds and a linear one is
milliseconds; the limits are far from both. The verdicts themselves are unchanged: the same lines
refused as before, checked here on short lines holding the shape and then the target.
"""

from __future__ import annotations

import time

import pytest

from cairnwork import tamper_scan as ts
from cairnwork.tamper_scan_diff import MAX_LINE

LONG = 100_000
LIMIT = 1.0  # seconds; a quadratic read of LONG takes several, a linear one a few milliseconds

# (rule name, the shape repeated, a tail that makes the rule refuse the whole line)
SHAPES = [
    ("pytest_public_patch", " ", "pytest.Item.x = 1"),
    ("pytest_public_patch", "\t", "pytest.Item.x = 1"),
    ("pytest_public_patch", "del ", "pytest.Item.x"),
    ("sys_modules_write", "del ", "sys.modules['sys']"),
    ("os_exit", "from os import a ", "_exit"),
    ("gc_walk", "from gc import a ", "get_objects"),
]
LIBRARY_SHAPES = [
    ("pytest_internals", "import a ", "_pytest"),
    ("sys_modules_write", "del a ", "sys.modules['sys']"),
    ("sys_modules_write", "sys.modules.update(a ", "'sys'"),
]
_RULES = {rule.name: rule.pattern for rule in ts.RULES}


def _read(pattern, text: str) -> float:
    start = time.perf_counter()
    pattern.search(text)
    return time.perf_counter() - start


def _pattern(name: str, library: bool):
    if library and name == "sys_modules_write":
        return ts._LIBRARY_MODULES_WRITE
    if library and name == "pytest_internals":
        return ts._LIBRARY_PYTEST.pattern
    return _RULES[name]


@pytest.mark.parametrize(("name", "unit", "tail"), SHAPES)
def test_rule_reads_a_long_run_in_linear_time(name: str, unit: str, tail: str) -> None:
    pattern = _pattern(name, library=False)
    assert _read(pattern, unit * (LONG // len(unit))) < LIMIT
    assert pattern.search((unit * 4) + tail) is not None  # the verdict is the same


@pytest.mark.parametrize(("name", "unit", "tail"), LIBRARY_SHAPES)
def test_library_rule_reads_a_long_run_in_linear_time(name: str, unit: str, tail: str) -> None:
    pattern = _pattern(name, library=True)
    assert _read(pattern, unit * (LONG // len(unit))) < LIMIT
    assert pattern.search((unit * 4) + tail) is not None


def _patch_seconds(path: str, line: str) -> tuple[float, list[str]]:
    lines = [(number, line[:MAX_LINE]) for number in range(1, 301)]
    start = time.perf_counter()
    found = ts.scan_added_lines(path, lines)
    return time.perf_counter() - start, [finding.rule for finding in found]


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("src/lib.py", " " * MAX_LINE),
        ("tests/test_x.py", " " * MAX_LINE),
        ("src/lib.py", "import a " * 500),
        ("src/lib.py", "del a " * 700),
        ("tests/test_x.py", "del a " * 700),
    ],
    ids=["blanks-lib", "blanks-test", "import-lib", "del-lib", "del-test"],
)
def test_a_patch_of_capped_lines_of_one_shape_is_read_in_seconds(path: str, line: str) -> None:
    # Through scan_added_lines: the patch ends in the budget's refusal or in no finding, within a
    # few seconds (one minute before #587 for the blank run).
    seconds, rules = _patch_seconds(path, line)
    assert seconds < 8.0, rules


def test_a_real_target_after_a_long_run_of_blanks_is_still_refused() -> None:
    line = " " * (MAX_LINE - 20) + "pytest.Item.x = 1"
    assert [f.rule for f in ts.scan_added_lines("src/lib.py", [(1, line)])] == [
        "pytest_public_patch"
    ]
    line = "import a " * 400 + "_pytest"
    assert [f.rule for f in ts.scan_added_lines("src/lib.py", [(1, line)])] == ["pytest_internals"]
