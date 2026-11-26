"""#1813: the exception types that still put raw text in their message show hidden characters
escaped, like TaskRejected does; ordinary ASCII and CJK text stays unchanged."""

import pytest

from cairnwork.changeset import ChangeSetError
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.intake_request import IntakeRefused
from cairnwork.verifier_spec import VerifierRejected
from cairnwork.workspace import WorkspaceError

MAKERS = {
    "workspace": lambda d: (WorkspaceError(d), "WORKSPACE_ERROR: "),
    "changeset": lambda d: (ChangeSetError(d), "CHANGE_SET_ERROR: "),
    "verifier": lambda d: (VerifierRejected(d), "VERIFIER_NOT_ALLOWED: "),
    "intake": lambda d: (IntakeRefused("INTAKE_BAD", d), "INTAKE_BAD: "),
    "uncomparable": lambda d: (
        BaseUncomparable(Comparison("UNCOMPARABLE", "BASE_X", d)),
        "BASE_X: ",
    ),
}
HOSTILE = [  # control, line and paragraph separators, bidi, a workflow command after a newline
    ("a\nb", "a\\x0ab"),
    ("a\rb", "a\\x0db"),
    ("a\x1b[31mb", "a\\x1b[31mb"),
    ("a\u2028b", "a\\u2028b"),
    ("a\u2029b", "a\\u2029b"),
    ("a\u202eb", "a\\u202eb"),
    ("a\u2066b", "a\\u2066b"),
    ("a\U000e0041b", "a\\U000e0041b"),  # a tag character above U+FFFF
    ("a\n::error::x", "a\\x0a::error::x"),
    ("a\n##[group]x", "a\\x0a##[group]x"),
]


@pytest.mark.parametrize("kind", MAKERS)
@pytest.mark.parametrize("detail, escaped", HOSTILE)
def test_hidden_characters_are_escaped_in_the_message(kind, detail, escaped):
    error, head = MAKERS[kind](detail)
    assert str(error) == head + escaped
    assert len(str(error).splitlines()) == 1


@pytest.mark.parametrize("kind", MAKERS)
@pytest.mark.parametrize("detail", ["ordinary path/x.py: 1 != 2", "測試失敗：路徑 a/b", ""])
def test_ordinary_text_is_unchanged(kind, detail):
    error, head = MAKERS[kind](detail)
    assert str(error) == head + detail


@pytest.mark.parametrize("isolated", [True, False])
def test_a_comparisons_worker_reason_escapes_its_detail(isolated):
    reason = Comparison("UNCOMPARABLE", "BASE_X", "a\n::error::x\u202e").worker_reason(
        isolated=isolated
    )
    assert reason.endswith("BASE_X: a\\x0a::error::x\\u202e")
    assert len(reason.splitlines()) == 1
