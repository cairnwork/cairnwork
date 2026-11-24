"""tools/blackbox_suggest (#370): pytest's test-case verbosity needs a value pytest can read.

pytest reads ``verbosity_test_cases`` with ``int()`` unless the value is exactly ``auto``; any
other value is an internal error once tests are collected, so no test result comes back. A
configuration with such a value refuses. ``verbosity_assertions`` is different: pytest reports
the assertion without the detail and the test result stands, so it keeps the draft.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x * 2\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"


def run(tmp_path, name, text):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": TEST,
        name: text,
    }
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def ini(key, value):
    return "pytest.ini", f"[pytest]\n{key} = {value}\n"


def toml(key, value):
    return "pyproject.toml", f"[tool.pytest.ini_options]\n{key} = {value}\n"


REFUSED = {
    "test_cases_word": ini("verbosity_test_cases", "bogus"),
    "test_cases_empty": ini("verbosity_test_cases", ""),
    "test_cases_fraction": ini("verbosity_test_cases", "1.5"),
    "test_cases_auto_case": ini("verbosity_test_cases", "Auto"),
    "toml_word": toml("verbosity_test_cases", '"bogus"'),
    "toml_bool": toml("verbosity_test_cases", "true"),
    "toml_float": toml("verbosity_test_cases", "1.0"),
}


@pytest.mark.parametrize("name,text", REFUSED.values(), ids=REFUSED.keys())
def test_a_verbosity_level_pytest_cannot_read_refuses(tmp_path, name, text):
    item = run(tmp_path, name, text)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "test_cases_number": ini("verbosity_test_cases", "2"),
    "assertions_number": ini("verbosity_assertions", "2"),
    "test_cases_auto": ini("verbosity_test_cases", "auto"),
    "test_cases_negative": ini("verbosity_test_cases", "-1"),
    "test_cases_signed": ini("verbosity_test_cases", "+1"),
    "toml_int": toml("verbosity_test_cases", "2"),
    "toml_auto": toml("verbosity_test_cases", '"auto"'),
    "assertions_word": ini("verbosity_assertions", "bogus"),
    "assertions_toml_float": toml("verbosity_assertions", "1.0"),
}


@pytest.mark.parametrize("name,text", KEPT.values(), ids=KEPT.keys())
def test_a_verbosity_level_pytest_reads_keeps_the_draft(tmp_path, name, text):
    item = run(tmp_path, name, text)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
