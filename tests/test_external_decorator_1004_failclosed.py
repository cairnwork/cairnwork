"""#1004: a stdlib decorator stops counting as a fixture factory only in one tight shape."""

import ast
import itertools

import pytest
from test_conftest_names import _check
from test_fixture_forms_1004_residual_cases import NESTED, _base

import cairnwork.deferred_tests as dt
from cairnwork.conftest_names import _fixtures
from cairnwork.fixture_forwarding import external_roots, voided_modules

CAND = {NESTED: "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n"}
FACTORY = {"tests/factory.py": "def register(fn):\n    return fn\n"}


def _blocked(repo, helper, extra=None, candidate=None):
    base = {**_base(helper), **(extra or {})}
    try:
        _check(repo, candidate or CAND, path=NESTED, base_files=base)
    except dt.TestCommitRejected as exc:
        assert exc.code == "TEST_COMMIT_CONFTEST_STATIC"
        return "dep" in str(exc)
    return False


def _fn(deco: str, head: str = "import functools") -> str:
    return f"{head}\n\n\n{deco}\ndef helper(dep):\n    return dep\n"


def _counts(src: str, voided: set[str] | None = None) -> bool:
    return "dep" in _fixtures(ast.parse(src), voided=voided)


PASS = [
    _fn("@functools.lru_cache"),
    _fn("@functools.lru_cache(maxsize=1)"),
    _fn("@lru_cache", "from functools import lru_cache"),
    _fn("@ft.lru_cache", "import functools as ft"),
    _fn("@contextmanager", "from contextlib import contextmanager"),
    _fn("@functools.partial(functools.lru_cache, maxsize=1)"),
    _fn("@lru_cache", "from factory import *\nfrom functools import lru_cache"),
]


@pytest.mark.parametrize("src", PASS)
def test_the_tight_stdlib_shape_does_not_count(src):
    assert not _counts(src)


@pytest.mark.parametrize("src", PASS[:2])
def test_the_harness_lets_the_candidate_through(repo, src):
    assert not _blocked(repo, src)


IN_TREE = {
    "factory": (_fn("@factory.register", "import factory"), FACTORY),
    "unresolved": (_fn("@missing", "import functools"), {}),
    "relative": (_fn("@functools.lru_cache", "from . import functools"), FACTORY),
    "shadow_module": (
        _fn("@functools.lru_cache"),
        {"tests/functools.py": "def lru_cache(fn):\n    return fn\n"},
    ),
    "namespace_dir": (_fn("@json.dumps", "import json"), {"tests/json/leaf.py": "X = 1\n"}),
    "package": (_fn("@json.dumps", "import json"), {"tests/json/__init__.py": ""}),
}


@pytest.mark.parametrize("name", IN_TREE)
def test_in_tree_unresolved_and_shadowed_decorators_count(repo, name):
    helper, extra = IN_TREE[name]
    assert _blocked(repo, helper, extra), name


def test_the_index_check_sees_namespace_directories_unit():
    src = _fn("@json.dumps", "import json")
    index = {"json.leaf": {"tests/json/leaf.py"}, "leaf": {"tests/json/leaf.py"}}
    assert "json" not in external_roots(ast.parse(src), index)
    assert "json" in external_roots(ast.parse(src), {})


BLOCK = {
    "third_party": _fn("@notinstalled_zzz.deco", "import notinstalled_zzz"),
    "pytest_root": _fn("@pytest.fixture", "import pytest"),
    "pytest_factoryboy": _fn("@pytest_factoryboy.register", "import pytest_factoryboy"),
    "pytest_alias": _fn("@f", "from pytest import fixture as f"),
    "relative": _fn("@functools.lru_cache", "from . import functools"),
    "try_import": _fn(
        "@functools.lru_cache",
        "try:\n    import functools\nexcept ImportError:\n    import f2 as functools",
    ),
    "if_import": _fn("@functools.lru_cache", "if True:\n    import functools"),
    "nested_import": _fn(
        "@functools.lru_cache", "import functools\n\n\ndef g():\n    import factory as functools"
    ),
    "def_rebind": _fn("@functools.lru_cache", "import functools\n\n\ndef functools():\n    pass"),
    "alias_assign": _fn("@functools.lru_cache", "import functools\nfunctools = factory"),
    "partial_factory": _fn("@functools.partial(factory.register)", "import functools, factory"),
    "partial_mixed": _fn(
        "@functools.partial(functools.lru_cache, factory.register)", "import functools, factory"
    ),
    "attr_store": _fn(
        "@functools.lru_cache", "import functools\nfunctools.lru_cache = factory.register"
    ),
    "star_after": _fn("@lru_cache", "from functools import lru_cache\nfrom factory import *"),
    "setattr": _fn(
        "@functools.lru_cache",
        "import functools\nsetattr(functools, 'lru_cache', factory.register)",
    ),
    "dict": _fn("@functools.lru_cache", "import functools\nfunctools.__dict__['lru_cache'] = 1"),
    "sys_modules": _fn(
        "@functools.lru_cache", "import functools, sys\nsys.modules['functools'] = factory"
    ),
    "from_sys_modules": _fn("@functools.lru_cache", "import functools\nfrom sys import modules"),
    "computed": _fn("@getattr(__import__('func' + 'tools'), 'lru_cache')", "import functools"),
    "computed_setattr": _fn(
        "@functools.lru_cache",
        "import functools\nsetattr(functools, 'lru_' + 'cache', factory.register)",
    ),
    "call_arg": _fn("@functools.lru_cache", "import functools\npatch(functools)"),
    "arg_of_other_decorator": _fn(
        "@factory.register(functools)\n@functools.lru_cache", "import functools, factory"
    ),
    "second_decorator_unknown": _fn("@functools.lru_cache\n@missing"),
    "walrus": _fn("@functools.lru_cache", "import functools\n(functools := factory)"),
    "twice_imported": _fn("@functools.lru_cache", "import functools\nimport factory as functools"),
    "importlib": _fn(
        "@functools.lru_cache",
        "import functools\nfrom importlib import import_module\n"
        "import_module('functools').lru_cache = factory.register",
    ),
    "builtins_import": _fn("@functools.lru_cache", "import functools\nfrom builtins import object"),
    "sys_alias_modules": _fn(
        "@functools.lru_cache",
        "import sys as s, factory\ns.modules['functools'] = factory\nimport functools",
    ),
    "mock_patch_string": _fn(
        "@functools.lru_cache",
        "import functools\nfrom unittest import mock\n"
        "mock.patch('functools.lru_cache', factory.register).start()",
    ),
    "runpy": _fn(
        "@functools.lru_cache", "import functools, runpy\nrunpy.run_path('tests/patch.py')"
    ),
    "string_names_stdlib": _fn(
        "@functools.lru_cache", "import functools\nNAME = 'functools.lru_cache'"
    ),
    "same_statement_alias": _fn("@f.lru_cache", "import functools as f, factory as f"),
    "sys_alias_modules_computed": _fn(
        "@functools.lru_cache",
        "import functools, sys as s, factory\nk = 'a' + 'b'\ns.modules[k] = factory",
    ),
    **{
        f"non_allowlisted_{m}": _fn("@functools.lru_cache", f"import functools\nimport {m}")
        for m in "pydoc timeit trace pdb doctest cProfile zipapp site sched os subprocess".split()
    },
    "from_non_allowlisted": _fn(
        "@functools.lru_cache", "import functools\nfrom pydoc import locate"
    ),
    "call_on_call": _fn("@functools.partial(functools.lru_cache)(1)", "import functools"),
    "expr_arg": _fn("@functools.lru_cache(maxsize=1 + x)", "import functools"),
}


@pytest.mark.parametrize("name", BLOCK)
def test_everything_else_counts_as_on_main(name):
    assert _counts(BLOCK[name]), name


BINDINGS = {
    "assign": "functools = 1",
    "augassign": "functools += 1",
    "annassign": "functools: int = 1",
    "namedexpr": "(functools := 1)",
    "for": "for functools in []:\n    pass",
    "asyncfor": "async def g():\n    async for functools in x:\n        pass",
    "with": "with x as functools:\n    pass",
    "asyncwith": "async def g():\n    async with x as functools:\n        pass",
    "except": "try:\n    pass\nexcept E as functools:\n    pass",
    "exceptstar": "try:\n    pass\nexcept* E as functools:\n    pass",
    "matchas": "match x:\n    case 1 as functools:\n        pass",
    "matchstar": "match x:\n    case [*functools]:\n        pass",
    "matchmapping": "match x:\n    case {**functools}:\n        pass",
    "def": "def functools():\n    pass",
    "asyncdef": "async def functools():\n    pass",
    "class": "class functools:\n    pass",
    "arg": "def g(functools):\n    pass",
    "lambda": "g = lambda functools: 1",
    "comprehension": "g = [1 for functools in []]",
    "global": "def g():\n    global functools",
    "nonlocal": "def g():\n    functools = 1\n\n    def h():\n        nonlocal functools",
    "delete": "del functools",
    "import_in_def": "def g():\n    import factory as functools",
    "from_in_def": "def g():\n    from factory import x as functools",
    "typealias": "type functools = int",
    "typeparam": "def g[functools]():\n    pass",
    "typeparam_class": "class C[functools]:\n    pass",
    "paramspec": "def g[**functools]():\n    pass",
    "typevartuple": "def g[*functools]():\n    pass",
}


@pytest.mark.parametrize("name", BINDINGS)
def test_any_other_binding_of_the_root_name_counts(name):
    src = _fn("@functools.lru_cache", "import functools\n" + BINDINGS[name])
    ast.parse(src)
    assert _counts(src), name


def test_the_binding_table_has_no_false_alarm():
    assert not _counts(_fn("@functools.lru_cache", "import functools\nother = 1"))


def test_a_star_import_before_the_import_is_harmless():
    assert not _counts(PASS[-1])


def test_a_second_pytest_plugins_module_that_patches_voids_the_module(repo):
    patcher = "import functools\nfunctools.lru_cache = factory.register\n"
    extra = {**FACTORY, "tests/patch.py": patcher}
    base = _base(_fn("@functools.lru_cache"))
    base["tests/conftest.py"] = base["tests/conftest.py"].replace(
        "['helpers']", "['helpers', 'patch']"
    )
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(repo, CAND, path=NESTED, base_files={**base, **extra})
    assert "dep" in str(refused.value)


def test_store_through_a_non_stdlib_chain_voids_every_module():
    helper = ast.parse(
        "import functools\n\n\n@functools.lru_cache\ndef helper(dep):\n    return dep"
    )
    other = ast.parse("import factory\nfactory.functools.lru_cache = factory.register")
    voided = voided_modules({"h": helper, "o": other})
    assert "*" in voided
    assert _counts(ast.unparse(helper), voided=voided)


def test_exec_capable_import_or_stdlib_string_in_any_tree_file_voids_everything():
    assert voided_modules({"o": ast.parse("import runpy\nrunpy.run_path('p')")}) == {"*"}
    assert voided_modules({"o": ast.parse("import pydoc\npydoc.locate('p.t')")}) == {"*"}
    assert (
        voided_modules({"o": ast.parse("import pathlib, sys, re\nsys.path.insert(0, '')")}) == set()
    )
    assert "*" in voided_modules({"o": ast.parse("x = go('functools.lru_cache')")})
    assert voided_modules({"o": ast.parse("x = go('not a module')")}) == set()


def test_voided_modules_unit():
    patch = ast.parse("import functools\nfunctools.lru_cache = 1\n")
    plain = ast.parse("import json\n")
    assert voided_modules({"a": patch, "b": plain}) == {"functools"}
    assert voided_modules({"b": plain}) == set()
    assert voided_modules({"a": ast.parse("import json\nsetattr(json, 'x', 1)")}) == {"json"}
    assert voided_modules({"a": ast.parse("import json\nsetattr(json, 'x', 1)\neval('1')")}) >= {
        "*"
    }
    assert _counts(PASS[0], voided={"functools"})
    assert _counts(PASS[0], voided={"*"})


def test_in_tree_helper_that_does_not_patch_does_not_void():
    assert (
        voided_modules(
            {"a": ast.parse("import functools\n\n\n@functools.lru_cache\ndef f(): pass")}
        )
        == set()
    )


DECORATORS = ["@functools.lru_cache", "@functools.lru_cache(maxsize=1)", "@lru_cache", "@missing"]
HEADS = [
    "import functools",
    "import functools\nfunctools = 1",
    "from functools import lru_cache",
    "from functools import lru_cache\nfrom factory import *",
    "import functools, factory\nfunctools.lru_cache = factory.register",
]


def _main_rule(src: str) -> bool:
    return any(isinstance(n, ast.FunctionDef) and n.decorator_list for n in ast.parse(src).body)


def test_differential_against_main_rule_agrees_except_on_the_must_pass_set():
    differs = []
    for deco, head in itertools.product(DECORATORS, HEADS):
        src = _fn(deco, head)
        new = _counts(src)
        assert _main_rule(src)  # main counts every decorated top-level function
        if not new:
            differs.append(src)
    assert set(differs) <= {s for s in PASS} | {
        _fn("@functools.lru_cache(maxsize=1)"),
        _fn("@lru_cache", "from functools import lru_cache"),
        _fn("@lru_cache", "from functools import lru_cache\nfrom factory import *"),
    } | {
        _fn("@functools.lru_cache"),
        _fn("@lru_cache", "from factory import *\nfrom functools import lru_cache"),
    }
    assert differs  # the relaxation still exists
