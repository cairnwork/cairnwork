"""#1235: independent pins for deferred attached-test AST cases."""

import pytest
from conftest import commit_change
from test_attached_deferred_tests import FN_IMPL, F, Rejected, _new_tests
from test_deferred_tests import REGRESSION

ATTACH = "def attach(f):\n    globals()['test_x'] = f\n    return f\n"

ANNOTATIONS = {
    "variadic": "def _h(*a: attach(_impl)):\n    pass\n",
    "keyword-variadic": "def _h(**k: attach(_impl)):\n    pass\n",
    "positional-only": "def _h(p: attach(_impl), /):\n    pass\n",
    "keyword-only": "def _h(*, k: attach(_impl)):\n    pass\n",
}

DUnder = {
    "self": "test_x = ().count.__self__.index(0)\n",
    "builtins": "test_x = (lambda: 0).__builtins__.get('eval')\n",
    "globals": "test_x = (lambda: 0).__globals__.get('_impl')\n",
    "dict": "test_x = (0).__class__.__dict__.get('real')\n",
}

ATTACHED_CASES = {
    **{f"annotation-{shape}": ATTACH + tail for shape, tail in ANNOTATIONS.items()},
    **{f"dunder-{shape}": tail for shape, tail in DUnder.items()},
    "comprehension-iter-walrus": "[0 for _ in [(test_x := _impl)]]\n",
    "match-star-capture": "match [_impl]:\n    case [*test_x]:\n        pass\n",
    "match-mapping-rest": "match {'x': _impl}:\n    case {**test_x}:\n        pass\n",
}


@pytest.mark.parametrize("shape", sorted(ATTACHED_CASES))
def test_deferred_attached_ast_case_is_refused(project, shape):
    body = FN_IMPL.format("    pass\n") + ATTACHED_CASES[shape]
    commit_change(project, {F: "from calc import add\n" + body})
    with pytest.raises(Rejected) as err:
        _new_tests(project, {F: REGRESSION + FN_IMPL.format("    assert False\n") + body})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
