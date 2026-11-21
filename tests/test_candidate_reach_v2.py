"""Candidate guard: a file that binds the module search path by a from-import is judged as a
whole. Once it also writes a file, any statement the candidate adds is a possible path edit."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

WRITE = (
    "import os, tempfile\nd = tempfile.mkdtemp()\n"
    "with open(os.path.join(d, 'helper_x.py'), 'w') as f:\n    f.write('V = 1\\n')\n"
)
BOUND = "from sys import path as p\n"


def _judge(repo, base_files, files):
    if base_files:
        commit_change(repo, base_files)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, files)
    allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


def _refused(repo, base_files, files):
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, base_files, files)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


BASE = "x = 1\n" + WRITE + BOUND + "print(len(p))\n"

ADDED = {
    "namespace_lookup": "globals()['p'].insert(0, d)\n",
    "other_object_attribute": "class _C:\n    s = p\n_C.s.insert(0, d)\n",
    "list_wrapped": "[p][0].insert(0, d)\n",
    "plain_call": "p.insert(0, d)\n",
    "unrelated_looking": "y = 2\n",
    "import_other": "import json\n",
    "constant_value": "z = '/tmp/elsewhere'\n",
}


@pytest.mark.parametrize("added", list(ADDED.values()), ids=list(ADDED))
def test_any_added_statement_in_a_binding_file_with_a_write_is_refused(repo, added):
    _refused(repo, {"src/a.py": BASE}, {"src/a.py": BASE + added})


def test_swapping_a_constant_read_by_an_existing_edit_is_refused(repo):
    base = "x = 1\n" + WRITE + BOUND + "D = '/safe'\np.insert(0, D)\n"
    cand = base.replace("'/safe'", "'/other'")
    _refused(repo, {"src/a.py": base}, {"src/a.py": cand})


def test_new_file_with_binding_and_write_is_refused(repo):
    _refused(repo, None, {"src/a.py": BASE + "p.insert(0, d)\n"})


def test_star_binding_with_a_write_refuses_added_statements(repo):
    base = "x = 1\n" + WRITE + "from sys import *\n"
    _refused(repo, {"src/a.py": base}, {"src/a.py": base + "y = 2\n"})


@pytest.mark.parametrize(
    "files",
    [
        {
            "src/reexp.py": BOUND,
            "src/a.py": "from reexp import p\n" + WRITE + "p.insert(0, d)\n",
        },
        pytest.param(
            {
                "src/hs.py": "import sys as s\n",
                "src/a.py": "from hs import s\n" + WRITE + "s.path.insert(0, d)\n",
            },
            marks=pytest.mark.xfail(strict=True, reason="no sys signal in the file (#1577)"),
        ),
        {
            "src/w.py": WRITE,
            "src/a.py": "from w import d\n" + BOUND + "p.insert(0, d)\n",
        },
    ],
    ids=["reexported_binding", "sys_through_other_module", "write_in_other_file"],
)
def test_splits_that_do_not_start_the_rule_are_left_to_the_existing_checks(repo, files):
    _refused(repo, None, files)


@pytest.mark.parametrize(
    ("base", "added"),
    [
        (BASE, "# a comment\n"),
        (BASE, "\n\n"),
        ("x = 1\n" + WRITE, "y = 2\nimport json\n"),
        ("x = 1\n" + BOUND, "y = 2\nimport json\n"),
        ("x = 1\n" + WRITE + "import sys\n", "y = 2\n"),
    ],
    ids=["comment", "blank", "write_no_binding", "binding_no_write", "sys_no_binding"],
)
def test_files_the_rule_does_not_start_on_are_unchanged(repo, base, added):
    _judge(repo, {"src/a.py": base}, {"src/a.py": base + added})


def test_a_docstring_added_to_a_binding_file_is_allowed(repo):
    base = '"""old"""\n' + BASE
    _judge(repo, {"src/a.py": base}, {"src/a.py": base.replace('"""old"""', '"""new"""')})
