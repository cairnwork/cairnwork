"""#370: members that import a module by a name given at run time. A literal name joins the
closure like an import statement and the existing checks judge it; a name the reader cannot
read, a name hidden in bytes, an in-process runner, or a member reached other than by a direct
call refuses. The other test runs first and can change what the drafted test sees."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\nTABLE = {}\n\n\ndef f(x):\n    return x * SCALE\n"
SIDE = "import pkg.core\n\npkg.core.SCALE = 10\nX = 1\n"  # importing it changes f
DRAFTED = "def test_a():\n    assert f(2) == 2\n"
MOCK = "from unittest import mock\n"
CONFIG = "logging.config.dictConfig({'version': 1, 'handlers': {'h': {'class': %r%s}}})"

REFUSED = {
    "patch_variable": (MOCK, "n = 'pkg.side.X'\nwith mock.patch(n, 2):\n    pass"),
    "patch_alias_variable": ("from unittest.mock import patch as p\n", "n = 'pkg.side.X'\np(n, 2)"),
    "patch_dict_project_value": (MOCK + "from pkg import core\n", "mock.patch.dict(core.TABLE)"),
    "patch_multiple_variable": (MOCK, "n = 'pkg.side'\nmock.patch.multiple(n, X=2)"),
    "patch_kwargs": (MOCK, "kw = {'target': 'pkg.side.X'}\nmock.patch(**kw)"),
    "member_as_value": (MOCK, "p = mock.patch\np('pkg.side.X', 2)"),
    "member_by_getattr": (MOCK, "getattr(mock, 'patch')('pkg.side.X', 2)"),
    "importorskip_variable": ("import pytest\n", "n = 'pkg.side'\npytest.importorskip(n)"),
    "pickle_loads": ("import pickle as pk\n", "pk.loads(b'cpkg.side\\nX\\n.')"),
    "pickle_from_import": ("from pickle import loads\n", "loads(b'cpkg.side\\nX\\n.')"),
    "pytest_main": ("import pytest\n", "pytest.main(['-q', '--co'])"),
    "dict_config_variable": (
        "import logging.config\n",
        "c = {'version': 1}\nlogging.config.dictConfig(c)",
    ),
    "locate_variable": ("import pydoc\n", "n = 'pkg.side'\npydoc.locate(n)"),
    "loader_variable": (
        "import unittest\n",
        "n = 'pkg.side'\nunittest.TestLoader().loadTestsFromName(n)",
    ),
    "loader_names_variable": (
        "import unittest\n",
        "n = ['pkg.side']\nunittest.TestLoader().loadTestsFromNames(n)",
    ),
    "monkeypatch_variable": ("", "n = 'pkg.side.X'\nmonkeypatch.setattr(n, 2)"),
    "star_import": ("from unittest.mock import *\n", "n = 'pkg.side.X'\npatch(n, 2)"),
    # a literal name is an import: of a project module the blackbox would not import, or of a
    # module outside the project and stdlib
    "patch_literal_unimported": (MOCK, "mock.patch('pkg.side.X', 2).start()"),
    "patch_literal_outside": (MOCK, "mock.patch('elsewhere.mod.X', 2).start()"),
    "monkeypatch_literal_unimported": ("", "monkeypatch.setattr('pkg.side.X', 2)"),
    "dict_config_literal_unimported": ("import logging.config\n", CONFIG % ("pkg.side.X", "")),
}
KEPT = {
    "plain": ("", "pass"),
    "patch_literal_drafted": (MOCK, "with mock.patch('pkg.core.SCALE', 2):\n    pass"),
    "patch_dict_stdlib": (
        "import os\n" + MOCK,
        "with mock.patch.dict(os.environ, {'A': '1'}):\n    pass",
    ),
    "monkeypatch_object": ("import os\n", "monkeypatch.setattr(os, 'sep', '/')"),
    "importorskip_literal": ("import pytest\n", "pytest.importorskip('json')"),
    "dict_config_literal": (
        "import logging.config\n",
        CONFIG % ("logging.StreamHandler", ", 'stream': 'ext://sys.stdout'"),
    ),
}


def _drafted(tmp_path: Path, head: str, body: str, core: str = CORE, side: str = SIDE):
    body = "".join(f"    {line}\n" for line in body.splitlines())
    files = {
        "pytest.ini": "[pytest]\npythonpath = src\n",
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": core,
        "src/pkg/side.py": side,
        "tests/test_x.py": f"{head}from pkg.core import f\n\n\ndef test_b(monkeypatch):\n"
        f"{body}\n\n{DRAFTED}",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    test = tmp_path / "tests/test_x.py"
    items = analyze_file(test, tmp_path, default_roots(tmp_path))
    return next(item for item in items if item.test_id.endswith("::test_a"))


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_name_the_reader_cannot_follow_refuses(tmp_path: Path, case: str):
    item = _drafted(tmp_path, *REFUSED[case])
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("case", sorted(KEPT))
def test_a_literal_name_the_draft_imports_anyway_keeps_it(tmp_path: Path, case: str):
    item = _drafted(tmp_path, *KEPT[case])
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


@pytest.mark.parametrize(
    "use",
    [
        "import pickle\n\n\ndef g(b):\n    return pickle.loads(b)\n",
        "import pytest\n\n\ndef g():\n    return pytest.main([])\n",
        "from unittest import mock\n\n\ndef g(n):\n    return mock.patch(n)\n",
    ],
)
def test_a_project_module_that_loads_by_unread_name_refuses(tmp_path: Path, use: str):
    item = _drafted(tmp_path, "", "pass", core=f"{use}\n\n{CORE}")
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_a_literal_name_in_a_project_module_joins_the_closure(tmp_path: Path):
    """pkg.core naming pkg.side by a literal puts pkg.side's own checks in play."""
    use = "from unittest import mock\n\n\ndef g():\n    return mock.patch('pkg.side.X')\n"
    hook = "def __getattr__(name):\n    return 1\n"
    item = _drafted(tmp_path, "", "pass", core=f"{use}\n\n{CORE}", side=hook)
    assert item.refusal is not None and "pkg.side" in item.refusal
