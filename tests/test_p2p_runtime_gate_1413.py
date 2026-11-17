"""#1413 PR-2a: runtime collect evidence gates direct-deletion classification."""

import pytest
from conftest import commit_change
from test_collect_exposed_1413 import TARGET, collect
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, _run

from cairnwork.p2p_classify_child import classify_payload
from cairnwork.p2p_collect import classify

pytestmark = pytest.mark.sandbox

KEEP = "    def test_keep(self):\n        assert True\n"
BASE_CLASS = "class TestA:\n    def test_x(self):\n        assert True\n" + KEEP
BASE_TOP = "def test_x():\n    assert True\ndef test_keep():\n    assert True\n"

ASSEMBLERS = {
    "concat": "setattr(Helper, 'test_' + 'x', f)",
    "fstring": "n = 'x'\nsetattr(Helper, f'test_{n}', f)",
    "type_concat": "Helper = type('Helper', (), {'test_' + 'x': f})",
    "exec_concat": "exec('class Helper:\\n    test_' + 'x = f')",
    "loop": "for n in ['x']:\n    setattr(Helper, 'test_' + n, f)",
    "setattr": "setattr(Helper, 'test_x', f)",
    "alias": "s = setattr\ns(Helper, 'test_x', f)",
    "builtins_getattr": "import builtins\ngetattr(builtins, 'set' + 'attr')(Helper, 'test_x', f)",
    "partial": "import functools\nfunctools.partial(setattr, Helper)('test_x', f)",
    "sys_modules": "import sys\nsys.modules['builtins'].setattr(Helper, 'test_x', f)",
    "import_module": "import importlib\n"
    "importlib.import_module('builtins').setattr(Helper, 'test_x', f)",
    "map": "list(map(setattr, [Helper], ['test_x'], [f]))",
    "exec": "exec(\"class Helper: pass\\nsetattr(Helper, 'test_x', f)\")",
    "type": "Helper = type('Helper', (), {'test_x': f})",
    "type_new": "Helper = type.__new__(type, 'Helper', (), {'test_x': f})",
    "new_class": "import types\ndef body(ns): ns['test_x'] = f\n"
    "Helper = types.new_class('Helper', (), exec_body=body)",
    "metaclass": "class Meta(type):\n    def __new__(m,n,b,d):\n"
    "        d['test_x'] = f\n        return super().__new__(m,n,b,d)\n"
    "class Helper(metaclass=Meta): pass",
    "decorator": "def deco(c): c.test_x = f; return c\n@deco\nclass Helper: pass",
    "test_false": "class TestHelper:\n    __test__ = False\n    test_x = f",
    "held_only": "def make():\n    class Helper: test_x = f\n    return Helper\nheld = [make()]",
}

# shapes the AST layer alone (main) reads as removed; the runtime fact must catch every shape
MISSED_BY_AST = {"concat", "exec_concat", "fstring", "loop", "metaclass", "type_concat"}


def _reattach(base, shape, top=False):
    prefix = (
        "def _x():\n    assert True\nf = _x\n"
        if top
        else ("def _x(self):\n    assert True\nf = _x\n")
    )
    keep = "def test_keep():\n    assert True\n" if top else "class TestA:\n" + KEEP
    return prefix + keep + "class Helper: pass\n" + shape + "\n"


def _e2e(repo, tmp_path, base, test):
    """A real run: the test commit edits ``tests/test_a.py`` and adds ``test_mul`` (fails on base),
    so the run reaches the P2P collect-only classification of the deleted base test."""
    commit_change(
        repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_a.py": base}
    )
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_a.py")),)
    test_files = {"tests/test_a.py": "import calc\n" + test + MUL_TEST}
    return _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": CALC + MUL}, test_files, check)


REFUSED = ("TEST_COMMIT_DROPS_BASE_TESTS", "DEFERRED_TESTS_UNREADABLE")


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", sorted(ASSEMBLERS))
def test_runtime_reattachment_is_rejected(repo, tmp_path, shape, top):
    base = BASE_TOP if top else BASE_CLASS
    result = _e2e(repo, tmp_path, base, _reattach(base, ASSEMBLERS[shape], top))
    assert not result.verified
    assert result.reason.startswith(REFUSED), result.reason


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
def test_honest_deletion_remains_removed(repo, tmp_path, top):
    test = "def test_keep():\n    assert True\n" if top else "class TestA:\n" + KEEP
    result = _e2e(repo, tmp_path, BASE_TOP if top else BASE_CLASS, test)
    assert result.verified, result.reason


HONEST = {
    "re_compile": "import re\nPATTERN = re.compile('x')\nclass TestA:\n" + KEEP,
    "dict_read": "class Box: pass\nNAMES = list(Box.__dict__)\nclass TestA:\n" + KEEP,
    "monkeypatch": "class Box: pass\nclass TestA:\n    def test_keep(self, monkeypatch):\n"
    "        monkeypatch.setattr(Box, 'value', 1, raising=False)\n",
    "other_name": "class TestA:\n" + KEEP + "class Helper:\n    def test_y(self):\n        pass\n",
    "move_to_test_class": "class TestA:\n"
    + KEEP
    + "class TestB:\n    def test_x(self):\n        assert True\n",
}


@pytest.mark.parametrize("shape", sorted(HONEST))
def test_runtime_evidence_does_not_misclassify_collected_tests(repo, tmp_path, shape):
    result = _e2e(repo, tmp_path, BASE_CLASS, HONEST[shape])
    assert result.verified, result.reason


def test_same_name_in_different_file_does_not_block_but_same_file_does():
    base = "class TestA:\n    def test_x(self): pass\n"
    source = "class TestA: pass\n"
    node = "a.py::TestA::test_x"
    side = classify(
        {node}, set(), {"a.py"}, lambda _: source, lambda _: base, exposed=("b.py::Helper::test_x",)
    )
    assert side.removed == {node}
    side = classify(
        {node}, set(), {"a.py"}, lambda _: source, lambda _: base, exposed=("a.py::Helper::test_x",)
    )
    assert side.dropped == {node}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "base_ids": ["a.py::test_x"],
            "test_ids": [],
            "edited": ["a.py"],
            "sources": {
                "test": {
                    "a.py": "",
                },
                "base": {"a.py": "def test_x(): pass"},
            },
        },
        {
            "base_ids": ["a.py::test_x"],
            "test_ids": [],
            "edited": ["a.py"],
            "exposed": "x",
            "sources": {"test": {"a.py": ""}, "base": {"a.py": "def test_x(): pass"}},
        },
        {
            "base_ids": ["a.py::test_x"],
            "test_ids": [],
            "edited": ["a.py"],
            "exposed": [4],
            "sources": {"test": {"a.py": ""}, "base": {"a.py": "def test_x(): pass"}},
        },
    ],
)
def test_missing_or_malformed_runtime_evidence_fails_closed(payload):
    assert classify_payload(payload)["dropped"] == ["a.py::test_x"]


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", sorted(ASSEMBLERS))
def test_real_collect_only_fact_alone_turns_removed_into_dropped(tmp_path, shape, top):
    """The gate on its own, independent of the deferred-tests guard: a real collect-only record
    of the test commit; without the fact the AST layer alone says removed (main's verdict)."""
    base = BASE_TOP if top else BASE_CLASS
    test = _reattach(base, ASSEMBLERS[shape], top)
    record = collect(tmp_path, test)
    node = f"{TARGET}::test_x" if top else f"{TARGET}::TestA::test_x"
    test_ids = set(record["collected"])
    args = ({node, *test_ids}, test_ids, {TARGET}, lambda _: test, lambda _: base)
    print("AST_ALONE", shape, top, node in classify(*args, exposed=()).removed)
    assert classify(*args, exposed=tuple(record["uncollected_exposed"])).dropped == {node}
