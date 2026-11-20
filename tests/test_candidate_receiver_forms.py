"""Candidate guard: the import tables and the module search path are machinery whatever the
receiver expression looks like (not only a bare ``sys`` name)."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

PRE = "import sys, types\nm = types.ModuleType('calc')\n"
SHADOW = (
    "import os, sys, tempfile\nd = tempfile.mkdtemp()\n"
    "with open(os.path.join(d, 'helper_x.py'), 'w') as f:\n    f.write('V = 1\\n')\n"
)

REFUSED = {
    "list_index": PRE + "[sys][0].modules['calc'] = m\n",
    "tuple_update": PRE + "(sys,)[0].modules.update(calc=m)\n",
    "lambda_param": PRE + "(lambda s: s.modules.__setitem__('calc', m))(sys)\n",
    "def_param": PRE + "def _f(s):\n    s.modules['calc'] = m\n_f(sys)\n",
    "class_attr": PRE + "class _C:\n    s = sys\n_C.s.modules['calc'] = m\n",
    "members_dict": PRE + "import inspect\ndict(inspect.getmembers(sys))['modules']['calc'] = m\n",
    "list_path": SHADOW + "[sys][0].path.insert(0, d)\n",
    "def_param_path": SHADOW + "def _g(s):\n    s.path.insert(0, d)\n_g(sys)\n",
    "element_assign": SHADOW + "def _g(s):\n    s.path[0:0] = [d]\n_g(sys)\n",
    "element_set": SHADOW + "def _g(s):\n    s.path[0] = d\n_g(sys)\n",
    "element_del": SHADOW + "def _g(s):\n    del s.path[0]\n_g(sys)\n",
    "stored_alias": SHADOW + "def _g(s):\n    p = s.path\n    p.insert(0, d)\n_g(sys)\n",
    "list_insert_alias": SHADOW + "def _g(s):\n    list.insert(s.path, 0, d)\n_g(sys)\n",
    "name_attr_assign": SHADOW + "def _g(s):\n    s.path = [d]\n_g(sys)\n",
    "name_attr_augassign": SHADOW + "def _g(s):\n    s.path += [d]\n_g(sys)\n",
    "walrus_alias": SHADOW + "def _g(s):\n    (p := s.path).insert(0, d)\n_g(sys)\n",
    "attr_assign_nonname": SHADOW + "[sys][0].path = [d]\n",
}


def _check(repo, source):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\n" + source})
    allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_indirect_receiver_is_refused(repo, name):
    with pytest.raises(CandidateRejected) as got:
        _check(repo, REFUSED[name])
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(
    "source",
    [
        "import os\np = os.path.join('a', 'b')\n",
        "from pathlib import Path\nq = Path('a').parent\n",
        SHADOW + "class _Req:\n    path = ''\nrequest = _Req()\nrequest.path = '/x'\n",
    ],
    ids=["os_path", "pathlib", "request_path_with_write"],
)
def test_unrelated_attributes_are_not_refused(repo, source):
    _check(repo, source)
