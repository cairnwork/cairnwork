"""#925: the AST reader (deferred_nodes) does not read every test pytest collects. A test commit
that changes a test inside a module-level block or a nested test class is refused,
DEFERRED_TESTS_UNREADABLE, also beside a visible new test (which before made a target naming
only that test count as covering the file); so is one that binds a ``test*``/``Test*`` name another
way (assignment, walrus, loop or match target, import) to anything but a literal, or adds a star
import. Removing a class decorator changes each of the class's tests. An unchanged hidden test on
the base, a ``test*`` defined inside a function and a literal ``test*`` value refuse nothing."""

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, make_test_commit
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import ensure_test_commit, new_tests, untargeted

F = "tests/test_regression.py"
WRONG = "        assert add(2, 3) == 6\n"  # the fix gives 5: a test the fix does not pass
HIDDEN = {
    "if-block": "if True:\n    def test_b():\n" + WRONG,
    "try-block": "try:\n    def test_b():\n" + WRONG + "except Exception:\n    pass\n",
    "with-block": "import contextlib\nwith contextlib.nullcontext():\n    def test_b():\n" + WRONG,
    "nested-class": "class TestOuter:\n    class TestInner:\n        def test_b(self):\n    "
    + WRONG,
    # names bound another way (#925 r2): pytest collects whatever the module or class holds
    "assign": "test_b = make_test()\n",
    "annotated": "test_b: object = make_test()\n",
    "augmented": "test_b = 0\ntest_b += make_test()\n",
    "walrus": "if (test_b := make_test()):\n    pass\n",
    "loop-target": "for test_b in [make_test()]:\n    pass\n",
    "type-call": "TestB = type('TestB', (), {'test_b': make_test()})\n",
    "nested-inherited": "class TestOuter:\n    class TestInner(Base):\n        pass\n",
    "class-body": "class TestOuter:\n    test_b = staticmethod(make_test())\n",
    "from-import": "from helpers import test_b\n",
    "import-as": "import helpers as test_b\n",
    "star-import": "from helpers import *\n",  # #925 r3: pytest collects the test* it brings
    "match-capture": "match make_test():\n    case test_b:\n        pass\n",
}
NOT_HIDDEN = (  # a literal value, the TestCase base, a module: pytest collects none of them
    "test_data = [1, 2, {'a': (3,)}]\nfrom unittest import TestCase\nimport testtools\n"
)


def _new_tests(project, files):
    base, test_commit = make_test_commit(project, files)
    ensure_test_commit(project, test_commit)
    return new_tests(project, base, test_commit, tuple(files))


@pytest.mark.parametrize("shape", sorted(HIDDEN))
def test_a_changed_hidden_test_is_refused_beside_a_visible_one(project, shape):
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + HIDDEN[shape]})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE" and F in str(err.value)


def test_removing_a_class_skip_changes_each_of_its_tests(project):
    cls = "class TestY:\n    def test_y(self):\n        assert add(2, 2) == 4\n"
    commit_change(project, {F: "import pytest\n" + REGRESSION + "@pytest.mark.skip\n" + cls})
    nodes = _new_tests(project, {F: "import pytest\n" + REGRESSION + cls})
    assert nodes == {f"{F}::TestY::test_y"}
    assert untargeted((F,), nodes, (f"{F}::test_regression",)) == (F,)


def test_an_unchanged_hidden_test_a_local_def_and_literals_refuse_nothing(project):
    local = "def test_c():\n    def test_inner():\n        pass\n    test_inner()\n"
    commit_change(project, {F: HIDDEN["if-block"]})
    nodes = _new_tests(project, {F: HIDDEN["if-block"] + NOT_HIDDEN + REGRESSION + local})
    assert nodes == {f"{F}::test_regression", f"{F}::test_c"}


@pytest.mark.sandbox
def test_verify_refuses_a_hidden_test_a_wrong_fix_would_fail(project, state):
    """Before #925: VERIFIED, the target names only test_regression and test_b never ran."""
    base, test_commit = make_test_commit(project, {F: REGRESSION + HIDDEN["if-block"]})
    cand = candidate(project, base, {"src/calc.py": FIX})
    command = f"python3 -m pytest -q {F}::test_regression"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert code == 2 and out["status"] == "REFUSED" and out["verified"] is False, out
    assert out["reason"].startswith("DEFERRED_TESTS_UNREADABLE"), out
