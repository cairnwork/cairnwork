"""#579: more test-suite shapes ``sys_path_scan`` classifies as possibly editing sys.path.

Each case commits a few files to a small repository and asks ``may_edit_sys_path`` about that
commit. True (the stricter answer) lets nothing be sealed; False lets a package stay sealed. The
controls are ordinary test-suite code that must keep its package sealed.
"""

from __future__ import annotations

import os

import pytest
from conftest import commit_change

from cairnwork.sys_path_scan import may_edit_sys_path

EDITS_PATH = "import sys\nsys.path.insert(0, 'pkg')\n"


def _scan(repo, files, links=None) -> bool:
    for path, body in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body if isinstance(body, bytes) else body.encode())
    for path, dest in (links or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(dest, repo / path)
    return may_edit_sys_path(repo, commit_change(repo))


# 1. A tracked symlink whose target may lie outside the commit --------------------------------


@pytest.mark.parametrize(
    "target",
    ["{outside}", "../outside/support", "hop/x/../../outside/support"],
    ids=["absolute", "above the root", "through another symlink"],
)
def test_a_symlink_that_may_leave_the_commit_may_edit(repo, target):
    outside = repo.parent / "outside" / "support"
    outside.mkdir(parents=True)
    (outside / "__init__.py").write_text(EDITS_PATH)
    links = {"hop": ".", "support": target.format(outside=outside)}
    files = {"x/keep.txt": "", "tests/test_a.py": "import support\n"}
    assert _scan(repo, files, links) is True


def test_a_symlink_inside_the_commit_keeps_the_package_sealed(repo):
    files = {"docs/guide.md": "# Guide\n", "lib/helpers/__init__.py": "X = 1\n"}
    links = {"guide.md": "docs/guide.md", "tests/helpers": "../lib/helpers"}
    assert _scan(repo, files, links) is False


def test_a_symlink_inside_the_commit_is_read_through_its_target(repo):
    files = {"lib/helpers/__init__.py": EDITS_PATH, "tests/test_a.py": "import helpers\n"}
    assert _scan(repo, files, {"tests/helpers": "../lib/helpers"}) is True


def test_a_symlink_whose_target_is_not_utf8_may_edit(repo):
    (repo / "lib").mkdir()
    os.symlink(b"lib/caf\xe9", bytes(repo / "notes"))
    assert _scan(repo, {"lib/x.py": "X = 1\n"}) is True


# 2. A source encoding the byte search cannot read --------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        b"# -*- coding: utf-7 -*-\nimport o+AHM-\no+AHM-.system('python x.py')\n",
        b"# coding: cp1252\nX = 1\n",
        b"# coding: no-such-codec\nX = 1\n",
    ],
    ids=["utf-7", "cp1252", "an unknown codec"],
)
def test_a_file_declared_in_another_encoding_may_edit(repo, source):
    assert _scan(repo, {"lib/x.py": source}) is True


@pytest.mark.parametrize(
    "source",
    [b"# -*- coding: %s -*-\nX = 1\n" % name for name in (b"utf-8", b"utf8", b"latin-1", b"ascii")]
    + [b"\xef\xbb\xbfX = 1\n"],
    ids=["utf-8", "utf8", "latin-1", "ascii", "a UTF-8 byte order mark"],
)
def test_a_file_in_utf8_latin1_or_ascii_keeps_the_package_sealed(repo, source):
    assert _scan(repo, {"lib/x.py": source}) is False


# 3. sys.modules patched by a string target ---------------------------------------------------


def test_pytest_mock_patching_sys_modules_by_name_may_edit(repo):
    src = "def test_a(mocker):\n    mocker.patch.dict('sys.modules', {'pkg': object()})\n"
    assert _scan(repo, {"tests/test_a.py": src}) is True


# 5. sys taken from os under another name; a relative module named os -------------------------


def test_sys_renamed_on_import_from_os_may_edit(repo):
    src = "from os import sys as runtime\nruntime.path.append('pkg')\n"
    assert _scan(repo, {"lib/x.py": src}) is True


def test_sys_renamed_on_import_from_os_for_a_safe_attribute_keeps_the_package_sealed(repo):
    src = "from os import sys as runtime\nARGS = runtime.argv\n"
    assert _scan(repo, {"lib/x.py": src}) is False


def test_sys_from_a_relative_module_named_os_may_edit(repo):
    files = {"lib/__init__.py": "", "lib/os.py": "import sys\n"}
    src = "from .os import sys\nARGS = sys.argv\n"
    assert _scan(repo, files | {"lib/x.py": src}) is True


# 6. unittest entry points under other spellings; other program launchers ---------------------

UNITTEST_SPELLINGS = {
    "a subclass of TestProgram": (
        "import unittest as ut\n\n\nclass Runner(ut.TestProgram):\n    pass\n\n\n"
        "Runner(module=None, exit=False)\n"
    ),
    "main bound to another name": (
        "from unittest import main as run_all\n\nlaunch = run_all\nlaunch(module=None)\n"
    ),
    "main handed to a function": (
        "import atexit\nimport unittest\n\natexit.register(unittest.main)\n"
    ),
    "unittest bound by a submodule import": "import unittest.util\n\nunittest.main(module=None)\n",
    "the unittest.main module renamed": (
        "import unittest.main as entry\n\nentry.main(module=None)\n"
    ),
    "a star import": "from unittest import *\n\nmain(module=None, exit=False)\n",
}
LAUNCHERS = {
    "invoke": "import invoke\n\ninvoke.run('python -m pkg')\n",
    "an async run_process": (
        "async def f(lib):\n    await lib.run_process(['python', '-m', 'pkg'])\n"
    ),
    "an async open_process": (
        "async def f(lib):\n    await lib.open_process(['python', '-m', 'pkg'])\n"
    ),
    "nox": (
        "import nox\n\n\n@nox.session\ndef tests(session):\n"
        "    session.run('python', '-m', 'pkg')\n"
    ),
}


@pytest.mark.parametrize("src", UNITTEST_SPELLINGS.values(), ids=UNITTEST_SPELLINGS.keys())
def test_unittest_entry_points_under_other_spellings_may_edit(repo, src):
    assert _scan(repo, {"tests/run.py": src}) is True


@pytest.mark.parametrize("src", LAUNCHERS.values(), ids=LAUNCHERS.keys())
def test_other_program_launchers_may_edit(repo, src):
    assert _scan(repo, {"tasks.py": src}) is True


KEEPS_SEALED = {
    "unittest.main given a module name": (
        "import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n\n\n"
        "if __name__ == '__main__':\n    unittest.main(module='tests.test_a')\n"
    ),
    "a TestCase subclass run by main()": (
        "from unittest import TestCase, main\n\n\nclass T(TestCase):\n"
        "    def test_t(self):\n        self.assertTrue(True)\n\n\nmain()\n"
    ),
    "click's CliRunner.invoke": (
        "from click.testing import CliRunner\n\n\ndef test_cli(cli):\n"
        "    assert CliRunner().invoke(cli, ['--help']).exit_code == 0\n"
    ),
}


@pytest.mark.parametrize("src", KEEPS_SEALED.values(), ids=KEEPS_SEALED.keys())
def test_ordinary_unittest_and_cli_tests_keep_the_package_sealed(repo, src):
    assert _scan(repo, {"tests/test_a.py": src}) is False


# 7. pytest run in process with options that may add to sys.path ------------------------------

IN_PROCESS = {
    "-o pythonpath": "import pytest\n\npytest.main(['-o', 'pythonpath=pkg', 'inner'])\n",
    "-c another ini": "import pytest\n\npytest.main(['-c', 'inner.ini', 'inner'])\n",
    "main imported by from": "from pytest import main\n\nmain(['-c', 'inner.ini'])\n",
    "pytest renamed": "import pytest as pt\n\npt.main(['-c', 'inner.ini'])\n",
    "pytest.main() with no arguments": (
        "import pytest\n\nif __name__ == '__main__':\n    pytest.main()\n"
    ),
    "pytest's config module by from": "from _pytest import config\n\nconfig.main(['inner'])\n",
    "pytest's config module by import": "import _pytest.config\n\n_pytest.config.main(['inner'])\n",
    "pytester with -o pythonpath": (
        "def test_inner(pytester):\n    pytester.inline_run('-o', 'pythonpath=pkg')\n"
    ),
}


@pytest.mark.parametrize("src", IN_PROCESS.values(), ids=IN_PROCESS.keys())
def test_pytest_run_in_process_may_edit(repo, src):
    files = {"inner.ini": "[pytest]\npythonpath = pkg\n", "tests/test_inner.py": src}
    assert _scan(repo, files) is True


# 8. The two unittest forms the surviving #526 mutants left untested ---------------------------


def test_unittest_main_given_its_module_through_keyword_unpacking_may_edit(repo):
    src = "import unittest\n\nunittest.main(**{'module': None})\n"
    assert _scan(repo, {"tests/run.py": src}) is True


def test_main_imported_from_the_unittest_main_module_may_edit(repo):
    src = "from unittest.main import main\n\nmain(module=None)\n"
    assert _scan(repo, {"tests/run.py": src}) is True
