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


def test_base_roots_allow_only_names_from_trusted_tree():
    from cairnwork.candidate_recognise import recognised_test_imports
    from cairnwork.candidate_recognise_names import base_roots_from_tree

    roots = base_roots_from_tree(
        (
            (0o100644, "blob", "0" * 40, "pkg/__init__.py"),
            (0o100644, "blob", "0" * 40, "src/libname/tool.py"),
            (0o100644, "blob", "0" * 40, "tests/helpers/util.py"),
            (0o100644, "blob", "0" * 40, "topmod.py"),
        )
    )
    assert roots == {"pkg", "topmod", "src", "tests", "libname", "helpers"}
    new_test = __import__("ast").parse("import newly_added\n")
    assert "*" in recognised_test_imports(new_test, roots)
    assert recognised_test_imports(__import__("ast").parse("import pkg\n"), roots) == {"pkg"}


def test_base_roots_ignore_data_only_and_stdlib_collisions():
    import ast

    from cairnwork.candidate_recognise import recognised_test_imports
    from cairnwork.candidate_recognise_names import base_roots_from_tree

    roots = base_roots_from_tree(
        (
            (0o100644, "blob", "0" * 40, "code/README.md"),
            (0o100644, "blob", "0" * 40, "mylib/data/README.md"),
            (0o100644, "blob", "0" * 40, "mylib/pkg/module.py"),
        )
    )
    assert "code" not in roots
    assert "mylib" in roots
    assert "*" in recognised_test_imports(ast.parse("import code\n"), roots)
    assert recognised_test_imports(ast.parse("import mylib\n"), roots) == {"mylib"}


def test_base_top_level_python_file_cannot_shadow_stdlib_allowlist():
    import ast

    from cairnwork.candidate_recognise import recognised_test_imports
    from cairnwork.candidate_recognise_names import base_roots_from_tree

    roots = base_roots_from_tree(((0o100644, "blob", "0" * 40, "code.py"),))
    assert "code" not in roots
    assert "*" in recognised_test_imports(ast.parse("import code\n"), roots)


@pytest.mark.parametrize(
    "name",
    ["__loader__", "__spec__", "__self__", "__class__", "__globals__", "__dict__"],
)
def test_dangerous_dunder_names_star(name):
    import ast

    from cairnwork.candidate_recognise import recognised_test_imports

    assert "*" in recognised_test_imports(ast.parse(f"value = {name}\n"))


def test_candidate_names_do_not_change_base_roots(repo, monkeypatch):
    from cairnwork import candidate_shadow

    base = commit_change(repo, {"pkg/__init__.py": ""})
    candidate = commit_change(
        repo,
        {
            "newpkg/__init__.py": "",
            "tests/test_base_names.py": "import newpkg\n",
            "pkg.py": "",
        },
    )
    captured = {}
    original = candidate_shadow._parse_imports

    def capture(raw, path, base_roots=frozenset()):
        result = original(raw, path, base_roots)
        captured[path] = (base_roots, result)
        return result

    monkeypatch.setattr(candidate_shadow, "_parse_imports", capture)
    candidate_shadow.find_shadow_file(repo, candidate, base_commit=base, changed={})
    assert captured
    roots, result = captured["tests/test_base_names.py"]
    assert "newpkg" not in roots and "pkg" in roots
    assert roots == candidate_shadow.base_roots_from_tree(
        candidate_shadow._tree_entries(repo, base)
    )
    assert "*" in result


def test_unreadable_base_tree_fails_closed(repo, monkeypatch):
    from cairnwork import candidate_shadow

    commit = commit_change(repo, {"tests/test_untrusted.py": "import unknown_package\n"})
    original = candidate_shadow._tree_entries

    def fail_base(path, tree):
        if tree == "0" * 40:
            raise OSError("unreadable")
        return original(path, tree)

    monkeypatch.setattr(candidate_shadow, "_tree_entries", fail_base)
    parsed = {}

    def capture(raw, path, base_roots=frozenset()):
        parsed[path] = (base_roots, _parse_imports(raw, path, base_roots))
        return parsed[path][1]

    monkeypatch.setattr(candidate_shadow, "_parse_imports", capture)
    candidate_shadow.find_shadow_file(repo, commit, base_commit="0" * 40, changed={})
    roots, names = parsed["tests/test_untrusted.py"]
    assert not roots and "*" in names


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
