"""tools/blackbox_suggest (#370): a pytest configuration that pytest itself cannot load refuses
the draft instead of being skipped (the same rule as #947 for the core's own reader)."""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(*a, **k):\n    return 1\n"
PLAIN = "from pkg.core import f\ndef test_a():\n    assert f(2) == 1\n"


def run(tmp_path, extra):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("")
    (tmp_path / "src/pkg/core.py").write_text(CORE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(PLAIN)
    for rel, text in extra.items():
        (tmp_path / rel).write_text(textwrap.dedent(text))
    (item,) = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return item


REFUSED = {
    "setup_cfg_with_only_a_pytest_section": {"setup.cfg": "[pytest]\nminversion = 7.0\n"},
    "setup_cfg_with_an_empty_pytest_section": {"setup.cfg": "[pytest]\n"},
    "setup_cfg_in_the_test_directory": {"tests/setup.cfg": "[pytest]\nxfail_strict = true\n"},
    "pyproject_pytest_table_is_a_string": {"pyproject.toml": '[tool]\npytest = "x"\n'},
    "pyproject_ini_options_is_a_number": {"pyproject.toml": "[tool.pytest]\nini_options = 3\n"},
    "pyproject_tool_is_a_number": {"pyproject.toml": "tool = 1\n"},
}


@pytest.mark.parametrize("name", list(REFUSED))
def test_a_configuration_pytest_cannot_load_refuses(tmp_path, name):
    item = run(tmp_path, REFUSED[name])
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "setup_cfg_with_both_sections": {
        "setup.cfg": "[pytest]\nx = 1\n[tool:pytest]\nminversion = 7.0\n"
    },
    "setup_cfg_without_pytest": {"setup.cfg": "[metadata]\nname = x\n"},
    "pyproject_without_pytest": {"pyproject.toml": "[tool.ruff]\nline-length = 100\n"},
    "pyproject_tool_pytest_without_ini_options": {
        "pyproject.toml": "[tool.pytest]\nminversion = '9.0'\n"
    },
}


@pytest.mark.parametrize("name", list(KEPT))
def test_a_configuration_pytest_loads_keeps_the_draft(tmp_path, name):
    item = run(tmp_path, KEPT[name])
    assert item.protected(KINDS_NOW) and item.refusal is None, item.reason(KINDS_NOW)
