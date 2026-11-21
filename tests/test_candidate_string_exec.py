"""Candidate guard: a trusted test file that runs code from a string can import anything, so it
fails closed for nested files; plain constant code and unrelated ``compile`` methods do not."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.candidate_shadow import _parse_imports
from cairnwork.workspace import CandidateRejected

TESTS = {
    "exec_name_string": "name = 'helperx'\nexec('import ' + name)\nmod = 1\n",
    "exec_dynamic": "import sys\nexec(sys.argv[0])\nmod = 1\n",
    "eval_dynamic": "import sys\neval(sys.argv[0])\nmod = 1\n",
    "compile_dynamic": "import sys\ncode = compile(sys.argv[0], 's', 'exec')\nmod = 1\n",
    "exec_alias": "run = exec\nrun('import helperx')\nmod = 1\n",
    "builtins_exec": "import builtins\nbuiltins.exec('import helperx')\nmod = 1\n",
    "getattr_exec": "import builtins\ngetattr(builtins, 'exec')('import helperx')\nmod = 1\n",
    "exec_in_function": "def go(src):\n    exec(src)\ngo('import helperx')\nmod = 1\n",
    "namespace_globals": "g = globals()\nmod = g\n",
    "namespace_vars": "mod = vars()\n",
    "namespace_locals": "mod = locals()\n",
    "namespace_dict": "import os\nmod = os.__dict__\n",
    "dunder_builtins": "mod = __builtins__\n",
    "builtins_alias_exec": "import builtins as b\nb.exec('import helperx')\nmod = 1\n",
    "builtins_from_alias": "from builtins import exec as e\ne('import helperx')\nmod = 1\n",
    "builtins_vars_lookup": "import builtins\nvars(builtins)['exec']('import helperx')\nmod = 1\n",
    "builtins_module": "import builtins\nmod = builtins\n",
    "getattr_dynamic_name": "import os\nn = 'sep'\nmod = getattr(os, n)\n",
    "const_unparsable": "exec('import (')\nmod = 1\n",
    "const_nested_dynamic": "exec('import sys\\nexec(sys.argv[0])')\nmod = 1\n",
    "const_alias_import": (
        "exec('from importlib import import_module as go\\ngo(\"helperx\")')\nmod = 1\n"
    ),
    "module_timeit": "import timeit\nmod = timeit\n",
    "module_timeit_use": "import timeit\nmod = timeit.timeit('import helperx', number=1)\n",
    "module_pdb": "import pdb\nmod = pdb\n",
    "module_code": "import code\nmod = code\n",
    "module_cprofile": "import cProfile\nmod = cProfile\n",
    "module_profile_from": "from profile import run\nmod = run\n",
    "module_runpy_from": "from runpy import run_path\nmod = run_path\n",
}
# A constant source adds what it imports, like a direct import, instead of a star.
CONST = {
    "exec_import_string": "exec('import helperx')\nmod = 1\n",
    "exec_bytes_import": "exec(b'import helperx')\nmod = 1\n",
    "eval_expression_import": "eval('__import__(\"helperx\")')\nmod = 1\n",
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


@pytest.mark.parametrize("name", sorted(CONST))
def test_constant_source_adds_what_it_imports(repo, name):
    assert "helperx" in _parse_imports(CONST[name].encode(), "tests/test_h.py")
    with pytest.raises(CandidateRejected):
        _check(repo, _files(repo, CONST[name]))
