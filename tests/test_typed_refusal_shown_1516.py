import pytest

from cairnwork.deferred_overlay import TestCommitRejected as CommitRejected
from cairnwork.pipeline import TaskRejected


@pytest.mark.parametrize("error_type", [TaskRejected, CommitRejected])
@pytest.mark.parametrize(
    "detail, escaped",
    [("a\nb", r"a\x0ab"), ("a\x1b[31mb", r"a\x1b[31mb"), ("a\u202eb", r"a\u202eb")],
)
def test_typed_refusal_shows_hidden_detail_once(error_type, detail, escaped):
    error = error_type("REJECTED", detail)

    assert str(error) == f"REJECTED: {escaped}"


@pytest.mark.parametrize("error_type", [TaskRejected, CommitRejected])
def test_typed_refusal_preserves_ordinary_detail(error_type):
    assert str(error_type("REJECTED", "ordinary path.py")) == "REJECTED: ordinary path.py"
