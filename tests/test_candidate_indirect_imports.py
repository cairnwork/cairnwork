"""Candidate guard: code-object and pickle loading count as import machinery, and a trusted test
file that reaches an import by an indirect route fails closed for nested files."""

from __future__ import annotations

import base64
import pickle

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

_PAYLOAD = base64.b64encode(pickle.dumps(1)).decode()
MACHINERY = {
    "code_object": (
        "import base64, marshal, types\n"
        "types.FunctionType(marshal.loads(base64.b64decode('AA==')), {})()\n"
    ),
    "pickle_load": f"import base64, pickle\npickle.loads(base64.b64decode('{_PAYLOAD}'))\n",
}
TESTS = {
    "getattr_value": "import importlib\nmod = getattr(importlib, 'import_module')('helperx')\n",
    "list_wrapped": "import importlib\nmod = [importlib.import_module][0]('helperx')\n",
    "runpy": "import runpy\nmod = runpy.run_module('helperx')\n",
    "pkgutil": "import pkgutil\nmod = pkgutil.resolve_name('helperx')\n",
    "renamed_import": "from importlib import import_module as load\nmod = load('helperx')\n",
    "renamed_route": "from runpy import run_module as go\nmod = go('helperx')\n",
    "renamed_resolver": "from pkgutil import resolve_name as pick\nmod = pick('helperx')\n",
}


def _check(repo, files):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, files)
    allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


@pytest.mark.parametrize("name", sorted(MACHINERY))
def test_code_loading_words_are_machinery(repo, name):
    with pytest.raises(CandidateRejected):
        _check(repo, {"src/a.py": "x = 1\n" + MACHINERY[name]})


def _with_test(repo, test_src):
    commit_change(repo, {"tests/test_h.py": test_src + "def test_h():\n    assert mod\n"})
    return {
        "src/a.py": "import sys\nx = 1\nsys.path.insert(0, 'vendor')\n",
        "vendor/helperx.py": "V = 1\n",
    }


@pytest.mark.parametrize("name", sorted(TESTS))
def test_indirect_import_in_a_test_file_is_refused(repo, name):
    files = _with_test(repo, TESTS[name])
    with pytest.raises(CandidateRejected):
        _check(repo, files)


def test_plain_getattr_in_a_test_file_adds_no_star(repo):
    from cairnwork.candidate_shadow import _parse_imports

    plain = b"import os\nmod = getattr(os, 'sep')\nxs = [1]\n"
    assert "*" not in _parse_imports(plain, "tests/test_h.py")
    assert "*" in _parse_imports(b"import runpy\nrunpy.run_module('a')\n", "tests/test_h.py")
    assert "*" not in _parse_imports(b"import runpy\nrunpy.run_module('a')\n", "src/a.py")


def test_importlib_import_is_starred_even_with_constant_name():
    from cairnwork.candidate_shadow import _parse_imports

    src = b"from importlib import import_module\nmod = import_module('helperx')\n"
    assert "*" in _parse_imports(src, "tests/test_h.py")


EDITS = {
    "from_sys_path": "from sys import path\nx = 1\npath.insert(0, 'vendor')\n",
    "from_sys_path_alias": "from sys import path as p\nx = 1\np.append('vendor')\n",
}


@pytest.mark.parametrize("name", sorted(EDITS))
def test_from_import_of_the_search_path_then_edit_is_refused(repo, name):
    files = _with_test(repo, "import helperx\nmod = helperx\n")
    files["src/a.py"] = EDITS[name]
    with pytest.raises(CandidateRejected):
        _check(repo, files)
