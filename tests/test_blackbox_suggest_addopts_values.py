"""tools/blackbox_suggest (#370): reporting options in addopts with a value pytest refuses.

pytest checks the value of some of its own reporting options when it parses addopts at start:
a choice it does not list or a number it cannot read stops the run, so no test runs. The reader
keeps a draft under these options only when the value is one pytest takes.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, addopts):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": TEST,
        "pytest.ini": f"[pytest]\naddopts = {addopts}\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "tb_choice": "--tb=bogus",
    "tb_empty": "--tb=",
    "color_choice": "--color=bogus",
    "code_highlight_choice": "--code-highlight=x",
    "durations_not_a_number": "--durations=x",
    "durations_fraction": "--durations=1.5",
    "durations_min_not_a_number": "--durations-min=x",
    "doctest_report_choice": "--doctest-report bogus",
}
KEPT = {
    "tb": "--tb=short",
    "tb_native": "--tb=native",
    "color": "--color=no",
    "code_highlight": "--code-highlight=yes",
    "durations": "--durations=10",
    "durations_min": "--durations-min=0.5",
    "doctest_report": "--doctest-report udiff",
}


@pytest.mark.parametrize("addopts", REFUSED.values(), ids=REFUSED.keys())
def test_a_reporting_option_with_a_value_pytest_refuses_refuses(tmp_path, addopts):
    item = run(tmp_path, f"-q {addopts}")
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("addopts", KEPT.values(), ids=KEPT.keys())
def test_a_reporting_option_with_a_value_pytest_takes_keeps_the_draft(tmp_path, addopts):
    item = run(tmp_path, f"-q {addopts}")
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
