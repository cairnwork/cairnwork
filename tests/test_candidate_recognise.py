"""Candidate guard: unfamiliar test code fails closed for nested imports."""

from __future__ import annotations

import sys

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.candidate_shadow import _parse_imports
from cairnwork.workspace import CandidateRejected

MUST_STAR = {
    "pickle": "import pickle\n",
    "marshal": "import marshal\n",
    "importlib": "import importlib\n",
    "runpy": "import runpy\n",
    "timeit": "import timeit\n",
    "pdb": "import pdb\n",
    "code": "import code\n",
    "cProfile": "import cProfile\n",
    "profile": "import profile\n",
    "builtins_alias": "import builtins as b\nb.exec('pass')\n",
    "builtins_exec_value": "import builtins\nrunner = builtins.exec\n",
    "builtins_getattr_value": "import builtins\nlookup = builtins.getattr\n",
    "builtins_from": "from builtins import exec as run\nrun('pass')\n",
    "exec_unparsable": "exec('import (')\n",
    "exec_dynamic": "exec(source)\n",
    "compile_dynamic": "compile(source, '<test>', 'exec')\n",
    "getattr_as_value": "lookup = getattr\n",
    "print_self": "x = print.__self__\n",
    "sys_modules": "import sys\nx = sys.modules\n",
    "os_sys_modules": "import os\nx = os.sys.modules\n",
    "typing_hints": "import typing\ntyping.get_type_hints(object)\n",
    "types_code": "import types\nx = types.CodeType\n",
    "sqlite_extension": "import sqlite3\nsqlite3.enable_load_extension(True)\n",
    "formatter_field": "import string\nstring.Formatter().get_field('x', (), {})\n",
    "getattr_dynamic": "x = getattr(object, unknown)\n",
    "attrgetter": "import operator\noperator.attrgetter('x')\n",
    "monkeypatch_dynamic": "monkeypatch.setattr(target, 1)\n",
    "patch_import": "from unittest.mock import patch\npatch('builtins.__import__')\n",
    "loader_dynamic": "import unittest\nunittest.TestLoader().loadTestsFromName(name)\n",
}

CONSTANT_TARGETS = {
    "monkeypatch": "monkeypatch.setattr('helperx.sub.fn', 1)\n",
    "importorskip": "pytest.importorskip('helperx.sub')\n",
    "mock_patch": "from unittest.mock import patch\npatch('helperx.sub.fn')\n",
    "loader": "import unittest\nunittest.TestLoader().loadTestsFromName('helperx.sub.fn')\n",
}

BENIGN = {
    "re_compile": "import re\nre.compile('x')\n",
    "method_exec": "thing.exec()\n",
    "getattr_constant": "getattr(object, 'harmless')\n",
    "plain": "import os\nos.sep\n",
}


@pytest.mark.parametrize("source", MUST_STAR.values(), ids=MUST_STAR.keys())
def test_unrecognised_test_code_stars(source):
    assert "*" in _parse_imports(source.encode(), "tests/test_h.py")


@pytest.mark.parametrize("source", CONSTANT_TARGETS.values(), ids=CONSTANT_TARGETS.keys())
def test_constant_targets_add_dotted_prefixes(source):
    names = _parse_imports(source.encode(), "tests/test_h.py")
    assert {"helperx", "helperx.sub"} <= names
    assert "*" not in names


@pytest.mark.parametrize("source", BENIGN.values(), ids=BENIGN.keys())
def test_recognised_benign_forms_stay_precise(source):
    assert "*" not in _parse_imports(source.encode(), "tests/test_h.py")


def test_allowlist_names_are_known_modules():
    from cairnwork import candidate_recognise

    roots = candidate_recognise.ALLOWED_TOP_LEVEL
    tests_dir = __import__("pathlib").Path("tests")
    repo_modules = {path.stem for path in tests_dir.glob("*.py")}
    repo_modules |= {path.name for path in tests_dir.iterdir() if path.is_dir()}
    assert roots <= sys.stdlib_module_names | repo_modules | {
        "cairnwork",
        "cairnwork_attest",
        "cairnwork_protocol",
        "cairnwork_sandbox",
        "cairnwork_quickstart",
        "experimental",
        "tests",
        "conftest",
        "scripts",
        "tools",
        "product",
        "pytest",
        "hypothesis",
        "cryptography",
    }


def test_non_test_paths_never_star():
    assert "*" not in _parse_imports(b"import pickle\nexec(source)\n", "src/example.py")


def test_unrecognised_import_stars_a_nested_candidate_file(repo):
    commit_change(repo, {"tests/test_h.py": "import pickle\n"})
    base = git(repo, "rev-parse", "HEAD")
    candidate = commit_change(
        repo,
        {
            "src/a.py": "import sys\nsys.path.insert(0, 'vendor')\n",
            "vendor/pickle.py": "VALUE = 1\n",
        },
    )
    changed = tuple(git(repo, "diff", "--name-only", base, candidate).split())
    with pytest.raises(CandidateRejected):
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, candidate, changed)
