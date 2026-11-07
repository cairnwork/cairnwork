"""#994: tests pytest collects that the AST reader does not read, attached outside a class body
(``Cls.test_x = f``, ``setattr``, ``sys.modules[__name__]``, ``globals()``, a same-file class
decorator or metaclass that writes ``test*``), and a ``pytestmark`` bound by an import.

An attaching statement that can be read statically (a literal name, a value made only of
same-file code) counts with everything it reads, so a changed ``f`` behind an unchanged
assignment is a change, refused DEFERRED_TESTS_UNREADABLE. One that cannot (a computed name, a
value that reaches an import) refuses a test commit that changes its file whenever it is there.
A module-level import binding ``pytestmark`` counts for every test. Known limit, pinned here: a
class decorator or metaclass imported from another module is not read (as #968 (b))."""

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
META = "class Meta(type):\n    def __init__(cls, *a):\n        cls.test_x = _impl\n"

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
    "trap-setattr": (CLS + IMPL.format(RIGHT) + "setattr(TestX, 'test_x', _impl)\n",
                     CLS + IMPL.format(WRONG) + "setattr(TestX, 'test_x', _impl)\n"),
    "trap-decorator": (IMPL.format(RIGHT) + WRITER + "@add_tests\n" + CLS,
                       IMPL.format(WRONG) + WRITER + "@add_tests\n" + CLS),
    "trap-metaclass": (IMPL.format(RIGHT) + META + "class TestX(metaclass=Meta):\n    pass\n",
                       IMPL.format(WRONG) + META + "class TestX(metaclass=Meta):\n    pass\n"),
    # not readable: the value reaches an import, the statement itself unchanged
    "from-an-import": ("from helpers import impl\n" + CLS + "TestX.test_x = impl\n",
                       "from helpers import impl\n" + CLS + "TestX.test_x = impl\n"),
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
    "rewritten-as-a-def": CLS + "def test_x():\n" + RIGHT,
    "unchanged-readable": CLS + IMPL.format(RIGHT) + "TestX.test_x = _impl\n",
}


@pytest.mark.parametrize("shape", sorted(CONTROLS))
def test_a_plain_assignment_or_an_unchanged_readable_attachment_refuses_nothing(project, shape):
    body = CONTROLS[shape]
    commit_change(project, {F: "from calc import add\n" + body})
    nodes = _new_tests(project, {F: REGRESSION + body})
    assert f"{F}::test_regression" in nodes and not any("TestX::test_x" in n for n in nodes)


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
def test_verify_refuses_the_trap_a_wrong_fix_would_fail(project, state):
    """Before #994: VERIFIED. Only _impl's body changed; the target names test_regression."""
    before, after = ATTACHED["trap-attribute"]
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
