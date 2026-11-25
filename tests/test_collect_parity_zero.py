"""#1768 review families: a counted file that collects no item is not always a hidden file.

A helper-only module, a mixin-only module and a class with ``__init__`` define nothing pytest
collects; an ``importorskip`` or a module-level skip is reached. None of them is refused. A hook
that empties a module with real test definitions, and ``norecursedirs``, still are.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import commit_change
from test_collect_parity_files import _case

from cairnwork.collect_parity import _counted, _dirs, _may_have_tests

NOT_TESTS = {
    "helper": "def make():\n    return 1\n",
    "mixin": "class Mixin:\n    def test_x(self):\n        pass\n",
    "init": "class TestA:\n    def __init__(self):\n        pass\n    def test_x(self):\n        pass\n",  # noqa: E501
    "plain_class": "class Helper:\n    def test_x(self):\n        pass\n",
    "empty": "",
}
TESTS = {
    "function": "def test_a():\n    pass\n",
    "async": "async def test_a():\n    pass\n",
    "class": "class TestA:\n    def test_x(self):\n        pass\n",
    "nested_if": "import sys\nif sys.platform:\n    def test_a():\n        pass\n",
    "based_class": "import unittest\nclass A(unittest.TestCase):\n    def test_x(self):\n        pass\n",  # noqa: E501
    "unparsable": "def test_a(:\n",
}


def _task(repo, source):
    base = commit_change(repo, {"tests/test_x.py": source})
    return SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))


@pytest.mark.parametrize("kind", sorted(NOT_TESTS))
def test_a_module_that_defines_nothing_collectable_may_have_no_tests(repo, kind):
    assert _may_have_tests(_task(repo, NOT_TESTS[kind]), "tests/test_x.py") is False


@pytest.mark.parametrize("kind", sorted(TESTS))
def test_a_module_that_defines_tests_may_have_tests(repo, kind):
    assert _may_have_tests(_task(repo, TESTS[kind]), "tests/test_x.py") is True


def test_a_directory_pattern_does_not_count_a_file_pytest_never_walks(repo):
    base = commit_change(
        repo,
        {
            "pytest.ini": "[pytest]\npython_files = tests/*.py\n",
            "tests/a.py": "def test_a(): pass\n",
            "other/b.py": "def test_b(): pass\n",
        },
    )
    task = SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))
    assert _counted(task, ("tests", "other")) == ["tests/a.py"]


def test_selected_files_collapse_to_their_topmost_directories():
    files = ("a/b/test_x.py", "a/test_y.py", "c/test_z.py", "test_root.py")
    assert _dirs(files) == ("a", "c")


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["helper", "mixin", "init"])
def test_a_counted_file_with_nothing_to_collect_is_not_refused(repo, tmp_path, kind):
    result = _case(repo, tmp_path, "", {"tests/test_helpers.py": NOT_TESTS[kind]})
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytest.importorskip('no_such_mod_rv1776')\ndef test_a():\n    pass\n",
        "import pytest\npytest.skip('no', allow_module_level=True)\ndef test_a():\n    pass\n",
    ],
)
def test_a_skipped_module_is_not_a_hidden_file(repo, tmp_path, source):
    result = _case(repo, tmp_path, "", {"tests/test_optional.py": source})
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
def test_a_hook_that_empties_a_module_with_tests_is_refused(repo, tmp_path):
    hook = (
        "import pytest\n"
        "class _Empty(pytest.Module):\n"
        "    def collect(self):\n"
        "        return []\n"
        "@pytest.hookimpl(wrapper=True)\n"
        "def pytest_collect_file(file_path, parent):\n"
        "    result = yield\n"
        "    if file_path.name == 'test_hidden.py':\n"
        "        return [_Empty.from_parent(parent, path=file_path)]\n"
        "    return result\n"
    )
    result = _case(repo, tmp_path, hook, {"tests/test_hidden.py": TESTS["function"]})
    assert result.reason.startswith("P2P_BASE_FILES_UNCOLLECTED: "), result.reason
