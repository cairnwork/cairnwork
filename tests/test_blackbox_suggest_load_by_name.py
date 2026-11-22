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
MP = "import pytest\n"
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
    "dict_config_ext_unimported": (
        "import logging.config\n",
        CONFIG % ("logging.StreamHandler", ", 'stream': 'ext://pkg.side.X'"),
    ),
    # a loader the reader must not lose: under a second meaning of its name, under another name
    # or as a value, unbound, or itself loaded by name
    "alias_two_meanings": (
        MOCK + "\n\ndef helper():\n    import json as mock\n",
        "with mock.patch('pkg.side.X', 2):\n    pass",
    ),
    "owner_as_value": (MOCK + "\n\ndef go(m):\n    m.patch('pkg.side.X', 2).start()\n", "go(mock)"),
    "loader_loaded_by_name": (
        "import importlib\n",
        "importlib.import_module('unittest.mock').patch('pkg.side.X', 2).start()",
    ),
    "monkeypatch_by_other_name": ("", "m = monkeypatch\nm.setattr('pkg.side.X', 2)"),
    "monkeypatch_class": (
        "import pytest\n",
        "with pytest.MonkeyPatch.context() as m:\n    m.setattr('pkg.side.X', 2)",
    ),
    "monkeypatch_unbound": (
        "import pytest\n",
        "pytest.MonkeyPatch.setattr(monkeypatch, 'pkg.side.X', 2)",
    ),
    "patcher_stored": ("", "s = monkeypatch.setattr\ns('pkg.side.X', 2)"),
    "patcher_by_getattr": ("", "getattr(monkeypatch, 'setattr')('pkg.side.X', 2)"),
    "patch_dict_attribute": (
        MOCK + "import sys\n",
        "sys.T = 'pkg.side.X'\nwith mock.patch.dict(sys.T, {}):\n    pass",
    ),
    "patch_dict_mapping_rebound": (
        MOCK + "import os\n",
        "monkeypatch.setattr(os, 'environ', 'pkg.side.X')\n"
        "with mock.patch.dict(os.environ):\n    pass",
    ),
    "loader_from_sys_modules": (
        MOCK + "import sys\n",
        "sys.modules['unittest.mock'].patch('pkg.side.X', 2).start()",
    ),
    "builtins_namespace": ("", "__builtins__['__import__']('pkg.side')"),
    "patch_dict_mapping_assigned": (
        MOCK + "import os\n",
        "os.environ = 'pkg.side.X'\nwith mock.patch.dict(os.environ):\n    pass",
    ),
    "patch_dict_mapping_set_by_key": (
        MOCK + "import os\n",
        "vars(os)['environ'] = 'pkg.side.X'\nwith mock.patch.dict(os.environ):\n    pass",
    ),
    "loader_discover": ("import unittest\n", "unittest.TestLoader().discover('src')"),
    "mock_private": ("from unittest.mock import _importer\n", "_importer('pkg.side')"),
    # the standard importers and in-process runners
    "import_module_literal_unimported": (
        "import importlib\n",
        "importlib.import_module('pkg.side')",
    ),
    "import_module_variable": (
        "from importlib import import_module as im\n",
        "n = 'pkg.side'\nim(n)",
    ),
    "import_module_relative": ("import importlib\n", "importlib.import_module('.side', 'pkg')"),
    "dunder_import_fromlist": ("", "__import__('pkg', fromlist=['side'])"),
    "resolve_name_literal_unimported": ("import pkgutil\n", "pkgutil.resolve_name('pkg.side:X')"),
    "pytest_as_value": ("import pytest\n", "p = pytest\np.importorskip('pkg.side')"),
    "pickle_as_value": ("import pickle\n", "p = pickle\np.loads(b'cpkg.side\\nX\\n.')"),
    "unittest_as_value": ("import unittest\n", "u = unittest\nu.TestLoader().discover('src')"),
    "unittest_main": (
        "import unittest\n",
        "unittest.main(module='pkg.side', exit=False, argv=['x'])",
    ),
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
    "resolve_name_drafted": ("import pkgutil\n", "pkgutil.resolve_name('pkg.core:f')"),
    "import_module_drafted": ("import importlib\n", "importlib.import_module('pkg.core')"),
    "monkeypatch_builtins": ("import builtins\n", "monkeypatch.setattr(builtins, 'input', str)"),
    "patch_builtins_open": (MOCK, "with mock.patch('builtins.open', mock.mock_open()):\n    pass"),
    "patch_dict_sys_modules": (
        "import sys\n" + MOCK,
        "with mock.patch.dict(sys.modules):\n    pass",
    ),
    "patch_dict_named_with_os_bound": (
        MOCK,
        "os = 1\nwith mock.patch.dict('os.environ', {'A': '1'}):\n    pass",
    ),
    "patch_dict_environ_by_name": (
        "from os import environ\n" + MOCK,
        "with mock.patch.dict(environ, {'A': '1'}):\n    pass",
    ),
}

# a name the patch.dict mapping exemption reads as os or sys, bound to something else
MAPPING_BOUND = {
    "assigned": (
        MOCK + "import os\n",
        "os = type('o', (), {'environ': 'pkg.side.X'})\n"
        "with mock.patch.dict(os.environ, {}):\n    pass",
    ),
    "parameter": (
        MOCK + "\n\ndef go(os):\n    with mock.patch.dict(os.environ, {}):\n        pass\n\n\n",
        "go(type('o', (), {'environ': 'pkg.side.X'}))",
    ),
    "loop_target": (
        MOCK + "import sys\n",
        "for sys in [type('s', (), {'modules': 'pkg.side.X'})]:\n"
        "    with mock.patch.dict(sys.modules, {}):\n        pass",
    ),
    "imported_as_another": (
        MOCK + "import os\n\n\ndef go():\n    import json as os\n    return os\n\n\n",
        "with mock.patch.dict(os.environ, {}):\n    pass",
    ),
    "imported_as_another_first": (
        MOCK + "import json as os\n\n\ndef go():\n    import os\n    return os\n\n\n",
        "with mock.patch.dict(os.environ, {}):\n    pass",
    ),
    "with_target": (
        MOCK + "import contextlib\n",
        "with contextlib.nullcontext(type('o', (), {'environ': 'pkg.side.X'})) as os:\n"
        "    with mock.patch.dict(os.environ, {}):\n        pass",
    ),
}
# the fixture or its patchers reached other than by a direct call of a member
FIXTURE_LOOKUP = {
    "methodcaller": (
        "import operator\n",
        "operator.methodcaller('setattr', 'pkg.side.X', 2)(monkeypatch)",
    ),
    "getattribute": ("", "monkeypatch.__getattribute__('setattr')('pkg.side.X', 2)"),
    "getattr_variable": ("", "n = 'set' + 'attr'\ngetattr(monkeypatch, n)('pkg.side.X', 2)"),
    "getattribute_variable": (
        "",
        "n = 'set' + 'attr'\nmonkeypatch.__getattribute__(n)('pkg.side.X', 2)",
    ),
    "methodcaller_instance": (
        "import operator\nimport pytest\n",
        "mp = pytest.MonkeyPatch()\noperator.methodcaller('setattr', 'pkg.side.X', 2)(mp)",
    ),
    "stored_on_instance": (MP, "mp = pytest.MonkeyPatch()\ns = mp.setattr\ns('pkg.side.X', 2)"),
    "unbound_on_instance": (
        MP,
        "mp = pytest.MonkeyPatch()\npytest.MonkeyPatch.setattr(mp, 'pkg.side.X', 2)",
    ),
    "loader_by_getattr": (
        "import unittest\n",
        "getattr(unittest.TestLoader(), 'loadTestsFromName')('pkg.side')",
    ),
    "passed_on": ("import os\n", "m = monkeypatch\nm.setattr(os, 'sep', '/')"),
}
# logging.config's own configurator classes import the class names they are given
CONFIGURATOR = {
    "dict_configurator": (
        "import logging.config as lc\n",
        "lc.DictConfigurator({'version': 1, 'handlers': {'h': {'class': 'pkg.side.X'}}})"
        ".configure()",
    ),
    "base_configurator": (
        "from logging.config import BaseConfigurator\n",
        "BaseConfigurator({}).resolve('pkg.side.X')",
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


def test_a_relative_name_is_not_read_as_an_import(tmp_path: Path):
    item = _drafted(tmp_path, *REFUSED["import_module_relative"])
    assert item.refusal is not None and "NAME_NOT_STATIC" in item.refusal


def _refuses(tmp_path: Path, head: str, body: str) -> None:
    item = _drafted(tmp_path, head, body)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("case", sorted(MAPPING_BOUND))
def test_a_mapping_module_bound_without_an_import_refuses(tmp_path: Path, case: str):
    _refuses(tmp_path, *MAPPING_BOUND[case])


@pytest.mark.parametrize("case", sorted(FIXTURE_LOOKUP))
def test_a_fixture_reached_by_a_name_lookup_refuses(tmp_path: Path, case: str):
    _refuses(tmp_path, *FIXTURE_LOOKUP[case])


@pytest.mark.parametrize("case", sorted(CONFIGURATOR))
def test_a_configurator_class_refuses(tmp_path: Path, case: str):
    _refuses(tmp_path, *CONFIGURATOR[case])
