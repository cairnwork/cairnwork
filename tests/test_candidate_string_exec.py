"""Candidate guard: a trusted test file that runs code from a string can import anything, so it
fails closed for nested files; plain constant code and unrelated ``compile`` methods do not."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.candidate_shadow import _parse_imports
from cairnwork.workspace import CandidateRejected

TESTS = {
    "exec_import_string": "exec('import helperx')\nmod = 1\n",
    "exec_name_string": "name = 'helperx'\nexec('import ' + name)\nmod = 1\n",
    "exec_dynamic": "import sys\nexec(sys.argv[0])\nmod = 1\n",
    "eval_dynamic": "import sys\neval(sys.argv[0])\nmod = 1\n",
    "compile_dynamic": "import sys\ncode = compile(sys.argv[0], 's', 'exec')\nmod = 1\n",
    "exec_alias": "run = exec\nrun('import helperx')\nmod = 1\n",
    "builtins_exec": "import builtins\nbuiltins.exec('import helperx')\nmod = 1\n",
    "getattr_exec": "import builtins\ngetattr(builtins, 'exec')('import helperx')\nmod = 1\n",
    "exec_in_function": "def go(src):\n    exec(src)\ngo('import helperx')\nmod = 1\n",
    "exec_bytes_import": "exec(b'import helperx')\nmod = 1\n",
}
BENIGN = {
    "constant_eval": "x = eval('1 + 1')\nmod = x\n",
    "constant_exec": "exec('y = 2')\nmod = 1\n",
    "re_compile": "import re\nmod = re.compile('a+')\n",
    "method_named_exec": "class _R:\n    def exec(self):\n        return 1\nmod = _R().exec()\n",
    "no_exec": "import os\nmod = os.sep\n",
}


def _check(repo, files):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, files)
    allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


def _files(repo, test_src):
    commit_change(repo, {"tests/test_h.py": test_src + "def test_h():\n    assert mod\n"})
    return {
        "src/a.py": "import sys\nx = 1\nsys.path.insert(0, 'vendor')\n",
        "vendor/helperx.py": "V = 1\n",
    }


@pytest.mark.parametrize("name", sorted(TESTS))
def test_string_exec_in_a_test_file_is_refused(repo, name):
    with pytest.raises(CandidateRejected):
        _check(repo, _files(repo, TESTS[name]))


@pytest.mark.parametrize("name", sorted(TESTS))
def test_string_exec_adds_a_star_in_a_test_file_only(name):
    src = TESTS[name].encode()
    assert "*" in _parse_imports(src, "tests/test_h.py")
    assert "*" not in _parse_imports(src, "src/a.py")


@pytest.mark.parametrize("name", sorted(BENIGN))
def test_unrelated_code_adds_no_star(name):
    assert "*" not in _parse_imports(BENIGN[name].encode(), "tests/test_h.py")
