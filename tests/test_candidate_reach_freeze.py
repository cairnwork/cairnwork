"""A file that binds the module search path by a from-import is frozen as a whole once it also
writes a file: any change to its bytes is refused, comments and blank lines included (#1577)."""

from __future__ import annotations

import pytest

from cairnwork.workspace import CandidateRejected
from tests.test_candidate_reach_v2 import BOUND, WRITE, _judge

PATH_EDIT = "p.insert(0, '/safe')\n"
FROZEN = WRITE + BOUND + PATH_EDIT


def _refused(repo, base: str, candidate: str) -> None:
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, {"src/a.py": base}, {"src/a.py": candidate})
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(
    ("base", "candidate"),
    [
        (FROZEN, "# note\n" + FROZEN),
        (FROZEN, "\n" + FROZEN),
        ('"""old"""\n' + FROZEN, '"""new"""\n' + FROZEN),
        (FROZEN, FROZEN.replace(BOUND, "\n" + BOUND)),
        ("x = 1\n" + FROZEN, FROZEN),
        ("x = 1\ny = 2\n" + FROZEN, "y = 2\nx = 1\n" + FROZEN),
    ],
    ids=[
        "comment_added",
        "blank_added",
        "module_docstring_changed",
        "blank_between_statements",
        "statement_deleted",
        "statements_swapped",
    ],
)
def test_any_byte_change_to_a_frozen_file_is_refused(repo, base, candidate):
    _refused(repo, base, candidate)


@pytest.mark.parametrize(
    ("base", "candidate"),
    [
        ("x = 1\n" + BOUND + PATH_EDIT, "# note\nx = 1\n" + BOUND + PATH_EDIT),
        ("x = 1\n" + WRITE + "p = 1\n", "# note\nx = 1\n" + WRITE + "p = 1\n"),
    ],
    ids=["binding_without_write", "write_without_binding"],
)
def test_files_outside_the_frozen_shape_are_allowed(repo, base, candidate):
    _judge(repo, {"src/a.py": base}, {"src/a.py": candidate})


def test_byte_identical_frozen_file_is_allowed(repo):
    _judge(
        repo,
        {"src/a.py": FROZEN, "src/b.py": "y = 1\n"},
        {"src/a.py": FROZEN, "src/b.py": "y = 2\n"},
    )


def test_new_file_with_write_and_no_binding_is_allowed(repo):
    _judge(repo, None, {"src/a.py": WRITE + "x = 1\n"})


def test_old_frozen_file_with_bound_line_removed_is_refused(repo):
    base = WRITE + BOUND + PATH_EDIT
    candidate = "\n" + WRITE + PATH_EDIT
    _refused(repo, base, candidate)


def test_frozen_shape_must_hold_in_one_version(repo):
    # old only binds, new only writes: neither version has both signals, so no freeze applies
    _judge(repo, {"src/a.py": BOUND + PATH_EDIT}, {"src/a.py": WRITE})


def test_write_removed_from_a_frozen_file_is_refused(repo):
    _refused(repo, FROZEN, BOUND + PATH_EDIT)
