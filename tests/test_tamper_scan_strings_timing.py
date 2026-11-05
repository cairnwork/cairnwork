"""tamper_scan: the string-built observer costs a linear number of steps (STRING-BUILT-v2, 8.1).

Two layers that must both hold, the same shape at 100 KB, 200 KB and 400 KB (a doubling of the
input at most doubles the work, ratio <= 2.2): the observer's own step counter, and a
``sys.settrace`` count of the lines run inside the observer's modules, which does not trust that
counter. The wall clock is only a loose ceiling (3 s) against a disaster, not evidence.
"""

import sys
import time
from itertools import pairwise

import pytest

import cairnwork.tamper_scan as scan
from cairnwork.tamper_scan import scan_added_lines
from cairnwork.tamper_scan_budget import MAX_TOKENIZED, SCAN_BUDGET
from cairnwork.tamper_scan_strings import StringScan
from cairnwork.tamper_scan_tokens import file_views

SIZES = (100_000, 200_000, 400_000)
RATIO = 2.2
_NAMES = ("tamper_scan_strings.py", "tamper_scan_fold.py")


def _rows(row: str, size: int) -> str:
    return (row + "\n") * -(-size // len(row + "\n"))


def _short_pieces(size: int) -> str:  # T2: 1-2 character literals joined by +, 600+ rows
    row = "x = getattr(m, " + " + ".join(['"a"', '"bc"'] * 400) + ")"
    return _rows(row, size)


def _empty_long_row(size: int) -> str:  # T3: empty strings, one 16000-piece row at a time
    row = "x = getattr(m, " + " + ".join(['""'] * 16000) + ")"
    return _rows(row, size)


def _empty_many_rows(size: int) -> str:  # T3: 1358 empty strings per row, 600 rows and more
    return _rows("x = getattr(m, " + " + ".join(['""'] * 1358) + ")", size)


def _quotes_in_comments(size: int) -> str:  # T4: odd quotes and backslashes in comments, strings
    first = 'x = getattr(m, "pytest_" + "co")  # ' + "\"'\\ " * 300
    second = 'y = ["\'", \'"\', """a"b\'c""", "\\"", \'\\\\\']  # \' " \\ \' "'
    return _rows(first, size // 2) + _rows(second, size // 2)


def _mixed(size: int) -> str:  # T5: quotes, nesting, a wrapped call, f-strings, deep brackets
    deep = "f" + "(" * 40 + ")" * 40
    row = (
        'a = getattr(m, ("x" + f"{y}") + \'q\', None); b = globals()["k" "j"]\n'
        'c = getattr(\n    m,\n    "pytest_"\n    + "configure",\n    f"{x!r:>{w}}",\n)\n'
        f"d = getattr(m, {deep}, 'z') + [{{'a': (1, [2])}}]\n"
    )
    return row * -(-size // len(row))


def _many_assignments(size: int) -> str:  # constants: the table fills and stays full
    return "".join(f'n{i} = "plug"\nx{i} = getattr(c, n{i} + "in")\n' for i in range(size // 40))


SHAPES = {
    "short-pieces": _short_pieces,
    "empty-long-row": _empty_long_row,
    "empty-many-rows": _empty_many_rows,
    "quotes-in-comments": _quotes_in_comments,
    "mixed": _mixed,
    "assignments": _many_assignments,
}


def _product_steps(text: str) -> int:
    observer = StringScan()
    assert file_views(text.encode(), observer) is not None
    return observer.steps


def _traced_lines(text: str) -> int:
    count = 0

    def local(frame, event, arg):
        nonlocal count
        count += event == "line"
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename.endswith(_NAMES) else None

    observer = StringScan()
    data = text.encode()
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        file_views(data, observer)
    finally:
        sys.settrace(previous)
    return count


@pytest.mark.parametrize("name", list(SHAPES))
def test_the_work_grows_no_faster_than_the_input_by_either_count(name):
    texts = [SHAPES[name](size) for size in SIZES]
    assert all(len(text) >= size * 0.9 for text, size in zip(texts, SIZES, strict=True))
    for count in (_product_steps, _traced_lines):
        work = [count(text) for text in texts]
        sizes = [len(text) for text in texts]
        for (w0, w1), (s0, s1) in zip(pairwise(work), pairwise(sizes), strict=True):
            assert w1 / w0 <= RATIO * (s1 / s0) / 2, (name, count.__name__, work, sizes)


@pytest.mark.parametrize("name", list(SHAPES))
def test_one_scan_of_the_biggest_input_stays_under_a_loose_wall_clock_ceiling(name):
    text = SHAPES[name](SIZES[-1])
    started = time.monotonic()
    scan_added_lines("pkg/m.py", [(1, "x = 1")], text.encode())
    assert time.monotonic() - started < 3


def test_a_file_past_the_tokenize_budget_is_scan_budget_and_the_observer_never_runs(monkeypatch):
    seen: list[StringScan] = []

    class Spy(StringScan):
        def __init__(self) -> None:
            super().__init__()
            seen.append(self)

    monkeypatch.setattr(scan, "StringScan", Spy)
    text = _rows('x = getattr(m, "a" + "b")', MAX_TOKENIZED + 1_000_000).encode()
    found = scan_added_lines("pkg/m.py", [(1, "x = 1")], text)
    assert [f.rule for f in found] == [SCAN_BUDGET]
    assert sum(one.steps for one in seen) == 0  # refused before any observer ran


def test_thousands_of_small_new_files_cost_steps_in_proportion_to_their_bytes(monkeypatch):
    seen: list[StringScan] = []

    class Spy(StringScan):
        def __init__(self) -> None:
            super().__init__()
            seen.append(self)

    monkeypatch.setattr(scan, "StringScan", Spy)
    body = 'import os\nx = getattr(m, "a" + "b")\ny = {"k": [1, 2, 3]}\n'
    for _ in range(2000):
        scan_added_lines("pkg/m.py", [(2, body.split("\n")[1])], body.encode())
    assert sum(one.steps for one in seen) <= 12 * 2000 * len(body)


@pytest.mark.parametrize(
    "source", ["x = 'a\ny = getattr(m, '' + '')\n" * 50, '"' * 300 + "\\" * 50]
)
def test_a_file_with_no_token_stream_costs_nothing_and_raises_nothing(source):
    observer = StringScan()
    started = time.monotonic()
    assert file_views(source.encode(), observer) is None
    assert observer.steps == 0
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("deep", [31, 32, 33, 200, 5000])
def test_brackets_past_the_depth_limit_do_not_raise_or_refuse(deep):
    text = "getattr(m, " + "(" * deep + ")" * deep + ")\n"
    observer = StringScan()
    if file_views(text.encode(), observer) is not None:
        assert observer.findings({1}) == []
