"""#382 scope: the base code that can put a sealed package in front of sys.path may live anywhere
in the checkout and may name ``sys`` (or ``os``) under another name. ``sys_path_scan`` reads every
Python file of the base, as text and as a syntax tree, and seals nothing when it cannot tell."""

import os
import sys

import pytest
from conftest import commit_change, git
from test_sealed_packages import FAKE, SEALED_PKG, SHADOW_TEST, _task

from cairnwork import sys_path_scan
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

REFUSED = "PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"
PUT_PKG_FIRST = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"
    " 'pkg'))\n"
)
ALIASED = (
    "import os\nimport sys as _s\n\n_s.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
)
PKG = {"pkg/__init__.py": "", "pkg/tests/__init__.py": ""}
LAYOUTS = {  # base files, test roots, the test the verifier runs
    "a root-level package the tests import": (
        PKG | {"pkg/tests/test_calc.py": "import testsupport\n" + SHADOW_TEST,
               "testsupport/__init__.py": PUT_PKG_FIRST},
        ("pkg/tests",), "pkg/tests/test_calc.py"),
    "a conftest in another test root": (
        PKG | {"pkg/tests/test_calc.py": "def test_ok():\n    assert True\n",
               "tests/conftest.py": PUT_PKG_FIRST, "tests/test_x.py": SHADOW_TEST},
        ("tests", "pkg/tests"), "tests/test_x.py"),
    "sys under another name": (
        PKG | {"pkg/tests/conftest.py": ALIASED, "pkg/tests/test_calc.py": SHADOW_TEST},
        ("pkg/tests",), "pkg/tests/test_calc.py"),
}  # fmt: skip


@pytest.mark.sandbox
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_check_task_refuses_a_package_the_base_puts_in_front(repo, tmp_path, layout):
    """check_task refuses the stdlib-named file; on the verifier's own pytest the same base
    really lets that file pass a wrong answer, so the refusal is what stops it."""
    files, roots, target = LAYOUTS[layout]
    head = commit_change(repo, files)
    verify = (("pytest", ("python3", "-m", "pytest", "-q", target)),)
    task = TaskSpec("key", "card", "t", "fix", repo, head, ("pkg/fractions.py",), verify,
                    test_roots=roots)  # fmt: skip
    with pytest.raises(TaskRejected, match=REFUSED):
        check_task(task)
    candidate = commit_change(repo, {"pkg/fractions.py": FAKE})
    spec = parse_verifier("v", ["python3", "-m", "pytest", target], roots)
    (run,) = run_verifiers(repo, candidate, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed, (run.why, run.log_path.read_text())


UNSEALING = {  # one base file anywhere in the checkout
    "an alias of sys": ("helpers/paths.py", "import sys as s\ns.path.append('pkg')\n"),
    "sys.path imported by name": (
        "helpers/paths.py", "from sys import path as p\np[:0] = ['pkg']\n"),
    "sys handed to a function": ("conftest.py", "import sys\nsetattr(sys, 'pa' 'th', ['pkg'])\n"),
    "sys handed to monkeypatch": (
        "tools/fix.py", "import sys\n\n\ndef f(mp):\n    mp.setattr(sys, 'path', [])\n"),
    "sys.meta_path": ("pkg/hooks.py", "import sys\nsys.meta_path.insert(0, object())\n"),
    "sys.modules": (
        "pkg/alias.py", "import sys\nsys.modules['fractions'] = sys.modules[__name__]\n"),
    "sys from another module": ("lib/x.py", "import logging\nm = logging.sys\n"),
    "importlib": ("lib/x.py", "import importlib\nimportlib.import_module('pkg.fractions')\n"),
    "__import__": ("lib/x.py", "m = __import__('pkg.fractions')\n"),
    "os.system imported by name": (
        "scripts/run.py", "from os import system\nsystem('python x.py')\n"),
    "an alias of os": ("scripts/run.py", "import os as o\no.execv('/bin/sh', ['sh'])\n"),
    "a Sphinx conf.py": (
        "docs/conf.py", "import os, sys\nsys.path.insert(0, os.path.abspath('..'))\n"),
    "a file name in capitals": ("LIB/X.PY", "import sys\nsys.path.append('pkg')\n"),
    "a name normalized to sys": ("lib/x.py", "import ｓｙｓ as s\ns.path.append('pkg')\n"),
    "a file that names sys and does not parse": ("lib/x.py", "import sys\nprint 'pkg'\n"),
    "a not-UTF-8 file that names sys": ("lib/x.py", b"import sys\nx = '\xff'\n"),
    "tracked bytecode": ("lib/x.pyc", b"\x00\x01"),
    "sys through getattr": (
        "lib/x.py", "import logging\ns = getattr(logging, 'sys')\ns.path.insert(0, 'pkg')\n"),
    "a sys name built at run time": (
        "lib/x.py", "import logging\ns = getattr(logging, 's' 'ys')\n"),
    "os.system through getattr": ("scripts/run.py", "import os\ngetattr(os, 'system')('x')\n"),
    "a module's __dict__": ("lib/x.py", "import logging\nm = logging.__dict__['s' 'ys']\n"),
    "exec": ("lib/x.py", "exec('import s' 'ys')\n"),
    "eval": ("lib/x.py", "m = eval('__imp' 'ort__')\n"),
    "a class pattern on sys": (
        "lib/x.py", "import logging\nmatch logging:\n    case object(sys=s):\n        pass\n"),
    "pickle": ("lib/x.py", "import pickle\nm = pickle.loads(b'')\n"),
    "site under another name": ("lib/x.py", "import site as s\ns.main()\n"),
    "importlib.util by from": ("lib/x.py", "from importlib import util\n"),
    "runpy.run_path": ("lib/x.py", "import runpy\nrunpy.run_path('x')\n"),
    "pkgutil.extend_path": ("lib/x.py", "import pkgutil\nx = pkgutil.extend_path([], 'pkg')\n"),
    "PYTHONPATH in os.environ": ("lib/x.py", "import os\nos.environ['PYTHONPATH'] = 'pkg'\n"),
    "an os.environ name built at run time": (
        "lib/x.py", "import os\nos.environ['PYTHON' 'PATH'] = 'pkg'\n"),
    "os.environ.update": ("lib/x.py", "import os\nos.environ.update(A='pkg')\n"),
    "monkeypatch.setenv": ("conftest.py", "def f(mp):\n    mp.setenv('A', 'pkg')\n"),
    "os from another module": ("lib/x.py", "import logging\nlogging.os.system('x')\n"),
    "os imported from another module": ("lib/x.py", "from logging import os as o\no.system('x')\n"),
    "os under a private name": ("lib/x.py", "import random\nrandom._os.system('x')\n"),
    "a module handed on": ("lib/x.py", "import logging\nm = logging\n"),
    "os handed to a function": (
        "tests_util.py",
        "import os\n\n\ndef f(mp):\n    mp.setattr(os, 'getcwd', lambda: os.path.sep)\n"),
    "builtins from another module": ("lib/x.py", "import reprlib\nf = reprlib.builtins.getattr\n"),
    "__subclasses__": ("lib/x.py", "x = ().__class__.__base__.__subclasses__()\n"),
    "pty.spawn": ("lib/x.py", "import pty\npty.spawn('x')\n"),
    "pkgutil.resolve_name": ("lib/x.py", "from pkgutil import resolve_name\n"),
    "resolve_name on another module": ("lib/x.py", "import foo\nm = foo.resolve_name('a.b')\n"),
    "a monkeypatch target by name": (
        "conftest.py", "def f(monkeypatch):\n    monkeypatch.delattr('s' 'ys.path')\n"),
    "unittest.mock": ("lib/x.py", "import unittest\nunittest.mock.patch('x')\n"),
}  # fmt: skip
KEEPS_SEALED = {
    "sys for the platform": ("conftest.py", "import sys\nWIN = sys.platform == 'win32'\n"),
    "sys under another name, for the version": (
        "lib/x.py", "import sys as _sys\nOLD = _sys.version_info < (3, 12)\n"),
    "os.path and os.environ reads": (
        "tests_util.py",
        "import os\nHOME = os.environ.get('HOME')\nCI = 'CI' in os.environ\nX = os.path.sep\n"),
    "a plain test module": (
        "pkg/tests/test_y.py",
        "import platform\nfrom collections import abc\n\nimport pytest\n\n\n"
        "@pytest.mark.skipif(platform.system() == 'Windows', reason='')\n"
        "def test_y():\n    assert isinstance({}, abc.Mapping)\n"),
    "sys.path in a text file": ("notes/readme.txt", "sys.path.insert(0, 'pkg')\n"),
    "re.compile and a method named eval": (
        "lib/x.py", "import re\n\n\nclass M:\n    def eval(self):\n        return re.compile\n"),
}  # fmt: skip


def _commit(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text if isinstance(text, bytes) else text.encode())
    return commit_change(repo, SEALED_PKG)


@pytest.mark.parametrize("case", sorted(UNSEALING))
def test_check_task_unseals_on_base_code_that_may_reach_the_import_system(repo, case):
    _commit(repo, *UNSEALING[case])
    with pytest.raises(TaskRejected, match=REFUSED):
        check_task(_task(repo, ("pkg/fractions.py",)))


@pytest.mark.parametrize("case", sorted(KEEPS_SEALED))
def test_check_task_keeps_the_package_sealed_for_other_base_code(repo, case):
    _commit(repo, *KEEPS_SEALED[case])
    assert check_task(_task(repo, ("pkg/fractions.py",)))


def test_a_symlinked_python_file_unseals(repo):
    commit_change(repo, SEALED_PKG)
    os.symlink("/dev/null", repo / "lib.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    with pytest.raises(TaskRejected, match=REFUSED):
        check_task(_task(repo, ("pkg/fractions.py",)))


@pytest.mark.parametrize(("name", "value"), [("MAX_FILES", 2), ("MAX_BYTES", 10)])
def test_too_much_python_seals_nothing(repo, monkeypatch, name, value):
    commit_change(repo, SEALED_PKG | {"lib/x.py": "x = 1\n", "lib/y.py": "y = 2\n"})
    monkeypatch.setattr(sys_path_scan, name, value)
    with pytest.raises(TaskRejected, match=REFUSED):
        check_task(_task(repo, ("pkg/fractions.py",)))


def test_the_file_limit_counts_paths_not_contents(repo, monkeypatch):
    """The base has five Python files but three contents: SEALED_PKG's three files are empty."""
    commit_change(repo, SEALED_PKG)
    monkeypatch.setattr(sys_path_scan, "MAX_FILES", 4)
    with pytest.raises(TaskRejected, match=REFUSED):
        check_task(_task(repo, ("pkg/fractions.py",)))


@pytest.mark.parametrize("module", sorted(sys_path_scan.IMPORT_MODULES))
def test_importing_a_module_that_can_change_imports_unseals(module):
    assert sys_path_scan._mentions(f"import {module} as m\n".encode())
    parent, _, leaf = module.rpartition(".")
    if parent:
        assert sys_path_scan._mentions(f"from {parent} import {leaf}\n".encode())


@pytest.mark.parametrize("name", sorted(sys_path_scan.HELD | sys_path_scan.HELD_IF_IMPORTED))
def test_a_module_held_by_another_module_unseals(name):
    assert sys_path_scan._mentions(f"import logging\nm = logging.{name}.x\n".encode())
    assert sys_path_scan._mentions(f"import logging\nm = logging._{name}.x\n".encode())
