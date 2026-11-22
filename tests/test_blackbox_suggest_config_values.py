"""tools/blackbox_suggest (#370): pytest configuration pytest refuses to load.

configparser and tomllib read more than pytest does: pytest reads ini files with iniconfig, takes
no type coercion in a native TOML table and checks some option values at start. A configuration
pytest refuses runs no test, so the draft refuses too.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, extra):
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": CORE, "tests/test_x.py": TEST, **extra}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def ini(body, name="pytest.ini", section="pytest"):
    return {name: f"[{section}]\n{body}"}


REFUSED = {
    "native_minversion_number": {"pyproject.toml": "[tool.pytest]\nminversion = 7.0\n"},
    "pytest_toml_minversion_number": {"pytest.toml": "[pytest]\nminversion = 7.0\n"},
    "native_addopts_string": {"pyproject.toml": '[tool.pytest]\naddopts = "-q"\n'},
    "native_testpaths_string": {"pyproject.toml": '[tool.pytest]\ntestpaths = "tests"\n'},
    "native_bool_string": {"pyproject.toml": '[tool.pytest]\nlog_cli = "true"\n'},
    "ini_options_minversion_list": {
        "pyproject.toml": '[tool.pytest.ini_options]\nminversion = ["7.0"]\n'
    },
    "section_line_with_more": {"pytest.ini": "[pytest] x\nminversion = 7.0\n"},
    "indented_first_line": ini("  minversion = 7.0\n"),
    "indented_first_line_setup_cfg": ini("  minversion = 7.0\n", "setup.cfg", "tool:pytest"),
    "minversion_above_installed": ini("minversion = 99\n"),
    "minversion_with_comment": ini("minversion = 7.0 # c\n"),
    "log_level_unknown": ini("log_level = BOGUS\n"),
    "bool_unknown": ini("xfail_strict = maybe\n"),
    "empty_parameter_set_mark_unknown": ini("empty_parameter_set_mark = bogus\n"),
    "retention_policy_unknown": ini("tmp_path_retention_policy = bogus\n"),
    "retention_count_not_a_number": ini("tmp_path_retention_count = many\n"),
    "faulthandler_timeout_not_a_number": ini("faulthandler_timeout = abc\n"),
    "doctest_report_without_value": ini("addopts = --doctest-report --doctest-modules\n"),
    "cov_report_without_value": ini("addopts = --cov-report --doctest-modules\n"),
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_a_configuration_pytest_refuses_to_load_refuses(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "ini_values": ini(
        "minversion = 7.0\nlog_level = info\nlog_cli_level = 20\nxfail_strict = true\n"
        "faulthandler_timeout = 5\ntmp_path_retention_count = 3\n"
        "empty_parameter_set_mark = xfail\naddopts = --cov-report term -q\n"
        "markers =\n    slow: slow tests\n"
    ),
    "native_values": {
        "pyproject.toml": '[tool.pytest]\nminversion = "7.0"\nxfail_strict = true\n'
        'testpaths = ["tests"]\nlog_level = "INFO"\n'
    },
    "ini_options_coerced": {
        "pyproject.toml": "[tool.pytest.ini_options]\nminversion = 7.0\nxfail_strict = true\n"
    },
    "setup_cfg_other_sections": {
        "setup.cfg": "[metadata]\nname = x\n[options]\ninstall_requires =\n    requests\n"
        "[tool:pytest]\nminversion = 7.0\n"
    },
}


@pytest.mark.parametrize("extra", KEPT.values(), ids=KEPT.keys())
def test_a_configuration_pytest_loads_keeps_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)
