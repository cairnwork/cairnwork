"""tools/blackbox_suggest (#370): one module name defined in two places pytest imports from.

pytest puts the test directories (and ``pythonpath``) in front of the installed packages. When a
module or package name is defined in two of those places, which file an ``import`` loads depends
on that order, not on the file the reader reads, so its top-level code may be code the reader
never saw. Such a project refuses. ``conftest`` is pytest's own and is not imported by name.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

INI = "[pytest]\npythonpath = src\n"
CORE = "def f(x):\n    return x * 2\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"
BASE = {"pytest.ini": INI, "src/pkg/__init__.py": "", "src/pkg/core.py": CORE}
BASE |= {"tests/test_x.py": TEST}
HELPED = "import helper\ndef f(x):\n    return x * 2 * helper.SCALE\n"


def run(tmp_path, extra):
    for rel, body in (BASE | extra).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "project_package_in_tests": {"tests/pkg/__init__.py": "", "tests/pkg/core.py": "f = len\n"},
    "project_module_in_tests": {
        "src/pkg/core.py": HELPED,
        "src/helper.py": "SCALE = 1\n",
        "tests/helper.py": "SCALE = 10\n",
    },
    "installed_module_in_tests": {
        "src/pkg/core.py": "import ruff\n" + CORE,
        "tests/ruff.py": "raise RuntimeError\n",
    },
    "two_collected_dirs": {
        "src/pkg/core.py": HELPED,
        "lib/helper.py": "SCALE = 1\n",
        "tests/helper.py": "SCALE = 10\n",
        "pytest.ini": "[pytest]\npythonpath = src lib\n",
    },
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_a_module_name_defined_twice_on_the_import_path_refuses(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain": {},
    "conftests": {"conftest.py": "", "tests/conftest.py": ""},
    "own_helper": {"src/pkg/core.py": HELPED, "src/helper.py": "SCALE = 1\n"},
}


@pytest.mark.parametrize("extra", KEPT.values(), ids=KEPT.keys())
def test_module_names_defined_once_keep_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


def test_the_project_on_the_environment_path_is_one_place(tmp_path, monkeypatch):
    """An editable install puts the project's own source directory on the path: same place."""
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    item = run(tmp_path, {})
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
