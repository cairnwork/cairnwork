"""Docstrings read as data must not be treated as harmless changes."""

from __future__ import annotations

import pytest

from cairnwork.workspace import CandidateRejected
from tests.test_candidate_reach_v2 import BOUND, WRITE, _judge


def _refused(repo, base: str, candidate: str) -> None:
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, {"src/a.py": base}, {"src/a.py": candidate})
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(
    ("base_doc", "candidate_doc", "path_edit"),
    [
        ("", '"""new module text"""\n', "p.insert(0, '/safe' + __doc__)\n"),
        (
            '"""old module text"""\n',
            '"""new module text"""\n',
            "p.insert(0, '/safe' + __doc__)\n",
        ),
        (
            "",
            "",
            "p.insert(0, helper.__doc__)\n",
        ),
    ],
    ids=["add_module_docstring", "replace_module_docstring", "function_docstring"],
)
def test_docstring_read_by_existing_path_edit_is_refused(repo, base_doc, candidate_doc, path_edit):
    function = (
        'def helper():\n    """old function text"""\n    return 1\n'
        if "helper.__doc__" in path_edit
        else ""
    )
    base = base_doc + WRITE + BOUND + function + path_edit
    candidate = candidate_doc + WRITE + BOUND + function + path_edit
    if "helper.__doc__" in path_edit:
        candidate = candidate.replace('"""old function text"""', '"""new function text"""')
    _refused(repo, base, candidate)


def test_docstring_change_without_doc_reference_is_allowed(repo):
    base = '"""old text"""\n' + WRITE + BOUND + "p.insert(0, '/safe')\n"
    candidate = base.replace('"""old text"""', '"""new text"""')
    _judge(repo, {"src/a.py": base}, {"src/a.py": candidate})


def test_docstring_text_mentioning_doc_reference_is_allowed(repo):
    base = '"""old text"""\n' + WRITE + BOUND + "p.insert(0, '/safe')\n"
    candidate = base.replace('"""old text"""', '"""mentions __doc__ as text"""')
    _judge(repo, {"src/a.py": base}, {"src/a.py": candidate})
