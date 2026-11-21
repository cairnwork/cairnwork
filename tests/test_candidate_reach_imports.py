"""Candidate guard: a name bound to the module search path by a from-import is the search path,
whatever it is called locally. The judgement reuses the receiver rules of the dotted form."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

SHADOW = (
    "import os, tempfile\nd = tempfile.mkdtemp()\n"
    "with open(os.path.join(d, 'helper_x.py'), 'w') as f:\n    f.write('V = 1\\n')\n"
)


def _judge(repo, base_files, files):
    if base_files:
        commit_change(repo, base_files)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, files)
    allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


REFUSED = {
    "alias_insert": "from sys import path as p\np.insert(0, d)\n",
    "bare_name_insert": "from sys import path\npath.insert(0, d)\n",
    "alias_append": "from sys import path as p\np.append(d)\n",
    "alias_slice": "from sys import path as p\np[0:0] = [d]\n",
    "alias_element": "from sys import path as p\np[0] = d\n",
    "alias_del": "from sys import path as p\ndel p[0]\n",
    "alias_in_def": "from sys import path as p\ndef _g():\n    p.insert(0, d)\n_g()\n",
    "alias_stored": "from sys import path as p\nq = p\nq.insert(0, d)\n",
    "alias_list_call": "from sys import path as p\nlist.insert(p, 0, d)\n",
    "alias_next_to_other": "from sys import argv, path as p\np.insert(0, d)\n",
    "star_import": "from sys import *\npath.insert(0, d)\n",
    "alias_sort": "from sys import path as p\np.sort()\n",
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_search_path_alias_is_refused(repo, name):
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, None, {"src/a.py": "x = 1\n" + SHADOW + REFUSED[name]})
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


def test_edit_added_next_to_an_existing_import_is_refused(repo):
    base = "x = 1\n" + SHADOW + "from sys import path as p\nprint(len(p))\n"
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, {"src/a.py": base}, {"src/a.py": base + "p.insert(0, d)\n"})
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(
    "source",
    [
        "from sys import argv, exit\nn = len(argv)\n",
        "from os import path\nq = path.join('a', 'b')\nok = path.exists(q)\n",
        "from os import path as p\np.insert = 1\n",
        "import sys\np = [1]\np.insert(0, 2)\n",
        "path = [1]\npath.append(2)\n",
    ],
    ids=["sys_other_names", "os_path_import", "os_path_alias", "local_list", "local_name_path"],
)
def test_other_names_are_not_refused(repo, source):
    _judge(repo, None, {"src/a.py": "x = 1\n" + SHADOW + "# sys.path\n" + source})
