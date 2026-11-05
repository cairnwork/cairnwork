"""tamper_scan: the scan budget of one patch (#668) and the cost of aliases (#665).

Each source of unbounded scan time is refused with ``scan_budget``, counted in characters and
names (no wall-time limit): many aliases bound in one file, a committed file whose unchanged rows
are too costly to tokenize, a line whose text grows past the line cap once aliases are spelled out,
and many lines that add up within one patch. Past the budget the patch is refused, never passed.
"""

import re
import sys
import types

import pytest
from conftest import git

import cairnwork.tamper_scan as scan
import cairnwork.tamper_scan_budget as budget_module
import cairnwork.tamper_scan_pytest as pytest_module
from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_budget import (
    MAX_ALIASES,
    MAX_READ,
    MAX_TOKENIZED,
    ROW_COST,
    SCAN_BUDGET,
    ScanBudget,
    canonical,
)
from cairnwork.tamper_scan_diff import MAX_LINE

TEST = "tests/test_x.py"
LIBRARY = "pkg/module.py"
BOTH = pytest.mark.parametrize("path", [TEST, LIBRARY])
MODULES = "from sys import modules as m"


def _rules(path, texts, data=None, budget=None):
    lines = [(number, text) for number, text in enumerate(texts, start=1)]
    return [(f.line, f.rule) for f in scan_added_lines(path, lines, data, budget)]


def _commit(repo, files: dict[str, str]) -> tuple[str, str]:
    base = git(repo, "rev-parse", "HEAD")
    for name, text in files.items():
        (repo / "src" / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    return base, git(repo, "rev-parse", "HEAD")


# --- aliases (#665) ------------------------------------------------------------------------------
@BOTH
@pytest.mark.parametrize("bind", ["import os as a{}", "from pytest import Item as a{}"])
def test_a_file_may_bind_up_to_max_aliases_names(path, bind):
    texts = [bind.format(i) for i in range(MAX_ALIASES)]
    assert _rules(path, texts) == []
    assert _rules(path, [*texts, bind.format(MAX_ALIASES)]) == [(MAX_ALIASES + 1, SCAN_BUDGET)]


@BOTH
def test_thousands_of_alias_lines_are_refused_after_the_cap_and_not_read_further(path):
    texts = [f"from pytest import Item as a{i}" for i in range(3000)] + ["import os", "os._exit(0)"]
    assert _rules(path, texts) == [(MAX_ALIASES + 1, SCAN_BUDGET)]


def _count_substitutions(monkeypatch) -> list[str]:
    calls: list[str] = []

    def sub(pattern, repl, text):
        calls.append(pattern)
        return re.sub(pattern, repl, text)

    stand_in = types.SimpleNamespace(sub=sub, escape=re.escape)
    monkeypatch.setattr(budget_module, "re", stand_in)
    monkeypatch.setattr(pytest_module, "re", stand_in)
    return calls


@BOTH
def test_a_line_without_an_alias_name_costs_no_substitution(path, monkeypatch):
    calls = _count_substitutions(monkeypatch)
    binds = ["import os as osa", "import sys as sysa", "import gc as gca", MODULES]
    binds += ["import pytest as pa", "from pytest import Item as it"]
    assert _rules(path, binds) == []
    bound = len(calls)  # the import lines hold their own names
    calls.clear()
    assert _rules(path, [*binds, *["x = 1 + y"] * 200]) == []
    assert len(calls) == bound
    calls.clear()
    assert _rules(path, [*binds, "osa._exit(0)"]) == [(len(binds) + 1, "os_exit")]
    assert len(calls) > 0


def test_skipping_absent_names_keeps_a_spelling_that_an_earlier_one_brings_in():
    # "a" is spelled "sys.modules" first; the alias "sys" then reads that "sys" as well, as before.
    aliases = {key: set() for key in ("sys", "os", "gc", "_exit")} | {"modules": {"a", "sys"}}
    assert "sys.modules.modules[0] = 1" in canonical("a[0] = 1", aliases)
    pytest_aliases = {key: set() for key in ("sys", "os", "gc", "modules", "_exit")}
    pytest_aliases |= {"Item": {"x"}, "Function": {"pytest"}}
    assert "pytest.Function.Item.runtest = f" in canonical("x.runtest = f", pytest_aliases)


# --- the text read after alias spelling (#668) ---------------------------------------------------
@BOTH
def test_a_line_spelled_out_past_the_line_cap_is_refused(path):
    raw = "m" + " " * (MAX_LINE - len("sys.modules"))  # "sys.modules" and spaces: exactly the cap
    assert len(raw) <= MAX_LINE
    assert _rules(path, [MODULES, raw]) == []
    assert _rules(path, [MODULES, raw + " "]) == [(2, SCAN_BUDGET)]


@BOTH
def test_alias_lines_under_the_raw_cap_that_grow_past_it_are_refused_on_the_first(path):
    line = ("del m " * MAX_LINE)[: MAX_LINE - 6]  # about 3.5 times longer once spelled out
    texts = [MODULES, *[line] * 32, "import os", "os._exit(0)"]
    assert _rules(path, texts) == [(2, SCAN_BUDGET)]


def test_a_line_whose_nfkc_form_passes_the_cap_is_refused():
    assert _rules(LIBRARY, ["x = " + "ﬁ" * 2000]) == []  # "fi": twice as long, under the cap
    assert _rules(LIBRARY, ["x = " + "ﬃ" * 1400]) == [(1, SCAN_BUDGET)]  # "ffi": past it


# --- tokenizing the committed file (#668) --------------------------------------------------------
def _with_context_row(length: int) -> bytes:
    return ("ROW = [" + "1," * (length // 2) + "]\nx = 1\n").encode()


def test_a_committed_file_past_the_tokenize_budget_is_refused_untokenized(monkeypatch):
    monkeypatch.setattr(scan, "file_views", lambda data: pytest.fail("tokenized"))
    data = _with_context_row(260 * 1024)  # an unchanged row: counted once per started MAX_LINE
    row = len(data.split(b"\n")[0])
    assert row * -(-row // MAX_LINE) > MAX_TOKENIZED
    assert scan_added_lines(TEST, [(2, "x = 1")], data)[0].rule == SCAN_BUDGET


def test_an_unchanged_long_row_within_the_budget_is_still_tokenized_and_read(monkeypatch):
    tokenized = []
    real = scan.file_views
    monkeypatch.setattr(scan, "file_views", lambda data: tokenized.append(1) or real(data))
    data = _with_context_row(64 * 1024) + b"import os as o\no._exit(0)\n"
    lines = [(3, "import os as o"), (4, "o._exit(0)")]
    assert [(f.line, f.rule) for f in scan_added_lines(TEST, lines, data)] == [(4, "os_exit")]
    assert tokenized == [1]


def test_the_tokenize_budget_is_one_per_patch():
    data = b"x = 1\n" * 100
    budget = ScanBudget(tokenized=3 * (100 * (5 + ROW_COST) + ROW_COST) // 2)  # 1.5 files
    assert _rules(TEST, ["x = 1"], data, budget) == []
    assert _rules(TEST, ["x = 1"], data, budget) == [(1, SCAN_BUDGET)]


# --- one budget per patch, refused when over (#668) ----------------------------------------------
def test_lines_that_add_up_past_the_patch_budget_refuse_the_patch(repo):
    line = ("value = alpha + beta  " * 200)[:4000] + "\n"
    per_file = MAX_READ * 6 // 10 // (4 * len(line)) * line  # each file reads ~60% of the budget
    base, head = _commit(repo, {"a.py": per_file, "b.py": per_file})
    found = scan_patch(
        repo, base, head, python=sys.executable
    )  # the files are not valid Python: also hook_file_touched
    assert [(f.path, f.rule) for f in found if f.rule == SCAN_BUDGET] == [("src/b.py", SCAN_BUDGET)]
    base, head = _commit(repo, {"a.py": "x = 2\n", "b.py": "y = 2\n"})
    assert scan_patch(repo, base, head, python=sys.executable) == []


@BOTH
def test_past_the_budget_the_file_is_refused_never_passed(path):
    """Whatever the budget runs out on, the result is the full one or holds scan_budget."""
    texts = ["import os as o", "x = (a)  # note", "s = 'a;b'; t = 1", "o._exit(0)", "y = 2"]
    full = _rules(path, texts)
    assert full == [(4, "os_exit")]
    needed = _spent(path, texts)
    for read in range(needed + 2):
        found = _rules(path, texts, budget=ScanBudget(read=read))
        assert found == full if read >= needed else (SCAN_BUDGET in {rule for _, rule in found})


def _spent(path, texts) -> int:
    budget = ScanBudget()
    _rules(path, texts, budget=budget)
    return MAX_READ - budget.read_left


@pytest.mark.parametrize("text", ["", "#", "\r" * 64, "x"])
def test_short_or_empty_lines_still_spend_the_budget(text):
    """Every line costs a pass of the rules, so many short lines run out too (#668)."""
    texts = [text] * 2000
    assert SCAN_BUDGET in {rule for _, rule in _rules(LIBRARY, texts, budget=ScanBudget(read=1000))}


def test_empty_rows_still_spend_the_tokenize_budget():
    budget = ScanBudget(tokenized=1000)
    assert not budget.tokenize(b"\n" * 2000)


def test_a_row_costs_the_row_cost_and_a_reading_costs_one_more_than_its_length():
    budget = ScanBudget()
    budget.tokenize(b"ab\n\n")  # rows "ab", "", "": three rows
    assert MAX_TOKENIZED - budget.tokenized_left == 2 + 3 * ROW_COST
    budget = ScanBudget()
    budget.tokenize(b"a\rb\r\nc")  # CR and CRLF end rows as file_views reads them: "a", "b", "c"
    assert MAX_TOKENIZED - budget.tokenized_left == 3 + 3 * ROW_COST
    budget.read(["abc", "", "de"])  # one extra per reading, pinned (#690)
    assert MAX_READ - budget.read_left == 3 + 0 + 2 + 3


def test_many_empty_rows_are_refused_before_file_views_runs(monkeypatch):
    """An empty row costs the tokenizer ~760 bytes and ~3 us, so the budget stops them early (#690).

    Counted, not timed: at most MAX_TOKENIZED // ROW_COST empty rows reach ``file_views``.
    """
    monkeypatch.setattr(scan, "file_views", lambda data: pytest.fail("tokenized"))
    for blank in (b"\n", b" \n", b"\t\n", b"\r", b"\r\n"):  # file_views splits on CR too
        data = blank * (MAX_TOKENIZED // 64 + 1)  # 64: what an empty row really costs
        assert scan_added_lines(TEST, [(1, "x = 1")], data)[0].rule == SCAN_BUDGET
    assert ScanBudget().tokenize(b"\n" * (MAX_TOKENIZED // ROW_COST - 2))


def test_a_large_honest_module_stays_inside_the_budget():
    """Row charging must not refuse ordinary code: a 1 MB module of plain statements passes."""
    data = ("".join(f"value_{i} = compute({i}, 'label-{i}')\n" for i in range(30_000))).encode()
    assert len(data) > 1_000_000
    assert ScanBudget().tokenize(data)
    assert ScanBudget().tokenize(data.replace(b"\n", b"\r\n"))  # Windows line ends cost the same
