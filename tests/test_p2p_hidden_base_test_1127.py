"""#1127: a base test the test commit keeps where pytest no longer collects it is dropped.

Each case passes the base / test-commit ids that ``pytest --collect-only`` really gives for the two
sources (checked on CPython 3.14.7, pytest from uv.lock), then asks ``classify`` for the split.
Found by the 2026-11-10 p2p re-verify of #1072 #1075 #1112 #1114; the false refusals of #1128 stay
xfail(strict=True) here so that a change to them is seen.
"""

from __future__ import annotations

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, SUB_OFF, _run

from cairnwork.p2p_collect import classify

KEEP = "    def test_keep(self):\n        assert True\n"
BASE_A = "class TestA:\n    def test_x(self):\n        assert True\n" + KEEP


def _split(base: str, test: str, base_ids, test_ids):
    side = classify(set(base_ids), set(test_ids), {"t.py"}, lambda p: test, lambda p: base)
    return side.removed, side.dropped


# --- #1127 fail-open on 1608c2b2: the base test's code stays in the file, pytest no longer collects
# it, and the whole test commit was accepted with the id "removed". All are dropped now.
HIDDEN = {
    # pure addition: a later class TestA shadows the untouched original (introduced by #1072)
    "owner-shadowed-by-later-class": (
        BASE_A,
        BASE_A + "class TestA:\n" + KEEP,
        ["t.py::TestA::test_x", "t.py::TestA::test_keep"],
        ["t.py::TestA::test_keep"],
        "t.py::TestA::test_x",
    ),
    "nested-owner-shadowed-by-later-class": (
        "class TestA:\n    class TestI:\n        def test_x(self):\n            assert True\n"
        "        def test_keep(self):\n            assert True\n",
        "class TestA:\n    class TestI:\n        def test_x(self):\n            assert True\n"
        "        def test_keep(self):\n            assert True\n"
        "    class TestI:\n        def test_keep(self):\n            assert True\n",
        ["t.py::TestA::TestI::test_x", "t.py::TestA::TestI::test_keep"],
        ["t.py::TestA::TestI::test_keep"],
        "t.py::TestA::TestI::test_x",
    ),
    # owner rebound by assignment to a holder hidden with __test__ = False (introduced by #1072)
    "owner-rebound-to-hidden-holder": (
        "class TestA:\n    def test_x(self):\n        assert True\nclass TestB:\n" + KEEP,
        "class TestA:\n    pass\nclass _Holder:\n    __test__ = False\n"
        "    def test_x(self):\n        assert True\nTestA = _Holder\nclass TestB:\n" + KEEP,
        ["t.py::TestA::test_x", "t.py::TestB::test_keep"],
        ["t.py::TestB::test_keep"],
        "t.py::TestA::test_x",
    ),
    # moved into a top-level class pytest does not collect (introduced by #1072)
    "moved-into-uncollected-top-level-class": (
        BASE_A,
        "class Hidden:\n    def test_x(self):\n        assert True\nclass TestA:\n" + KEEP,
        ["t.py::TestA::test_x", "t.py::TestA::test_keep"],
        ["t.py::TestA::test_keep"],
        "t.py::TestA::test_x",
    ),
    "module-test-moved-into-uncollected-class": (
        "def test_x():\n    assert True\ndef test_keep():\n    assert True\n",
        "class _Hold:\n    def test_x(self):\n        assert True\n"
        "def test_keep():\n    assert True\n",
        ["t.py::test_x", "t.py::test_keep"],
        ["t.py::test_keep"],
        "t.py::test_x",
    ),
    # moved into a new nested class pytest does not collect (introduced by #1114)
    "moved-into-new-uncollected-nested-class": (
        BASE_A,
        "class TestA:\n    class Hidden:\n        def test_x(self):\n            assert True\n"
        + KEEP,
        ["t.py::TestA::test_x", "t.py::TestA::test_keep"],
        ["t.py::TestA::test_keep"],
        "t.py::TestA::test_x",
    ),
    # whole-file regex fallback misses an NFKC spelling of the name (pre-existing, old regex too)
    "nfkc-name-under-if-false": (
        "class TestA:\n    def test_x(self):\n        assert True\nclass TestB:\n" + KEEP,
        "if False:\n    class TestA:\n        def test_ｘ(self):\n            assert True\n"
        "class TestB:\n" + KEEP,
        ["t.py::TestA::test_x", "t.py::TestB::test_keep"],
        ["t.py::TestB::test_keep"],
        "t.py::TestA::test_x",
    ),
}


@pytest.mark.parametrize("case", sorted(HIDDEN))
def test_hidden_base_test_is_dropped(case):
    base, test, base_ids, test_ids, nodeid = HIDDEN[case]
    assert _split(base, test, base_ids, test_ids) == (set(), {nodeid})


# --- #1128 false refusals (P2, not this fix): an honest deletion is refused as
# TEST_COMMIT_DROPS_BASE_TESTS. Kept strict xfail so #1127 is seen not to change them.
REFUSED = {
    # deleting a parent test also removes it from a same-file subclass (pre-existing)
    "inherited-by-same-file-subclass": (
        "class TestA:\n    def test_x(self):\n        assert True\n"
        + KEEP
        + "class TestB(TestA):\n    pass\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\n",
        [
            "t.py::TestA::test_x",
            "t.py::TestA::test_keep",
            "t.py::TestB::test_x",
            "t.py::TestB::test_keep",
        ],
        ["t.py::TestA::test_keep", "t.py::TestB::test_keep"],
        {"t.py::TestA::test_x", "t.py::TestB::test_x"},
    ),
    # #842 item 2: one parametrized case deleted, the function kept (not addressed by #1072)
    "parametrized-case-deleted": (
        "import pytest\n@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n):\n    assert n\n",
        "import pytest\n@pytest.mark.parametrize('n', [1])\ndef test_x(n):\n    assert n\n",
        ["t.py::test_x[1]", "t.py::test_x[2]"],
        ["t.py::test_x[1]"],
        {"t.py::test_x[2]"},
    ),
    # annotation-only `test_x: T` does not rebind, but counts as a binding (introduced by #1075)
    "annotation-only-is-not-a-rebinding": (
        "def test_x():\n    assert True\ntest_x: object\ndef test_keep():\n    assert True\n",
        "def test_keep():\n    assert True\n",
        ["t.py::test_x", "t.py::test_keep"],
        ["t.py::test_keep"],
        {"t.py::test_x"},
    ),
}


@pytest.mark.xfail(strict=True, reason="#1128: honest deletion refused")
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_honest_deletion_is_removed_1128(case):
    base, test, base_ids, test_ids, gone = REFUSED[case]
    assert _split(base, test, base_ids, test_ids) == (gone, set())


# --- #847 residual (#1128 item 4): a literal-name rebinding the binding walk does not follow; the
# base collects _other as test_x, whose code the test commit keeps. Dropped now.
OTHER = "def _other():\n    assert True\n"
LITERAL_REBIND = {
    "globals-subscript": "globals()['test_x'] = _other\n",
    "globals-update-keyword": "globals().update(test_x=_other)\n",
    "setattr-own-module": "import sys\nsetattr(sys.modules[__name__], 'test_x', _other)\n",
}


@pytest.mark.parametrize("case", sorted(LITERAL_REBIND))
def test_literal_rebinding_is_dropped(case):
    base = OTHER + "def test_x():\n    assert False\n" + LITERAL_REBIND[case]
    base += "def test_keep():\n    assert True\n"
    test = OTHER + "def test_keep():\n    assert True\n"
    assert _split(base, test, ["t.py::test_x", "t.py::test_keep"], ["t.py::test_keep"]) == (
        set(),
        {"t.py::test_x"},
    )


# Sonnet counterexample round on #1130: a copy shadowed inside a collected chain, a def inside
# a method, a constant that is never a test, and a deep AST
X = "    def test_x(self):\n        assert True\n"
TEST_A = "class TestA:\n" + KEEP
MORE_HIDDEN = {
    "copy-in-shadowed-class-next-to-live-one": (
        TEST_A + "class TestB:\n" + X + "class TestB:\n    def test_x(self):\n        assert 1\n",
        ["t.py::TestA::test_keep", "t.py::TestB::test_x"],
    ),
    "copy-under-if-false-before-live-method": (
        TEST_A + "class TestB:\n    if False:\n        def test_x(self):\n            assert True\n"
        "    def test_x(self):\n        assert 1\n",
        ["t.py::TestA::test_keep", "t.py::TestB::test_x"],
    ),
    "def-inside-a-method-of-an-uncollected-class": (
        "class Hid:\n    def m(self):\n        def test_x(self):\n            assert True\n"
        + TEST_A,
        ["t.py::TestA::test_keep"],
    ),
}


@pytest.mark.parametrize("case", sorted(MORE_HIDDEN))
def test_more_hidden_shapes_are_dropped(case):
    test, test_ids = MORE_HIDDEN[case]
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    assert _split(BASE_A, test, ids, test_ids) == (set(), {"t.py::TestA::test_x"})


@pytest.mark.parametrize("field", ["test_x = 1\n", "test_x: int = 0\n", "test_x: int\n"])
def test_a_constant_with_the_test_name_in_another_class_is_not_a_test(field):
    test = "class Cfg:\n    " + field + TEST_A
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    assert _split(BASE_A, test, ids, ids[1:]) == ({"t.py::TestA::test_x"}, set())


def test_a_deep_expression_does_not_crash_the_split():
    deep = " + ".join(["f"] * 3000) + "\n"
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    gone = {"t.py::TestA::test_x"}
    assert _split(BASE_A, TEST_A + "v = " + deep, ids, ids[1:]) == (gone, set())
    # bound to the test's name in an uncollected class: not provably gone, never a crash
    assert _split(BASE_A, "class Hid:\n    test_x = " + deep + TEST_A, ids, ids[1:]) == (
        set(),
        gone,
    )


# Codex r1 on #1130: where the rebinding sits decides whether it counts
CLASS_BASE = "def _other(self):\n    assert True\n" + BASE_A
CLASS_TEST = "def _other(self):\n    assert True\nclass TestA:\n" + KEEP
STRING_REBIND = {
    # module or class level: any target counts, an alias of the class included
    "module-level-class-alias": ("cls = TestA\ncls.test_x = _other\n", True),
    "in-function-on-a-file-class": ("def _put():\n    TestA.test_x = _other\n_put()\n", True),
    "in-function-on-sys-modules": (
        "import sys\ndef _put():\n"
        "    setattr(sys.modules[__name__].TestA, 'test_x', _other)\n_put()\n",
        True,
    ),
    # inside a fixture, a store on a plain object does not touch what pytest collects
    "fixture-setattr-on-self": (
        "    def setup_method(self):\n        setattr(self, 'test_x', 1)\n",
        False,
    ),
    "fixture-dict-update": (
        "    def setup_method(self):\n        cfg = {}\n        cfg.update(test_x=1)\n",
        False,
    ),
    "fixture-dict-subscript": (
        "    def setup_method(self):\n        cfg = {}\n        cfg['test_x'] = 1\n",
        False,
    ),
}


@pytest.mark.parametrize("case", sorted(STRING_REBIND))
def test_a_rebinding_by_string_counts_only_where_it_can_change_collection(case):
    extra, rebinds = STRING_REBIND[case]
    base = (
        CLASS_BASE + extra
        if not extra.startswith("    ")
        else CLASS_BASE.replace(KEEP, KEEP + extra)
    )
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    gone = {"t.py::TestA::test_x"}
    assert _split(base, CLASS_TEST, ids, ids[1:]) == ((set(), gone) if rebinds else (gone, set()))


# --- pins of what 1608c2b2 already got right
def test_original_kept_in_if_with_top_level_shadow_is_dropped():
    test = "if True:\n" + "".join(f"    {ln}\n" for ln in BASE_A.splitlines())
    test += "class TestA:\n" + KEEP
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    assert _split(BASE_A, test, ids, ids[1:]) == (set(), {"t.py::TestA::test_x"})


def test_owner_rebound_by_assignment_keeps_original_mention():
    test = BASE_A + "class _Other:\n" + KEEP + "TestA = _Other\n"
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    assert _split(BASE_A, test, ids, ids[1:]) == (set(), {"t.py::TestA::test_x"})


def test_847_rebinding_with_real_ids_is_dropped():
    base = (
        OTHER
        + "def test_x():\n    assert False\ntest_x = _other\ndef test_keep():\n    assert True\n"
    )
    test = OTHER + "def test_keep():\n    assert True\n"
    ids = ["t.py::test_x", "t.py::test_keep"]
    assert _split(base, test, ids, ids[1:]) == (set(), {"t.py::test_x"})


# --- honest deletions that stay removed under #1127
def test_name_kept_in_another_collected_class_is_removed():
    base = BASE_A + "class TestB:\n    def test_x(self):\n        assert True\n"
    test = "class TestA:\n" + KEEP + "class TestB:\n    def test_x(self):\n        assert True\n"
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep", "t.py::TestB::test_x"]
    assert _split(base, test, ids, ids[1:]) == ({"t.py::TestA::test_x"}, set())


def test_unchanged_uncollected_helper_class_from_the_base_is_removed():
    helper = "class Helper:\n    def test_x(self):\n        assert True\n"
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    test = helper + "class TestA:\n" + KEEP
    assert _split(helper + BASE_A, test, ids, ids[1:]) == ({"t.py::TestA::test_x"}, set())


def test_changed_uncollected_helper_class_is_dropped():
    helper = "class Helper:\n    def test_x(self):\n        assert {}\n"
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    test = helper.format(False) + "class TestA:\n" + KEEP
    got = _split(helper.format(True) + BASE_A, test, ids, ids[1:])
    assert got == (set(), {"t.py::TestA::test_x"})


def test_self_attribute_named_like_the_test_is_not_a_rebinding():
    base = "class TestA:\n    def setup_method(self):\n        self.test_x = 1\n"
    base += "    def test_x(self):\n        assert True\n" + KEEP
    test = "class TestA:\n" + KEEP
    ids = ["t.py::TestA::test_x", "t.py::TestA::test_keep"]
    assert _split(base, test, ids, ids[1:]) == ({"t.py::TestA::test_x"}, set())


# --- end to end (run_task, real sandbox): the before/after table of the #1127 PR
CLASS_SUB = "class TestCalc:\n    def test_sub(self):\n        assert calc.sub(5, 3) == 2\n"
E2E_TEST_COMMITS = {
    # 1608c2b2: verified for both candidates (test_sub "removed"); now the test commit is refused
    "hides-sub": "import calc\n" + CLASS_SUB + MUL_TEST + "class TestCalc:\n    pass\n",
    # an honest deletion: verified on 1608c2b2 and now
    "deletes-sub": "import calc\nclass TestCalc:\n    pass\n" + MUL_TEST,
}


@pytest.mark.sandbox
@pytest.mark.parametrize("good", [True, False], ids=["fix", "breaks-sub"])
@pytest.mark.parametrize("shape", sorted(E2E_TEST_COMMITS))
def test_a_shadowed_base_test_refuses_the_test_commit_end_to_end(repo, tmp_path, shape, good):
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST,
                         "tests/test_calc.py": "import calc\n" + CLASS_SUB})  # fmt: skip
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    candidate = (CALC if good else SUB_OFF) + MUL
    result = _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": candidate},
                  {"tests/test_calc.py": E2E_TEST_COMMITS[shape]}, check)  # fmt: skip
    if shape == "hides-sub":
        assert not result.verified
        assert "TEST_COMMIT_DROPS_BASE_TESTS" in result.reason, result.reason
    else:
        assert result.status == "COMPLETED" and result.verified, result.reason
