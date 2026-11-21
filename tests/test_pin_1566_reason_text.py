"""#1574: pin each shape the #1566 DEFERRED_TESTS_UNREADABLE reason names, hidden and attached.
The wording is hard-coded here on purpose, so a reworded or dropped shape turns a test red."""

import ast

import pytest
from test_attached_deferred_guard import ESCAPES, F
from test_hidden_deferred_tests import HIDDEN, REGRESSION, _new_tests

from cairnwork.deferred_attached import refuse_attached
from cairnwork.deferred_tests import TestCommitRejected as Rejected

HIDDEN_REASON = (
    "a test inside a module-level if/try/with/loop block, a nested test class,"
    " a star import, a class-level global, a rebound test name or an imported"
    " TestCase (local, renamed or relative) changed; Cairnwork cannot tell which tests"
    " it adds. Define it with a top-level def test_* or in a top-level Test* class"
)
ATTACHED_REASON = (
    "a test attached outside a class body (an attribute, setattr or namespace"
    " store, or a class decorator or metaclass) changed or cannot be read; Cairnwork cannot"
    " tell which tests it adds. Define it as def test_… in a class body or at top level"
)
# one real input per attached shape the reason names
ATTACHED = {
    **ESCAPES,
    "setattr": "import builtins\nbuiltins.setattr(globals(), 'test_x', lambda: 1)\n",
    "namespace": "globals()['test_x'] = lambda: 1\n",
    "class-decorator": "def deco(c):\n    c.test_x = lambda self: 1\n    return c\n"
    "@deco\nclass TestX:\n    pass\n",
    "metaclass": "class Meta(type):\n    pass\n"
    "class TestX(metaclass=Meta):\n    test_x = len.__self__.eval('1')\n",
}


@pytest.mark.parametrize("shape", sorted(HIDDEN))
def test_hidden_refusal_names_every_shape_and_the_fix(project, shape):
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + HIDDEN[shape]})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
    assert HIDDEN_REASON in str(err.value)


@pytest.mark.parametrize("shape", sorted(ATTACHED))
def test_attached_refusal_names_every_shape_and_the_fix(shape):
    with pytest.raises(Rejected) as err:
        refuse_attached(F, ast.parse(ATTACHED[shape]), ast.parse(""))
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
    assert ATTACHED_REASON in str(err.value)
