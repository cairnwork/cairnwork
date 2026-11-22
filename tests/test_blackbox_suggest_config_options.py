"""tools/blackbox_suggest (#370): pytest options that run or change project code.

pytest imports a warning category named by a filterwarnings entry, opens the log file at start,
imports every module a python_files pattern collects and can turn off one of its own plugins with
``-p no:``. Each of these refuses unless it is known to leave the tests alone.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
SIDE = "import pkg.core\npkg.core.SCALE = 10\nclass Warn(Warning):\n    pass\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, extra):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "src/pkg/side.py": SIDE,
        "tests/test_x.py": TEST,
        **extra,
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "project_category_ini": {
        "pytest.ini": "[pytest]\nfilterwarnings =\n    ignore::pkg.side.Warn\n"
    },
    "project_category_native": {
        "pyproject.toml": '[tool.pytest]\nfilterwarnings = ["ignore::pkg.side.Warn"]\n'
    },
    "project_category_ini_options": {
        "pyproject.toml": "[tool.pytest.ini_options]\n"
        'filterwarnings = ["default:m:pkg.side.Warn"]\n'
    },
    "project_category_after_a_builtin": {
        "setup.cfg": "[tool:pytest]\nfilterwarnings =\n"
        "    ignore::DeprecationWarning\n    ignore::pkg.side.Warn\n"
    },
    "log_file": {"pytest.ini": "[pytest]\nlog_file = src/pkg/core.py\n"},
    "python_files_everything": {"pytest.ini": "[pytest]\npython_files = *.py\n"},
    "python_files_beside_the_defaults": {
        "pytest.ini": "[pytest]\npython_files = test_*.py side.py\n"
    },
    "own_plugin_turned_off": {"pytest.ini": "[pytest]\naddopts = -p no:python\n"},
    "own_plugin_in_one_element": {
        "pyproject.toml": '[tool.pytest]\naddopts = ["-p no:warnings"]\n'
    },
    "own_plugin_by_module": {"tox.ini": "[pytest]\naddopts = -p no:_pytest.python\n"},
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_an_option_that_runs_or_changes_project_code_refuses(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "builtin_and_stdlib_categories": {
        "pytest.ini": "[pytest]\nfilterwarnings =\n    ignore:m:UserWarning\n"
        "    ignore::DeprecationWarning\n    ignore::warnings.WarningMessage\n"
    },
    "another_plugin_turned_off": {
        "pytest.ini": "[pytest]\naddopts = -p no:cacheprovider -p no:x\n"
    },
    "default_python_files": {"pytest.ini": "[pytest]\npython_files = test_*.py *_test.py\n"},
}


@pytest.mark.parametrize("extra", KEPT.values(), ids=KEPT.keys())
def test_an_option_known_to_leave_the_tests_alone_keeps_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)
