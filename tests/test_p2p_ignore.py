"""#956 item 2: a base test file the repository's conftests leave out with collect_ignore is no
untargetable file (p2p_ignore). Each case is checked against what pytest itself collects:
a file read as ignored is never collected; the cases marked "collected" are."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from conftest import commit_change

from cairnwork.issue_task import test_target as target_rule
from cairnwork.p2p_ignore import collect_ignored, untargetable

FAR = "tests/v1.0/test_far.py"
TEST = "def test_far():\n    pass\n"
NEAR = "tests/conftest.py"
ROOT = "conftest.py"
IGNORED = {  # name: base files
    "the test dir's conftest names the directory": {NEAR: "collect_ignore = ['v1.0']\n"},
    "the root conftest names it": {ROOT: "collect_ignore = ['tests/v1.0']\n"},
    "the file itself, in a tuple": {NEAR: "collect_ignore = ('v1.0/test_far.py',)\n"},
    "a conftest inside the directory names the file": {
        "tests/v1.0/conftest.py": "collect_ignore = ['test_far.py']\n"},
    "a path to normalise": {NEAR: "collect_ignore = ['./v1.0/', '../tests/v1.0']\n"},
    "a nearer conftest that does not name it": {
        ROOT: "collect_ignore = ['tests/v1.0']\n",
        NEAR: "import pytest\n\n\n@pytest.fixture\ndef f():\n    return 1\n"},
    "testpaths above the directory": {
        ROOT: "collect_ignore = ['tests/v1.0']\n", "pytest.ini": "[pytest]\ntestpaths = tests\n"},
    "a conftest importing the standard library and pytest": {
        NEAR: "import os\nfrom pathlib import Path\n\nimport pytest\n\n"
              "collect_ignore = ['v1.0']\n"},
}  # fmt: skip
KEPT = {  # name: (base files, what pytest does: "collected", or None when it ignores the file too)
    "a nearer conftest's own list replaces it": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n", NEAR: "collect_ignore = []\n"}, "collected"),
    "a nearer conftest star-imports a list": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n", NEAR: "from helpers import *  # noqa\n",
         "tests/helpers.py": "collect_ignore = []\n"}, "collected"),
    "a nearer conftest binds it through globals()": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "globals()['collect_' + 'ignore'] = []\n"}, "collected"),
    "a nearer conftest's module __getattr__": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "def __getattr__(name):\n    return []\n"}, "collected"),
    "a nearer conftest binds it in a match": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "match []:\n    case [*collect_ignore]:\n        pass\n"}, "collected"),
    "a pytest_ignore_collect hook": (
        {NEAR: "collect_ignore = ['v1.0']\n\n\ndef pytest_ignore_collect(collection_path):\n"
               "    return False\n"}, "collected"),
    "a farther conftest changes the nearer list": (
        {ROOT: "from tests.conftest import collect_ignore as c\n\nc.clear()\n",
         "tests/__init__.py": "", NEAR: "collect_ignore = ['v1.0']\n"}, None),
    "testpaths names the directory": (
        {NEAR: "collect_ignore = ['v1.0']\n", "pytest.ini": "[pytest]\ntestpaths = tests/v1.0\n"},
        "collected"),
    "testpaths is a pattern": (
        {NEAR: "collect_ignore = ['v1.0']\n", "pytest.ini": "[pytest]\ntestpaths = tests/*\n"},
        None),
    "an absolute path": ({NEAR: "collect_ignore = ['/tests/v1.0']\n"}, "collected"),
    "out of the repository": ({NEAR: "collect_ignore = ['../../tests/v1.0']\n"}, "collected"),
    "a list built at run time": ({NEAR: "V = 'v1.0'\ncollect_ignore = [V]\n"}, None),
    "one item built at run time": ({NEAR: "V = 'x'\ncollect_ignore = ['v1.0', V]\n"}, None),
    "a list changed after it is bound": (
        {NEAR: "collect_ignore = []\ncollect_ignore.append('v1.0')\n"}, None),
    "bound twice": ({NEAR: "collect_ignore = ['v1.0']\ncollect_ignore = []\n"}, "collected"),
    "bound under a condition": (
        {NEAR: "import sys\n\nif sys.version_info > (9,):\n    collect_ignore = ['v1.0']\n"},
        "collected"),
    "an annotated binding": ({NEAR: "collect_ignore: list = ['v1.0']\n"}, None),
    "pytest_plugins": ({NEAR: "collect_ignore = ['v1.0']\npytest_plugins = []\n"}, None),
    "a conftest that does not parse": ({NEAR: "collect_ignore = ['v1.0'\n"}, None),
    # #992 r1 review: other ways to reach a conftest's globals, and two surviving mutants
    "an import alias of a builtin": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "from builtins import globals as g\ng()['collect_' + 'ignore'] = []\n"},
        "collected"),
    "an attribute of an imported builtins": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "import builtins as b\nb.vars()['collect_' + 'ignore'] = []\n"}, "collected"),
    "gc clears the list": (
        {NEAR: "import gc\ncollect_ignore = ['v1.0']\nfor o in gc.get_objects():\n"
               "    if type(o) is list and o == ['v1.0']:\n        o.clear()\n"}, "collected"),
    "the module's own __setattr__": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "import conftest as me\nme.__setattr__('collect_' + 'ignore', [])\n"}, "collected"),
    "a repository module clears it": (
        {NEAR: "collect_ignore = ['v1.0']\nimport helper_x  # noqa\n",
         "tests/helper_x.py": "import conftest\nconftest.collect_ignore.clear()\n"}, "collected"),
    "a repository module named like a standard one": (  # the conftest's directory comes first
        {NEAR: "collect_ignore = ['v1.0']\nimport colorsys  # noqa\n",
         "tests/colorsys.py": "import conftest\nconftest.collect_ignore.clear()\n"}, "collected"),
    "a relative import of a module named like a standard one": (
        {NEAR: "collect_ignore = ['v1.0']\nfrom .colorsys import x  # noqa\n",
         "tests/__init__.py": "", "tests/colorsys.py": "x = 1\n"}, None),
    "a third-party module": (  # it could change the list; pytest itself still ignores the file
        {NEAR: "import pluggy  # noqa\n\ncollect_ignore = ['v1.0']\n"}, None),
    "import_module through an alias": (
        {ROOT: "collect_ignore = ['tests/v1.0']\n",
         NEAR: "from importlib import import_module as im\n"
               "im(__name__).__setattr__('collect_' + 'ignore', [])\n"}, "collected"),
    "testpaths names the file below the directory": (
        {NEAR: "collect_ignore = ['v1.0']\n",
         "pytest.ini": "[pytest]\ntestpaths = tests/v1.0/test_far.py\n"}, "collected"),
    "a later mention changes the list": (
        {NEAR: "collect_ignore = ['v1.0']\ncollect_ignore.remove('v1.0')\n"}, "collected"),
}  # fmt: skip


def _task(repo, base, test_commit):
    return SimpleNamespace(repo=repo, base_commit=base, test_commit=test_commit,
                           test_roots=("tests",))  # fmt: skip


def _both(task):
    return (task.base_commit, task.test_commit)


def _setup(repo, files, path=FAR):
    base = commit_change(repo, {**files, path: TEST}, message="base")
    test_commit = commit_change(repo, {"tests/test_new.py": TEST}, message="tests")
    return _task(repo, base, test_commit)


def _collected(repo, path=FAR) -> bool:
    """Whether pytest itself collects the file (a checkout of the base files plus the tests)."""
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTHONPATH", None)
    if not (repo / "pytest.ini").exists():  # uncommitted: rootdir and confcutdir at the repo
        (repo / "pytest.ini").write_text("[pytest]\n")
    ini = ["-c", str(repo / "pytest.ini")]
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p",
                          "no:cacheprovider", *ini, f"--rootdir={repo}"], cwd=repo, env=env,
                         capture_output=True, text=True, timeout=120).stdout  # fmt: skip
    return any(line.startswith(f"{path}::") for line in out.splitlines())


@pytest.mark.parametrize("name", list(IGNORED))
def test_a_file_the_conftests_ignore_is_left_out(repo, name):
    task = _setup(repo, IGNORED[name])
    assert collect_ignored(task, task.test_roots, _both(task))(FAR)
    assert not _collected(repo)


@pytest.mark.parametrize("name", list(KEPT))
def test_a_file_whose_ignore_is_in_doubt_stays_untargetable(repo, name):
    files, pytest_does = KEPT[name]
    task = _setup(repo, files)
    assert not collect_ignored(task, task.test_roots, _both(task))(FAR)
    if pytest_does == "collected":
        assert _collected(repo)


def test_a_root_spelled_another_case_is_still_an_initial_path(repo):
    """#1001: a tracked Tests/ is the tests root on a case-insensitive file system. Its own
    conftests are read, and Tests itself is a root, never a directory a conftest above ignores."""
    files = {"conftest.py": "collect_ignore = ['Tests']\n", "Tests/v1.0/test_far.py": TEST}
    task = _task(repo, commit_change(repo, files, message="base"), "")
    assert not collect_ignored(task, ("tests",), (task.base_commit,))("Tests/v1.0/test_far.py")
    near = {"Tests/conftest.py": "collect_ignore = ['v1.0']\n"}
    task = _task(repo, commit_change(repo, near, message="near"), "")
    assert collect_ignored(task, ("tests",), (task.base_commit,))("Tests/v1.0/test_far.py")


def test_the_test_commit_must_ignore_it_too(repo):
    """#798: a test commit may change conftests; the base alone is not enough."""
    base = commit_change(repo, {NEAR: "collect_ignore = ['v1.0']\n", FAR: TEST}, message="base")
    test_commit = commit_change(repo, {NEAR: "collect_ignore = []\n"}, message="tests")
    task = _task(repo, base, test_commit)
    assert not collect_ignored(task, ("tests",), _both(task))(FAR)
    assert collect_ignored(task, ("tests",), (base,))(FAR)  # before the worker: the base alone
    assert not collect_ignored(task, ("tests",), ())(FAR)
    target = target_rule(("tests",))  # untargetable: early (before the worker) reads the base alone
    assert untargetable(task, [FAR], target) == [FAR]
    assert untargetable(task, [FAR], target, early=True) == []


@pytest.mark.parametrize(("cookie", "ignored"), [("latin-1", True), ("utf-8", True),
                                                 ("cp1252-misread", False)])  # fmt: skip
def test_a_conftest_is_read_with_its_own_encoding(repo, cookie, ignored):
    """The literal is decoded as Python decodes the file: a latin-1 cookie over UTF-8 bytes
    gives another name, which pytest does not skip."""
    path = "tests/vé/test_far.py"
    text = "collect_ignore = ['vé']\n"
    if cookie == "cp1252-misread":
        data = b"# -*- coding: latin-1 -*-\n" + text.encode()
    else:
        data = f"# -*- coding: {cookie} -*-\n".encode() + text.encode(cookie)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / NEAR).write_bytes(data)
    task = _setup(repo, {}, path)
    assert collect_ignored(task, task.test_roots, _both(task))(path) is ignored
    assert _collected(repo, path) is not ignored


def test_a_conftest_that_is_a_link_is_in_doubt(repo):
    """git stores a link's target text, not the file pytest imports through it."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests/x.py").write_text("collect_ignore = []\n")
    os.symlink("x.py", repo / NEAR)
    task = _setup(repo, {ROOT: "collect_ignore = ['tests/v1.0']\n"})
    assert not collect_ignored(task, task.test_roots, _both(task))(FAR)
    assert _collected(repo)
