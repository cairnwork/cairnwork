"""tools/blackbox_suggest (#303): the draft declaration, the coverage numbers and the CLI."""

import json
import os
import stat

import pytest

from cairnwork.blackbox import parse
from tools.blackbox_suggest import Obligation, analyze_file, default_roots
from tools.blackbox_suggest.__main__ import main
from tools.blackbox_suggest.coverage import aggregate, category, task_coverage
from tools.blackbox_suggest.render import declaration, markdown, report, write_declaration

TESTS = """\
import pytest
from pkg.core import add, half

@pytest.mark.parametrize("a,out", [(1, 1), (2, 2), (3, 3)])
def test_table(a, out):
    assert add(a, 0) == out

def test_bool():
    assert add(True, False) is True

def test_float():
    assert half(3) == 1.5

def test_fixture(tmp_path):
    assert add(1, 1) == 2
"""


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("")
    (tmp_path / "src/pkg/core.py").write_text("def add(a, b):\n    pass\ndef half(x):\n    pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text(TESTS)
    return tmp_path


def obligations(proj):
    return analyze_file(proj / "tests/test_a.py", proj, default_roots(proj))


def test_declaration_holds_only_todays_kinds_and_parses_with_the_verifiers_parser(proj, tmp_path):
    draft = declaration(obligations(proj))
    assert draft is not None
    out = tmp_path / "decl.json"
    write_declaration(out, draft)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    parsed = parse(out.read_bytes())
    assert [(c.module, c.function) for c in parsed.checks] == [("pkg.core", "add")]
    cases = parsed.checks[0].cases
    assert [c.expected for c in cases] == ["1", "2", "3", "true"]  # no float, no fixture test
    assert len({c.id for c in cases}) == 4
    with pytest.raises(FileExistsError):
        write_declaration(out, draft)
    assert declaration(obligations(proj))["salt"] != draft["salt"]  # type: ignore[index]


def test_a_draft_the_verifier_refuses_is_never_written(tmp_path):
    bad = {"salt": "0" * 64, "checks": [{"id": "x", "module": "m", "function": "f", "cases": []}]}
    with pytest.raises(ValueError):
        write_declaration(tmp_path / "d.json", bad)
    assert not (tmp_path / "d.json").exists()


def test_more_than_100_cases_are_split_into_checks(tmp_path):
    case = {"line": 1, "target": {"module": "m", "function": "f"}, "args": [1], "kwargs": {},
            "expected": {"value": 1, "compare": "exact"}, "needs": ["function"]}  # fmt: skip
    many = [Obligation(f"t.py::test_{i}", "t.py", 1, True, None, [case]) for i in range(150)]
    draft = declaration(many)
    assert draft is not None and [len(c["cases"]) for c in draft["checks"]] == [100, 50]
    parse(json.dumps(draft).encode())


def test_no_declarable_obligation_gives_no_declaration():
    refused = [Obligation("t.py::test_a", "t.py", 1, True, "uses fixture(s): x", [])]
    assert declaration(refused) is None


def test_task_coverage_counts_every_obligation_in_the_denominator(proj):
    now = task_coverage(obligations(proj), {"function"})
    assert (now["obligations"], now["protected"]) == (6, 4) and now["exact"]
    assert now["coverage"] == pytest.approx(4 / 6)
    assert now["reasons"] == {"uses fixture(s) …": 1, "needs float (not declarable here)": 1}
    later = task_coverage(obligations(proj), {"function", "float"})
    assert later["protected"] == 5


def test_aggregate_reports_mean_and_fully_protected_share():
    tasks = [
        {"obligations": 4, "protected": 4, "coverage": 1.0, "exact": True, "reasons": {}},
        {"obligations": 4, "protected": 1, "coverage": 0.25, "exact": True, "reasons": {"a": 3}},
        {"obligations": 2, "protected": 0, "coverage": 0.0, "exact": False, "reasons": {"a": 2}},
        {"obligations": 0, "protected": 0, "coverage": None, "exact": True, "reasons": {}},
    ]
    agg = aggregate(tasks)
    assert agg["mean_task_coverage"] == pytest.approx((1.0 + 0.25 + 0.0) / 3)
    assert agg["fully_protected_share"] == pytest.approx(1 / 3)
    assert (agg["obligations"], agg["protected"], agg["tasks_without_obligations"]) == (10, 5, 1)
    assert agg["tasks_count_exact"] == 2 and agg["unprotected_reasons"] == {"a": 5}


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("line 4: 'helper' is defined in the test file, not in the project",
         "… is defined in the test file, not in the project"),
        ("uses fixture(s): secret_fixture", "uses fixture(s) …"),
        ("mypkg is not a module of the project (stdlib or a dependency?)",
         "… is not a module of the project (stdlib or a dependency?)"),
        ("cannot find 'f' in mypkg by reading its source",
         "cannot find … in … by reading its source"),
        ("line 9: with freeze_time(...) is not pytest.raises", "with …(...) is not …"),
        ("line 2: a.b.c is not a top-level name of a project module",
         "… is not a top-level name of a project module"),
    ],
)  # fmt: skip
def test_reason_categories_carry_no_names(reason, expected):
    assert category(reason) == expected


def test_report_and_markdown(proj):
    data = report(obligations(proj), {"function"})
    assert (data["obligations"], data["protected"], data["count_exact"]) == (6, 4, True)
    text = markdown(data)
    assert "tests/test_a.py:5" in text and "refused" in text and "exact JSON types" in text
    assert "pkg.core.add(args=[1, 0], kwargs={}) -> 1" in text


def test_cli_writes_report_markdown_and_declaration(proj, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    rc = main(["--project", str(proj), "--json", str(out / "r.json"), "--markdown",
               str(out / "r.md"), "--declaration", str(out / "d.json"),
               str(proj / "tests/test_a.py")])  # fmt: skip
    assert rc == 0
    assert json.loads((out / "r.json").read_text())["protected"] == 4
    assert (out / "r.md").exists() and parse((out / "d.json").read_bytes())
    assert main(["--project", str(proj), "--kinds", "function,float",
                 str(proj / "tests/test_a.py")]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["protected"] == 5


@pytest.mark.parametrize("argv_tail", [["--kinds", "teleport"], []])
def test_cli_refuses_unknown_kinds_and_tests_outside_the_project(proj, tmp_path, argv_tail):
    outside = tmp_path.parent / "elsewhere_test.py"
    outside.write_text("def test_x():\n    pass\n")
    target = str(proj / "tests/test_a.py") if argv_tail else str(outside)
    with pytest.raises(SystemExit) as exc:
        main(["--project", str(proj), *argv_tail, target])
    assert exc.value.code == 2
