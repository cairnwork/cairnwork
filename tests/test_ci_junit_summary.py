"""scripts/ci_junit_summary.py turns a pytest JUnit XML into a per-file markdown table (#1492)."""

from __future__ import annotations

from pathlib import Path

from scripts import ci_junit_summary as summary

XML = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="t1" file="tests/test_a.py" time="2.5"/>
<testcase classname="tests.test_a" name="t2" file="tests/test_a.py" time="1.5"><failure/></testcase>
<testcase classname="tests.test_b" name="t3" file="tests/test_b.py" time="10"/>
<testcase classname="tests.test_b" name="t4" file="tests/test_b.py" time="0"><skipped/></testcase>
<testcase classname="tests.test_c.TestX" name="t5" time="1"/>
</testsuite></testsuites>"""


def test_per_file_time_outcomes_and_totals(tmp_path: Path):
    path = tmp_path / "j.xml"
    path.write_text(XML)
    files, totals = summary.summarize(path)
    assert files["tests/test_a.py"] == {"seconds": 4.0, "passed": 1, "failed": 1, "skipped": 0}
    assert files["tests/test_b.py"] == {"seconds": 10.0, "passed": 1, "failed": 0, "skipped": 1}
    assert "tests/test_c/TestX.py" in files  # no file attribute: derived from the classname
    assert (totals["passed"], totals["failed"], totals["skipped"]) == (3, 1, 1)


def test_the_table_lists_the_slowest_file_first_and_names_the_failing_files(tmp_path: Path):
    path = tmp_path / "j.xml"
    path.write_text(XML)
    text = summary.render(path, "probe", 2)
    table = text.split("| seconds")[1]
    assert table.index("tests/test_b.py") < table.index("tests/test_a.py")
    assert "Files with failures: `tests/test_a.py`" in text
    assert "tests/test_c/TestX.py" not in text  # top 2 only


def test_an_unreadable_file_is_one_line_not_an_exception(tmp_path: Path):
    assert "no JUnit summary" in summary.render(tmp_path / "missing.xml", "probe", 5)


def test_a_malformed_time_is_one_line_not_an_exception(tmp_path: Path):
    path = tmp_path / "j.xml"
    case = '<testcase name="t" file="tests/a.py" time="abc"/>'
    path.write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
    assert "no JUnit summary" in summary.render(path, "probe", 5)


def test_a_file_name_cannot_break_the_table_or_the_code_span(tmp_path: Path):
    path = tmp_path / "j.xml"
    case = '<testcase name="t" file="tests/a|b`c.py" time="1"/>'
    path.write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
    row = [line for line in summary.render(path, "probe", 5).splitlines() if "a" in line][-1]
    assert "tests/a\\|b'c.py" in row and row.count("|") == 7  # 5 cells + 1 escaped + edge


def test_an_unknown_xml_encoding_declaration_is_one_line_not_an_exception(tmp_path: Path):
    path = tmp_path / "j.xml"
    path.write_text('<?xml version="1.0" encoding="no-such-codec"?><testsuites/>')
    assert "no JUnit summary" in summary.render(path, "probe", 5)
