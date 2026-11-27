"""#1878 on the cairnwork-verify path: a changed test Cairnwork cannot read statically (a class
built by an imported helper, or by ``type`` under another name) is not covered by a node target;
the refusal held back until P2P has planned must still be raised (verify_candidate)."""

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from verify_helpers import FIX, candidate, verify

sandbox = pytest.mark.sandbox
TEST = "tests/test_regression.py"
HEAD = "import unittest\nfrom calc import add\n"
HELPERS = (
    "import unittest\n\n\ndef make(t):\n"
    "    return type('Hid', (unittest.TestCase,), {'test_h': t})\n"
)
VISIBLE = "def test_regression():\n    assert add(1, 1) == {n}\n\n\n"
HIDDEN = "def _t(self):\n    assert add(1, 1) == {n}\n\n\n"
SHAPES = {
    "helper": "from helpers import make\n\nHid = make(_t)\n",
    "type_alias": "mk = type\nHid = mk('Hid', (unittest.TestCase,), {'test_h': _t})\n",
}
SAME = "add(1, 1)"


def _text(shape, n_visible, n_hidden):
    return HEAD + VISIBLE.format(n=n_visible) + HIDDEN.format(n=n_hidden) + SHAPES[shape]


def _run(project, state, shape, n_hidden, target):
    commit_change(project, {TEST: _text(shape, SAME, SAME), "tests/helpers.py": HELPERS})
    base, test_commit = make_test_commit(project, {TEST: _text(shape, 2, n_hidden)})
    cand = candidate(project, base, {"src/calc.py": FIX})
    return verify(state, project, base, cand, "--test-commit", test_commit,
                  tests=(f"python3 -m pytest -q {target}",))  # fmt: skip


@sandbox
@pytest.mark.parametrize("shape", SHAPES)
def test_verify_path_node_target_cannot_hide_a_changed_test(project, state, shape):
    code, out = _run(project, state, shape, 99, f"{TEST}::test_regression")
    assert code != 0 and out["status"] == "REFUSED", out
    assert out["reason"].startswith("DEFERRED_TESTS_NOT_TARGETED"), out


@sandbox
@pytest.mark.parametrize("shape", SHAPES)
def test_verify_path_whole_file_target_verifies(project, state, shape):
    code, out = _run(project, state, shape, 2, TEST)
    assert code == 0 and out["status"] == "VERIFIED", out
