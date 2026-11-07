"""#994: tests pytest collects that the AST reader does not read, attached outside a class body
(``Cls.test_x = f``, ``setattr``, ``sys.modules[__name__]``, ``globals()``, a same-file class
decorator or metaclass that writes ``test*``, a ``test*`` name bound at module level or in a
top-level class body, a class body's own helper or a method's decorator), and a ``pytestmark``
bound by an import.

An attaching statement that can be read statically (a literal name, a value made only of
same-file code) counts with everything it reads, so a changed ``f`` behind an unchanged
assignment is a change, refused DEFERRED_TESTS_UNREADABLE. One that cannot (a computed name, a
value that reaches an import) refuses a test commit that changes its file whenever it is there.
A module-level import binding ``pytestmark`` counts for every test. Known limit, pinned here: no
other module's code is read (as #968 (b)), so nothing an import brings in is followed: a class
decorator, a metaclass or a base class imported from elsewhere, or an imported function called at
import or while a class is built. pytest collects from ``__dict__`` only (``_pytest/python.py``),
so a module-level ``__getattr__`` or ``__dir__`` adds no test (Codex r1 on #1003, disputed)."""

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, make_test_commit
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import ensure_test_commit, new_tests, untargeted

F = "tests/test_regression.py"
WRONG = "    assert add(2, 3) == 6\n"  # the fix gives 5: a test the fix does not pass
RIGHT = "    assert add(2, 2) == 4\n"
CLS = "class TestX:\n    pass\n"
IMPL = "def _impl(self):\n{}"
WRITER = "def add_tests(cls):\n    cls.test_x = _impl\n    return cls\n"
FN_IMPL = "def _impl():\n{}"
INSTALL = "def _install():\n    globals().update({'test_x': _impl})\n_install()\n"
META = "class Meta(type):\n    def __init__(cls, *a):\n        cls.test_x = _impl\n"
OK = "def _ok(v):\n    assert v\n"
LAMBDA = "_impl = lambda self: _ok({})\nTestX.test_x = _impl\n"  # not a def
HOLDER = (
    "class H:\n    @staticmethod\n    def add_tests(cls):\n        cls.test_x = _impl\n"
    "        return cls\n@H.add_tests\n" + CLS
)
SUBCLASS = (
    "class Base:\n    def __init_subclass__(cls):\n        cls._add()\n"
    "    @classmethod\n    def _add(cls):\n        cls.test_x = _impl\n"
    "class TestX(Base):\n    pass\n"
)
DEFAULT = "def _h(a=setattr(TestX, 'test_x', _impl)):\n    pass\n"
TRAP = "    assert add(2, 2) == WANT\n" + "TestX.test_x = _impl\n"  # only WANT changes
DICT = "import sys\nsys.modules[__name__].__dict__['test_x'] = _impl\n"

# (on the base, in the test commit) beside REGRESSION: each changes an attached test
ATTACHED = {
    "attribute": ("", CLS + IMPL.format(WRONG) + "TestX.test_x = _impl\n"),
    "setattr": ("", CLS + IMPL.format(WRONG) + "setattr(TestX, 'test_x', _impl)\n"),
    "setattr-computed": ("", CLS + IMPL.format(WRONG) + "setattr(TestX, 'test_' + 'x', _impl)\n"),
    "sys-modules": ("", "import sys\n" + IMPL.format(WRONG).replace("self", "")
                    + "sys.modules[__name__].test_x = _impl\n"),
    "globals": ("", IMPL.format(WRONG).replace("self", "") + "globals()['test_x'] = _impl\n"),
    "class-body": ("", "class Holder:\n    pass\n" + IMPL.format(WRONG)
                   + "class TestX:\n    vars()['test_x'] = _impl\n"),
    # the trap (owner, 11/07): the assignment stays byte-identical, only f's body changes
    "trap-attribute": (CLS + IMPL.format(RIGHT) + "TestX.test_x = _impl\n",
                       CLS + IMPL.format(WRONG) + "TestX.test_x = _impl\n"),
    "trap-module-name": (FN_IMPL.format(RIGHT) + "test_x = _impl\n",
                         FN_IMPL.format(WRONG) + "test_x = _impl\n"),
    "trap-class-name": (IMPL.format(RIGHT) + "class TestX:\n    test_x = _impl\n",
                        IMPL.format(WRONG) + "class TestX:\n    test_x = _impl\n"),
    "qualified-globals": ("", "import builtins\n" + FN_IMPL.format(WRONG)
                          + "builtins.globals()['test_x'] = _impl\n"),
    "trap-class-helper": (
        IMPL.format(RIGHT) + "class TestY:\n    pass\nclass TestX:\n"
        "    def _add():\n        TestY.test_x = _impl\n    _add()\n",
        IMPL.format(WRONG) + "class TestY:\n    pass\nclass TestX:\n"
        "    def _add():\n        TestY.test_x = _impl\n    _add()\n",
    ),
    "trap-method-decorator": (
        IMPL.format(RIGHT) + "def reg(f):\n    TestY.test_x = _impl\n    return f\n"
        "class TestY:\n    pass\nclass TestX:\n    @reg\n"
        "    def helper(self):\n        pass\n",
        IMPL.format(WRONG) + "def reg(f):\n    TestY.test_x = _impl\n    return f\n"
        "class TestY:\n    pass\nclass TestX:\n    @reg\n"
        "    def helper(self):\n        pass\n",
    ),
    "trap-setattr": (CLS + IMPL.format(RIGHT) + "setattr(TestX, 'test_x', _impl)\n",
                     CLS + IMPL.format(WRONG) + "setattr(TestX, 'test_x', _impl)\n"),
    "trap-decorator": (IMPL.format(RIGHT) + WRITER + "@add_tests\n" + CLS,
                       IMPL.format(WRONG) + WRITER + "@add_tests\n" + CLS),
    "trap-metaclass": (IMPL.format(RIGHT) + META + "class TestX(metaclass=Meta):\n    pass\n",
                       IMPL.format(WRONG) + META + "class TestX(metaclass=Meta):\n    pass\n"),
    # Codex r1 on #1003: a namespace written by a call, an alias, or a function given it
    "namespace-update": ("", FN_IMPL.format(WRONG) + "globals().update({'test_x': _impl})\n"),
    "update-keyword": ("", FN_IMPL.format(WRONG) + "globals().update(test_x=_impl)\n"),
    "setdefault": ("", FN_IMPL.format(WRONG) + "globals().setdefault('test_x', _impl)\n"),
    "trap-update-helper": (FN_IMPL.format(RIGHT) + INSTALL, FN_IMPL.format(WRONG) + INSTALL),
    "alias": ("", FN_IMPL.format(WRONG) + "ns = globals()\nns['test_x'] = _impl\n"),
    "handed-off": ("", FN_IMPL.format(WRONG) + "def fill(ns):\n    ns['test_x'] = _impl\n"
                   "fill(globals())\n"),
    # not readable: the value reaches an import, the statement itself unchanged
    "from-an-import": ("from helpers import impl\n" + CLS + "TestX.test_x = impl\n",
                       "from helpers import impl\n" + CLS + "TestX.test_x = impl\n"),
    # Claude r1 on #1003: a value not bound by a def or class, a class holding the decorator,
    # a classmethod run while a subclass is built, more attaching forms, each guard pinned
    "trap-assigned": (CLS + OK + LAMBDA.format("add(2, 2) == 4"),
                      CLS + OK + LAMBDA.format("add(2, 3) == 6")),
    "trap-static-decorator": (IMPL.format(RIGHT) + HOLDER, IMPL.format(WRONG) + HOLDER),
    "trap-init-subclass": (IMPL.format(RIGHT) + SUBCLASS, IMPL.format(WRONG) + SUBCLASS),
    "trap-function-default": (CLS + IMPL.format(RIGHT) + DEFAULT,
                              CLS + IMPL.format(WRONG) + DEFAULT),
    "trap-dict": (FN_IMPL.format(RIGHT) + DICT, FN_IMPL.format(WRONG) + DICT),
    "trap-module-value": ("WANT = 4\n" + CLS + IMPL.format(TRAP),
                          "WANT = 6\n" + CLS + IMPL.format(TRAP)),
    "laundered-import": ("from helpers import impl\n" + CLS + "_v = impl\nTestX.test_x = _v\n",
                         "from helpers import impl\n" + CLS + "_v = impl\nTestX.test_x = _v\n"),
    "import-in-helper": (CLS + "def _go():\n    from helpers import impl\n    TestX.test_x = impl\n"
                         "_go()\n",) * 2,
    "for-target": ("", CLS + IMPL.format(WRONG) + "for TestX.test_x in [_impl]:\n    pass\n"),
    "with-target": ("", CLS + IMPL.format(WRONG) + "with _cm() as TestX.test_x:\n    pass\n"),
    "builtins-setattr": ("", "import builtins\n" + CLS + IMPL.format(WRONG)
                         + "builtins.setattr(TestX, 'test_x', _impl)\n"),
    "type-setattr": ("", CLS + IMPL.format(WRONG) + "type.__setattr__(TestX, 'test_x', _impl)\n"),
    "a-test-class": ("", FN_IMPL.format(WRONG)
                     + "globals()['TestY'] = type('TestY', (), {'test_x': staticmethod(_impl)})\n"),
    "removed-unreadable": (CLS + IMPL.format(RIGHT) + "setattr(TestX, 'test_' + 'x', _impl)\n",
                           CLS + IMPL.format(RIGHT)),
    # Claude r2 on #1003: an import or an assignment over a def name, a keyword-only default
    "import-over-def": (CLS + IMPL.format(RIGHT) + "from helpers import _impl\n"
                        "TestX.test_x = _impl\n",) * 2,
    "assigned-over-def": (CLS + IMPL.format(RIGHT) + "_impl = staticmethod(_impl)\n"
                          "TestX.test_x = _impl\n",) * 2,
    "kw-only-default": ("", CLS + IMPL.format(WRONG)
                        + "def _h(*, a=setattr(TestX, 'test_x', _impl)):\n    pass\n"),
    # Claude r3 on #1003: each test-name value form, a method's defaults, a module's __setattr__
    **{shape: (IMPL.format(RIGHT) + tail, IMPL.format(WRONG) + tail) for shape, tail in {
        "trap-name-attribute": "class _H:\n    f = staticmethod(_impl)\ntest_x = _H.f\n",
        "trap-name-lambda": "test_x = lambda self=None: _impl(self)\n",
        "trap-name-call": "class TestZ:\n    test_x = staticmethod(_impl)\n",
        "trap-method-default": "class TestY:\n    pass\nclass Holder:\n"
        "    def m(self, a=setattr(TestY, 'test_x', _impl)):\n        pass\n",
        "trap-method-kw-default": "class TestY:\n    pass\nclass Holder:\n"
        "    def m(self, *, a=setattr(TestY, 'test_x', _impl)):\n        pass\n",
    }.items()},
    "module-setattr": ("", "import sys\n" + FN_IMPL.format(WRONG)
                       + "sys.modules[__name__].__setattr__('test_x', _impl)\n"),
    # #1024: the binding is unchanged and only the same-file function's body changes
    **{shape: (FN_IMPL.format(RIGHT) + tail, FN_IMPL.format(WRONG) + tail) for shape, tail in {
        "trap-tuple-unpack": "test_x, _other = (_impl, 1)\n",
        "trap-subscript": "test_x = [_impl][0]\n",
        "trap-conditional": "test_x = _impl if True else None\n",
        "trap-boolean": "test_x = True and _impl\n",
        "trap-module-for": "for test_x in (_impl,):\n    pass\n",
        "trap-imported-mark": "import pytest\ntest_x = pytest.mark.slow(_impl)\n",
        "trap-class-in-block": "if True:\n    class TestY:\n        test_x = _impl\n",
        "trap-nested-class": "class Outer:\n    class TestY:\n        test_x = _impl\n",
        "trap-global-helper": "def install():\n    global test_x\n    test_x = _impl\ninstall()\n",
    }.items()},
}  # fmt: skip


def _new_tests(project, files):
    base, test_commit = make_test_commit(project, files)
    ensure_test_commit(project, test_commit)
    return new_tests(project, base, test_commit, tuple(files))


@pytest.mark.parametrize("shape", sorted(ATTACHED))
def test_a_changed_attached_test_is_refused_beside_a_visible_one(project, shape):
    before, after = ATTACHED[shape]
    if before:
        commit_change(project, {F: "from calc import add\n" + before})
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + after})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE" and F in str(err.value)


CONTROLS = {  # no attached test: nothing refused, only test_regression is new
    "plain-attribute": CLS + "TestX.helper = len\nconfig = type('C', (), {})\nconfig.debug = 1\n",
    "test-named-literal": CLS + "TestX.test_data = [1, 2]\nsetattr(TestX, 'test_n', 3)\n",
    "module-data-values": "test_data = [1, 2]\ntest_label = 'sample'\ntest_count = 7\n",
    "rewritten-as-a-def": CLS + "def test_x():\n" + RIGHT,
    "unchanged-readable": CLS + IMPL.format(RIGHT) + "TestX.test_x = _impl\n",
    "namespace-reads": "DATA = 1\nglobals().update({'DATA': len})\nN = len(globals())\n"
    "X = globals().get('DATA')\nNAMES = [k for k in globals()]\n"
    "for _k in vars(len):\n    pass\n",
    "self-stores": "from helpers import make\nclass Base:\n    def setUp(self):\n"
    "        self.test_client = make()\n        setattr(self, 'test_db', make())\n"
    "class TestX(Base):\n    pass\n",
    "local-test-name": "from helpers import make\ndef _h():\n    test_x = make\n    return test_x\n"
    "_h()\n",
    "module-literal-name": "test_data = {'a': 1}\n",
    "module-data-name": "import os\ntest_data_path = os.path.join('a', 'b')\n"
    "test_cases = [os.sep, os.pathsep]\n",
    "local-test-name-in-block": "from helpers import make\nif True:\n    def _h():\n"
    "        test_x = make\n        return test_x\n",
    "test-method-named-in-class-body": "from helpers import make\nclass TestY:\n"
    "    def test_a(self):\n        setattr(type(self), 'test_b', make)\n    alias = test_a\n",
}


@pytest.mark.parametrize("shape", sorted(CONTROLS))
def test_a_plain_assignment_or_an_unchanged_readable_attachment_refuses_nothing(project, shape):
    body = CONTROLS[shape]
    commit_change(project, {F: "from calc import add\n" + body})
    nodes = _new_tests(project, {F: REGRESSION + body})
    assert f"{F}::test_regression" in nodes and not any("TestX::test_x" in n for n in nodes)


def test_a_changed_literal_attribute_refuses_nothing(project):
    commit_change(project, {F: "from calc import add\n" + CLS + "setattr(TestX, 'test_n', 3)\n"})
    after = REGRESSION + CLS + "setattr(TestX, 'test_n', 4)\n"
    assert _new_tests(project, {F: after}) == {f"{F}::test_regression"}


def test_an_imported_class_decorator_is_the_known_limit(project):
    """Not read, as #968 (b): the decorator's module is not followed. Pinned so a change here is
    a decision, not an accident."""
    body = "from helpers import add_tests\n@add_tests\n" + CLS
    commit_change(project, {F: "from calc import add\n" + body})
    assert _new_tests(project, {F: REGRESSION + body}) == {f"{F}::test_regression"}


def test_an_unparsable_file_with_an_attached_test_is_never_read_as_empty(project):
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + ATTACHED["attribute"][1] + "def broken(:\n"})
    assert err.value.code == "TEST_COMMIT_UNPARSABLE"


OLD = "def test_old():\n" + WRONG
MARK = {"tests/marks.py": "import pytest\npytestmark = skip = pytest.mark.skip\n"}
TAIL = REGRESSION.removeprefix("from calc import add\n")  # test_regression alone


@pytest.mark.parametrize(("before", "after"), [
    ("from marks import pytestmark\n", ""),  # removed: test_old is no longer skipped
    ("", "from marks import pytestmark\n"),  # added: test_old is now skipped
    ("from marks import skip as pytestmark\n", ""),
])  # fmt: skip
def test_an_import_binding_pytestmark_changes_every_test(project, before, after):
    commit_change(project, {**MARK, F: "from calc import add\n" + before + OLD})
    nodes = _new_tests(project, {F: "from calc import add\n" + after + OLD + TAIL})
    assert nodes == {f"{F}::test_old", f"{F}::test_regression"}
    assert untargeted((F,), nodes, (f"{F}::test_regression",)) == (F,)


def test_other_imports_and_a_class_body_pytestmark_import_add_nothing(project):
    cls = "class TestY:\n    from marks import pytestmark\n    def test_y(self):\n    " + RIGHT
    commit_change(project, {**MARK, F: "from calc import add\n" + cls + OLD})
    after = "from calc import add\nimport json\n" + cls + OLD + TAIL
    assert _new_tests(project, {F: after}) == {f"{F}::test_regression"}


@pytest.mark.sandbox
@pytest.mark.parametrize("shape", ["trap-attribute", "trap-assigned", "trap-static-decorator",
                                   "trap-module-value", "for-target", "trap-module-name",
                                   "trap-class-helper", "trap-method-decorator"])  # fmt: skip
def test_verify_refuses_the_trap_a_wrong_fix_would_fail(project, state, shape):
    """Each wrong candidate was VERIFIED before #994 or its r2 guards (the PR's table). The
    attached test fails the fix; the target names test_regression only."""
    before, after = ATTACHED[shape]
    if before:
        commit_change(project, {F: "from calc import add\n" + before})
    base, test_commit = make_test_commit(project, {F: REGRESSION + after})
    cand = candidate(project, base, {"src/calc.py": FIX})
    command = f"python3 -m pytest -q {F}::test_regression"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert code == 2 and out["status"] == "REFUSED" and out["verified"] is False, out
    assert out["reason"].startswith("DEFERRED_TESTS_UNREADABLE"), out


@pytest.mark.sandbox
@pytest.mark.parametrize(("target", "status"), [
    (f"{F}::test_regression", "REFUSED"),  # before #994: VERIFIED, test_old never ran
    (F, "VERIFIED"),  # control: the whole file named, a correct fix
])  # fmt: skip
def test_verify_and_a_removed_pytestmark_import(project, state, target, status):
    old = "def test_old():\n" + RIGHT  # skipped on the base, runs once the import goes
    marked = "from calc import add\nfrom marks import pytestmark\n" + old
    commit_change(project, {**MARK, F: marked})
    base, test_commit = make_test_commit(project, {F: REGRESSION + old})
    cand = candidate(project, base, {"src/calc.py": FIX if status == "VERIFIED" else WRONG_FIX})
    command = f"python3 -m pytest -q {target}"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert out["status"] == status, out
    if status == "REFUSED":
        assert out["reason"].startswith("DEFERRED_TESTS_NOT_TARGETED"), out


WRONG_FIX = "def add(a, b):\n    return 0 if a == 2 else a + b\n"  # passes 1+1, fails 2+2
