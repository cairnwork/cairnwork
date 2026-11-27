"""#1878: the whole family of class shapes the static reader cannot see (module-attribute or
call/subscript/starred bases, factory-built classes, ``type()`` in a function) needs a whole-file
target; readable shapes and whole-file verifiers are unchanged."""

import pytest
from conftest import commit_change, git
from test_deferred_opaque_changed_1878 import HEAD, HELPERS, TEST, VISIBLE, _argv
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

import cairnwork.deferred_tests as dt
from cairnwork.deferred_tests import ensure_test_commit

sandbox = pytest.mark.sandbox
T = "    def test_h(self):\n        assert add(1, 1) == {n}\n"
CHECK = "        assert add(1, 1) == {n}\n"
MUST_BLOCK = {
    "module_attr": "import helpers\n\n\nclass Hid(helpers.Base):\n" + T,
    "module_alias_attr": "import helpers as h\n\n\nclass Hid(h.Base):\n" + T,
    "factory_class": "def make():\n    class Hid(unittest.TestCase):\n        def test_h(self):\n"
    "            assert add(1, 1) == {n}\n\n    return Hid\n\n\nHid = make()\n",
    "factory_type": "def _t(self):\n    assert add(1, 1) == {n}\n\n\ndef make():\n"
    "    return type('Hid', (unittest.TestCase,), {{'test_h': _t}})\n\n\nHid = make()\n",
    "type_in_if": "def _t(self):\n    assert add(1, 1) == {n}\n\n\nif True:\n"
    "    Hid = type('Hid', (unittest.TestCase,), {{'test_h': _t}})\n",
    "call_base": "def mk():\n    return unittest.TestCase\n\n\nclass Hid(mk()):\n" + T,
    "call_base_direct": "class Hid(unittest.TestCase()):\n" + T,
    "subscript_generic": "from typing import Generic, TypeVar\n\nT = TypeVar('T')\n\n\n"
    "class Hid(Generic[T]):\n" + T,
    "subscript_base": "from helpers import Base\n\n\nclass Hid(Base[int]):\n" + T,
    "subscript_index": "BASES = (unittest.TestCase,)\n\n\nclass Hid(BASES[0]):\n" + T,
    "starred": "bs = (unittest.TestCase,)\n\n\nclass Hid(*bs):\n" + T,
    "metaclass": "class Hid(unittest.TestCase, metaclass=type):\n" + T,
}
READABLE = {
    "no_base": "class TestX:\n" + T,
    "object": "class TestX(object):\n" + T,
    "unittest": "class U(unittest.TestCase):\n" + T,
    "testcase_name": "from helpers import MyTestCase\n\n\nclass U(MyTestCase):\n" + T,
    "earlier_class": "class A(unittest.TestCase):\n    pass\n\n\nclass B(A):\n" + T,
}


def _text(shape, n_visible, n_shape):
    return HEAD + VISIBLE.format(n=n_visible) + "\n\n" + shape.format(n=n_shape)


def _setup(project, shape, n_new):
    files = {TEST: _text(shape, "1 + 1", "1 + 1"), "tests/helpers.py": HELPERS}
    commit_change(project, files)
    return make_test_commit(project, {TEST: _text(shape, 2, n_new)})


@pytest.mark.parametrize("name", MUST_BLOCK)
def test_family_shape_needs_whole(project, name):
    base, commit = _setup(project, MUST_BLOCK[name], 99)
    ensure_test_commit(project, commit, origin_url=git(project, "remote", "get-url", "origin"))
    assert dt.whole_file_required(project, base, commit, (TEST,)) == {TEST}


@pytest.mark.parametrize("name", READABLE)
def test_readable_shape_needs_no_whole(project, name):
    base, commit = _setup(project, READABLE[name], 99)
    ensure_test_commit(project, commit, origin_url=git(project, "remote", "get-url", "origin"))
    assert dt.whole_file_required(project, base, commit, (TEST,)) == frozenset()


PIPELINE = ("module_attr", "factory_class", "call_base")


@sandbox
@pytest.mark.parametrize("name", PIPELINE)
def test_node_target_cannot_hide_a_failing_changed_test(project, tmp_path, name):
    base, commit = _setup(project, MUST_BLOCK[name], 99)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX),
                 verifiers=_argv(f"{TEST}::test_regression"))  # fmt: skip
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason


@sandbox
@pytest.mark.parametrize("name", PIPELINE)
def test_whole_file_verifier_is_unchanged(project, tmp_path, name):
    base, commit = _setup(project, MUST_BLOCK[name], 99)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status != "COMPLETED"
    assert not result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason
