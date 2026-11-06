"""#925: the AST reader (deferred_nodes) does not read every test pytest collects. A test commit
that changes a test inside a module-level block or a nested test class is refused,
DEFERRED_TESTS_UNREADABLE, also beside a visible new test (which before made a target naming
only that test count as covering the file); so is one that binds a ``test*``/``Test*`` name another
way (assignment, walrus, loop or match target, import, a ``global`` in a function) to anything
but a literal, or adds a star import. Removing a class decorator changes each of the class's
tests. An unchanged hidden test on the base, a ``test*`` defined inside a function and a literal
``test*`` value refuse nothing."""

import ast

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, make_test_commit
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_nodes import test_functions as read_tests
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
    "global-in-function": "def _setup():\n    global test_b\n    test_b = make_test()\n_setup()\n",
    "global-in-class": "class Holder:\n    global test_b\n    def test_b():\n" + WRONG,
    # a TestCase the test commit's own tree may define can hold tests (Test* is a pytest name)
    "testcase-local": "from test_regression import TestCase\n",  # the file the commit changes
    "testcase-renamed": "from test_regression import Base as TestCase\n",
    "testcase-relative": "from .unittest import TestCase\n",
    "version-block": "import sys\nif sys.version_info >= (3, 8):\n    def test_b():\n" + WRONG,
}
NOT_HIDDEN = (  # a literal value, a framework's TestCase base, a module: none holds a test
    "test_data = [1, 2, {'a': (3,)}]\nfrom unittest import TestCase\nimport testtools\n"
    "from django.test import TestCase\n"  # no django module or directory in the tree
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


Y = "    def test_y(self):\n        assert add(2, 2) == 4\n"
SKIP = "pytestmark = pytest.mark.skip\n"
MARKS = {  # (on the base, in the test commit, the tests that change): #968
    "base-class": ("@pytest.mark.skip\nclass TestBase:\n" + Y, "class TestBase:\n" + Y,
                   {"TestBase::test_y", "TestSub::test_y"}),
    "second-base": ("@pytest.mark.skip\nclass Marked:\n    pass\nclass Tests:\n" + Y,
                    "class Marked:\n    pass\nclass Tests:\n" + Y, {"TestSub::test_y"}),
    "class-pytestmark": ("class TestBase:\n    " + SKIP + Y, "class TestBase:\n" + Y,
                         {"TestBase::test_y", "TestSub::test_y"}),
}  # fmt: skip


@pytest.mark.parametrize("shape", sorted(MARKS))
def test_removing_a_base_class_mark_changes_each_inherited_copy(project, shape):
    """#968: pytest applies every base class's marks along the MRO, so a copy a subclass
    inherits changes with any base's skip and must be targeted too."""
    before, after, changed = MARKS[shape]
    sub = "class TestSub(Marked, Tests):\n    pass\n" if shape == "second-base" else (
        "class TestSub(TestBase):\n    pass\n")  # fmt: skip
    commit_change(project, {F: "import pytest\n" + REGRESSION + before + sub})
    nodes = _new_tests(project, {F: "import pytest\n" + REGRESSION + after + sub})
    assert nodes == {f"{F}::{n}" for n in changed}
    assert untargeted((F,), nodes, (f"{F}::test_regression",)) == (F,)


FN = "def test_y():\n    assert add(2, 2) == 4\n"
CLS = "class TestX:\n" + Y
FLAG = "    pytestmark = [pytest.mark.skip] * SKIP\n" + Y
CHAIN = "class TestX:\n    @C.mark\n" + Y
ALL = {"test_y", "test_regression"}  # a module-level statement counts for every test
ON = "def on():\n    return flag()\n"
BASE = "class Base:\n    def __init_subclass__(cls, **kw):\n        pass\n"
SUBCLASS = "class Base:\n    def __init_subclass__(cls):\n        {}\nclass TestX(Base):\n" + Y
FX = "@pytest.fixture\ndef fx():\n    {}\n"
ALIAS = "@pytest.fixture(name='db')\ndef _db():\n    {}\n"  # jeter r2
HELPED = "COND = on()\n@pytest.mark.skipif(COND, reason='r')\n" + FN
REACH = {  # (on the base, in the test commit, the tests that change): #968 r3, Codex r2
    "alias": ("s = pytest.mark.skip\n@s\n" + FN, "s = pytest.mark.xfail\n@s\n" + FN, ALL),
    "skipif-condition": ("COND = True\n@pytest.mark.skipif(COND, reason='r')\n" + FN,
                         "COND = False\n@pytest.mark.skipif(COND, reason='r')\n" + FN, ALL),
    "method-alias": ("s = pytest.mark.skip\nclass TestX:\n    @s\n" + Y,
                     "s = pytest.mark.xfail\nclass TestX:\n    @s\n" + Y,
                     {"TestX::test_y", "test_regression"}),
    "attribute-pytestmark": (CLS + "TestX.pytestmark = pytest.mark.skip\n", CLS,
                             {"TestX::test_y", "test_regression"}),
    "setattr-pytestmark": (CLS + "setattr(TestX, 'pytestmark', pytest.mark.skip)\n", CLS,
                           {"TestX::test_y", "test_regression"}),
    "through-a-helper": ("def on():\n    return True\n" + HELPED,
                         "def on():\n    return False\n" + HELPED, ALL),
    "module-pytestmark-class": ("pytestmark = pytest.mark.skip\n" + CLS, CLS,
                                {"TestX::test_y", "test_regression"}),  # jeter r2
    "uncollected-class": ("class TestX:\n    def __init__(self):\n        pass\n" + Y,
                          "pytestmark = pytest.mark.filterwarnings('ignore')\nclass TestX:\n"
                          "    def __init__(self):\n        pass\n" + Y,
                          {"test_regression"}),  # jeter r2: no marks on a class pytest skips
    # Gemini r3 at 95770d5: a class attribute or flag, an attribute chain, a mutated mark list
    "class-attribute-mark": ("class TestX:\n    MARK = pytest.mark.skip\n    @MARK\n" + Y,
                             "class TestX:\n    MARK = pytest.mark.xfail\n    @MARK\n" + Y,
                             {"TestX::test_y"}),
    "class-flag": ("class TestX:\n    SKIP = True\n" + FLAG,
                   "class TestX:\n    SKIP = False\n" + FLAG, {"TestX::test_y"}),
    "attribute-chain": ("class C:\n    pass\nC.mark = pytest.mark.skip\n" + CHAIN,
                        "class C:\n    pass\nC.mark = pytest.mark.xfail\n" + CHAIN,
                        {"TestX::test_y", "test_regression"}),
    "mark-list-append": ("pytestmark = []\npytestmark.append(pytest.mark.skip)\n" + FN,
                         "pytestmark = []\n" + FN, ALL),
    "new-test-in-a-marked-class": (CLS + "TestX.pytestmark = pytest.mark.skip\n",
                                   CLS + "    def test_z(self):\n        pass\n"
                                   "TestX.pytestmark = pytest.mark.skip\n", {"TestX::test_z"}),
    "helper-chain": ("def flag():\n    return True\n" + ON + HELPED,
                     "def flag():\n    return False\n" + ON + HELPED, ALL),  # followed in turn
    # owner ruling on A'': a changed import source changes each test whose marks read the name
    "import-source": ("from marks_a import s\n@s\n" + FN, "from marks_b import s\n@s\n" + FN,
                      {"test_y"}),
    "import-source-class": ("from marks_a import s\nclass TestX:\n    @s\n" + Y,
                            "from marks_b import s\nclass TestX:\n    @s\n" + Y, {"TestX::test_y"}),
    "import-source-pytestmark": ("from marks_a import s\npytestmark = s\n" + FN,
                                 "from marks_b import s\npytestmark = s\n" + FN, ALL),
    # #979 r1 (Gemini, jeter): every other same-file place that can mark or skip a test
    "metaclass": ("class Meta(type):\n    pass\nclass TestX(metaclass=Meta):\n" + Y,
                  "class Meta(type):\n    x = 1\nclass TestX(metaclass=Meta):\n" + Y,
                  {"TestX::test_y"}),
    "class-keyword": (BASE + "class TestX(Base, on=True):\n" + Y,
                      BASE + "class TestX(Base, on=False):\n" + Y, {"TestX::test_y"}),
    "init-subclass": (SUBCLASS.format("cls.pytestmark = pytest.mark.skip"),
                      SUBCLASS.format("pass"), {"TestX::test_y"}),
    "class-helper": ("class TestX:\n    def mark(f):\n        return pytest.mark.skip(f)\n"
                     "    @mark\n" + Y, "class TestX:\n    def mark(f):\n        return f\n"
                     "    @mark\n" + Y, {"TestX::test_y"}),
    "setup-method": ("class TestX:\n    def setup_method(self):\n        pytest.skip('r')\n" + Y,
                     "class TestX:\n    def setup_method(self):\n        pass\n" + Y,
                     {"TestX::test_y"}),
    "setup-module": ("def setup_module():\n    pytest.skip('r', allow_module_level=True)\n" + FN,
                     "def setup_module():\n    pass\n" + FN, ALL),
    "autouse-fixture": ("@pytest.fixture(autouse=True)\ndef auto():\n    pytest.skip('r')\n" + FN,
                        "@pytest.fixture(autouse=True)\ndef auto():\n    pass\n" + FN, ALL),
    "hook": ("def pytest_runtest_setup(item):\n    pytest.skip('r')\n" + FN,
             "def pytest_runtest_setup(item):\n    pass\n" + FN, ALL),
    "fixture-argument": (FX.format("pytest.skip('r')") + "def test_y(fx):\n    pass\n",
                         FX.format("pass") + "def test_y(fx):\n    pass\n", {"test_y"}),
    "method-fixture-argument": (FX.format("pytest.skip('r')") + "class TestX:\n"
                                "    def test_y(self, fx):\n        pass\n",
                                FX.format("pass") + "class TestX:\n"
                                "    def test_y(self, fx):\n        pass\n", {"TestX::test_y"}),
    "fixture-name-alias": (ALIAS.format("pytest.skip('r')") + "def test_y(db):\n    pass\n",
                           ALIAS.format("pass") + "def test_y(db):\n    pass\n", {"test_y"}),
    "usefixtures": (FX.format("pytest.skip('r')") + "@pytest.mark.usefixtures('fx')\n" + FN,
                    FX.format("pass") + "@pytest.mark.usefixtures('fx')\n" + FN, {"test_y"}),
    "module-constant": ("DATA = 1\n" + FN, "DATA = 2\n" + FN, ALL),  # counted whole: the cost
    "unrelated": (FN, "import json\ndef helper():\n    return json.dumps(1)\n" + FN
                  + "def test_new():\n    assert helper()\n", {"test_new"}),  # read by no mark
}  # fmt: skip


@pytest.mark.parametrize("shape", sorted(REACH))
def test_a_mark_reached_from_outside_the_test_changes_it(project, shape):
    """#968 r3: a mark a test gets from a module-level statement (an alias, a condition, a
    ``pytestmark`` set from outside the class) is part of the test."""
    before, after, changed = REACH[shape]
    commit_change(project, {F: "import pytest\n" + REGRESSION + before})
    nodes = _new_tests(project, {F: "import pytest\n" + REGRESSION + after})
    assert nodes == {f"{F}::{n}" for n in changed}


def test_a_fingerprint_does_not_grow_with_the_file():
    """#979 r1 (jeter): each test held a full copy of the module's statements, so memory grew as
    tests times file size. A fingerprint is now a fixed-size digest of digests."""
    data = "DATA = [" + ",".join(map(str, range(5000))) + "]\n"
    text = "import pytest\n" + data + FN + "class TestX:\n" + Y
    functions, _ = read_tests(ast.parse(text))
    assert {len(v) for v in functions.values()} == {64}


def test_removing_a_module_pytestmark_changes_every_test(project):
    test_y = "def test_y():\n    assert add(2, 2) == 4\n"
    commit_change(project, {F: "import pytest\n" + SKIP + REGRESSION + test_y})
    nodes = _new_tests(project, {F: "import pytest\n" + REGRESSION + test_y})
    assert nodes == {f"{F}::test_regression", f"{F}::test_y"}


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


@pytest.mark.sandbox
def test_verify_refuses_a_test_case_imported_from_a_base_helper(project, state):
    """#925 follow-up. Before: VERIFIED. Any ``from x import TestCase`` was trusted, but pytest
    collects the helper's ``TestCase`` tests in this module, and the target never ran them."""
    helper = "from calc import add\nclass TestCase:\n    def test_b(self):\n" + WRONG
    commit_change(project, {"tests/helpers.py": helper})  # on the base; the test commit keeps it
    files = {F: REGRESSION + "from helpers import TestCase\n"}
    base, test_commit = make_test_commit(project, files)
    cand = candidate(project, base, {"src/calc.py": FIX})
    command = f"python3 -m pytest -q {F}::test_regression"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert code == 2 and out["status"] == "REFUSED", out
    assert out["reason"].startswith("DEFERRED_TESTS_UNREADABLE"), out


INHERIT = (
    "class TestBase:\n    value = 2\n    def test_y(self):\n"
    "        assert add(self.value, 3) == self.value + 3\n"
    "class TestSub(TestBase):\n    value = 3\n"
)
WRONG_FIX = "def add(a, b):\n    return 0 if a == 3 else a + b\n"  # passes TestBase, not TestSub


@pytest.mark.sandbox
@pytest.mark.parametrize(("fix", "target", "status"), [
    (WRONG_FIX, f"{F}::TestBase::test_y", "REFUSED"),  # main: VERIFIED, TestSub's copy never ran
    (FIX, F, "VERIFIED"),  # the whole file named, a correct fix
])  # fmt: skip
def test_verify_refuses_an_unrun_copy_of_a_test_whose_base_class_skip_was_removed(
    project, state, fix, target, status
):
    """#968: removing a base class's skip also unskips the copy a subclass inherits."""
    commit_change(
        project, {F: "import pytest\nfrom calc import add\n@pytest.mark.skip\n" + INHERIT}
    )
    base, test_commit = make_test_commit(project, {F: "import pytest\n" + REGRESSION + INHERIT})
    cand = candidate(project, base, {"src/calc.py": fix})
    command = f"python3 -m pytest -q {F}::test_regression {target}"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert out["status"] == status, out
    if status == "REFUSED":
        assert out["reason"].startswith("DEFERRED_TESTS_NOT_TARGETED"), out
        assert "請點名整個檔案" in out["reason"], out


@pytest.mark.parametrize("shadow", ["tests/unittest.py", "tests/lib/deep/unittest/__init__.py"])
def test_a_test_case_name_the_test_commit_can_shadow_is_not_trusted(project, shadow):
    """#925: a test commit's own file named like the module, at any depth (prepend mode or a
    pythonpath entry may put its directory first), may define a TestCase that holds tests."""
    case = "class TestCase:\n    def test_b(self):\n" + WRONG
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + "from unittest import TestCase\n", shadow: case})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"


CASES = "class TestCase:\n    def test_b(self):\n" + WRONG
PLAIN_BASE = "class TestCase:\n    def setUp(self):\n        pass\n"


@pytest.mark.parametrize(
    ("base_files", "module"),
    [  # the base's own module, unchanged by the test commit, anywhere in the tree
        ({"testing_helpers.py": CASES}, "testing_helpers"),  # cw#964 Codex r1: root level
        ({"testing_helpers/__init__.py": CASES}, "testing_helpers"),  # a root package
        ({"helpers/base.py": CASES}, "helpers.base"),  # a dotted import, its top in the tree
        ({"lib/cases/__init__.py": "from .impl import TestCase\n", "lib/cases/impl.py": CASES},
         "cases"),  # re-exported from a file with tests
        ({"fw/test/__init__.py": "from fw.test.base import TestCase\n",
          "fw/test/base.py": PLAIN_BASE}, "fw.test"),  # no tests, but a tree name: accepted cost
    ],
)  # fmt: skip
def test_a_test_case_from_a_module_the_tree_holds_is_hidden(project, base_files, module):
    """#925 (layer1 ruling on cw#964): the whole tree is listed, and a top-level module name
    that is a file stem or a directory at any depth is not trusted, whatever the file holds."""
    commit_change(project, base_files)
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + f"from {module} import TestCase\n"})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
