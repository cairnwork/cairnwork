"""RED tests for #1230 R1 findings: sys-alias escapes, evaluator holes, deep AST, M7."""

from __future__ import annotations

from pathlib import Path

import pytest

from experimental.committed_sys_path import check_source, git_io, is_test_file

BASE_DIRS = frozenset({"", ".", "src", "bin", "tests", "linkdir"})

_R1_MUST_BLOCK: list[tuple[str, str]] = [
    ("sys_modules_subscript", "import sys\nsys.modules['sys'].path.insert(0, '/tmp')\n"),
    ("sys_modules_get", "import sys\nsys.modules.get('sys').path.insert(0, '/tmp')\n"),
    (
        "sys_modules_alias",
        "import sys\nm = sys.modules\nm['sys'].path.insert(0, '/tmp')\n",
    ),
    (
        "from_sys_modules",
        "from sys import modules\nmodules['sys'].path.insert(0, '/tmp')\n",
    ),
    (
        "sys_modules_dyn_subscript",
        "import sys\nk = ''.join(['s', 'ys'])\nsys.modules[k].path.insert(0, '/tmp')\n",
    ),
    (
        "sys_modules_dyn_get",
        "import sys\nk = ''.join(['s', 'ys'])\nm = sys.modules.get(k)\nm.path.insert(0, '/tmp')\n",
    ),
    (
        "builtins_dunder_import",
        "import builtins\nbuiltins.__import__('sys').path.insert(0, '/tmp')\n",
    ),
    (
        "builtins_alias_dunder_import",
        "import builtins\nb = builtins\nb.__import__('sys').path.insert(0, '/tmp')\n",
    ),
    ("aliased_dunder_import", "imp = __import__\nimp('sys').path.insert(0, '/tmp')\n"),
    ("concat_dunder_import", "__import__('s' + 'ys').path.insert(0, '/tmp')\n"),
    ("dunder_builtins_dict", "__builtins__['__import__']('sys').path.insert(0, '/tmp')\n"),
    (
        "aliased_import_module",
        "from importlib import import_module as im\nim('sys').path.insert(0, '/tmp')\n",
    ),
    (
        "importlib_alias_import_module",
        "import importlib\nim = importlib\nim.import_module('sys').path.insert(0, '/tmp')\n",
    ),
    (
        "os_alias_getattr_sys",
        "import os\no = os\ngetattr(o, 's' + 'ys').path.insert(0, '/tmp')\n",
    ),
    (
        "dynamic_import_module",
        "from importlib import import_module\n"
        "dyn_name = 's' + 'ys'\n"
        "import_module(dyn_name).path.insert(0, '/tmp')\n",
    ),
    ("globals_concat", "import sys\nglobals()['s' + 'ys'].path.insert(0, '/tmp')\n"),
    ("vars_sys_dict", "import sys\nvars(sys)['path'].insert(0, '/tmp')\n"),
    ("vars_locals_sys", "import sys\nvars()['sys'].path.insert(0, '/tmp')\n"),
    ("os_dunder_dict", "import os\nos.__dict__['sys'].path.insert(0, '/tmp')\n"),
    ("sys_dunder_dict", "import sys\nsys.__dict__['path'].insert(0, '/tmp')\n"),
    (
        "locals_sys",
        "import sys\ndef f():\n    s = sys\n    locals()['s'].path.insert(0, '/tmp')\nf()\n",
    ),
    ("param_escape_sys", "import sys\ndef f(s):\n    s.path.insert(0, '/tmp')\nf(sys)\n"),
    ("return_escape_sys", "import sys\ndef g():\n    return sys\ng().path.insert(0, '/tmp')\n"),
    ("param_escape_path", "import sys\ndef h(p):\n    p.insert(0, '/tmp')\nh(sys.path)\n"),
    ("for_loop_sys", "import sys\nfor s in [sys]:\n    s.path.insert(0, '/tmp')\n"),
    ("list_comp_sys", "import sys\n[s.path.insert(0, '/tmp') for s in [sys]]\n"),
    (
        "with_nullcontext",
        "import sys\nfrom contextlib import nullcontext\n"
        "with nullcontext(sys) as s:\n    s.path.insert(0, '/tmp')\n",
    ),
    ("class_attr_sys", "import sys\nclass C:\n    s = sys\nC.s.path.insert(0, '/tmp')\n"),
    (
        "method_return_sys",
        "import sys\nclass C:\n    def get_s(self):\n        return sys\n"
        "C().get_s().path.insert(0, '/tmp')\n",
    ),
    (
        "simple_namespace",
        "import sys\nfrom types import SimpleNamespace\n"
        "SimpleNamespace(s=sys).s.path.insert(0, '/tmp')\n",
    ),
    ("dict_get_sys", "import sys\n{'k': sys}.get('k').path.insert(0, '/tmp')\n"),
    ("lambda_sys", "import sys\n(lambda s: s.path.insert(0, '/tmp'))(sys)\n"),
    ("getattr_concat_path", "import sys\ngetattr(sys, 'pa' + 'th').insert(0, '/tmp')\n"),
    ("getattr_sys_alias_path", "import sys\nm = sys\ngetattr(m, 'path').insert(0, '/tmp')\n"),
    ("getattr_builtins_open", "import builtins\ngetattr(builtins, 'open')('/tmp/x', 'w')\n"),
    ("setattr_sys_path", "import sys\nsetattr(sys, 'path', ['/tmp'])\n"),
    (
        "attrgetter_path",
        "import sys\nfrom operator import attrgetter\nattrgetter('path')(sys).insert(0, '/tmp')\n",
    ),
    ("tuple_unpack_slice", "import sys\nsys.path[:], x = ['/tmp'], 1\n"),
    ("starred_target", "import sys\n*sys.path, = ['/tmp']\n"),
    ("subscript_assign", "import sys\nsys.path[0] = '/tmp'\n"),
    ("del_slice", "import sys\ndel sys.path[:]\n"),
    (
        "monkeypatch_syspath_prepend",
        "def f(monkeypatch):\n    monkeypatch.syspath_prepend('/tmp')\n",
    ),
    ("pytester_syspathinsert", "def f(pytester):\n    pytester.syspathinsert('/tmp')\n"),
    ("exec_sys_path", "exec(\"import sys; sys.path.insert(0, '/tmp')\")\n"),
    ("eval_import_sys_path", "eval(\"__import__('sys').path.insert(0, '/tmp')\")\n"),
    (
        "shadowed_str_builtin",
        "import sys\nfrom pathlib import Path\nstr = lambda x: '/tmp'\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))\n",
    ),
    ("class_attr_sys_only", "import sys\nclass C:\n    s = sys\n"),
    ("class_attr_sys_with_fn_alias", "import sys\ndef f():\n    s = sys\nclass C:\n    s = sys\n"),
    ("class_attr_sys_nested_if", "import sys\nclass C:\n    if True:\n        s = sys\n"),
    ("attr_target_sys", "import sys\nclass C:\n    pass\nC.s = sys\n"),
    ("writable_blocked_sys_modules", "import sys\nsys.modules = {}\n"),
    ("writable_blocked_sys_executable", "import sys\nsys.executable = '/tmp/py'\n"),
    ("del_safe_sys_attr_blocked", "import sys\ndel sys.dont_write_bytecode\n"),
]

_R1_MUST_PASS: list[tuple[str, str]] = [
    ("custom_non_sys_obj", "class N:\n    path = []\nN().path.insert(0, 'custom')\n"),
    ("readonly_subscript_len", "import sys\nprint(sys.path[0], len(sys.path))\n"),
    ("readonly_for_loop", "import sys\nfor p in sys.path:\n    print(p)\n"),
    (
        "getattr_nontracked_append_insert_path",
        "class N:\n    path = []\n    def append(self, x): pass\n"
        "n = N()\ngetattr(n, 'append')(1)\ngetattr(n, 'path')\ngetattr([], 'insert')(0, 1)\n",
    ),
    (
        "getattr_tokenize_tamper_scan_tokens",
        "import tokenize\nfor kind in 'FT':\n"
        "    _ = getattr(tokenize, f'{kind}STRING_START', None)\n",
    ),
    (
        "getattr_ast_field_tamper_scan_walk",
        "import ast\ndef walk(node):\n"
        "    for f in node._fields:\n        _ = getattr(node, f, None)\n",
    ),
    (
        "getattr_old_python_probe_pattern",
        "import sys\ndef check(cls):\n    mod = sys.modules.get(cls.__module__)\n"
        "    return getattr(mod, 'MAPPING', None)\n",
    ),
    ("getattr_os_safe_attr", "import os\nflags = getattr(os, 'O_NOFOLLOW', 0)\n"),
    (
        "local_sys_alias_insert",
        "import sys, os\ns = sys\n"
        "s.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
    ),
    (
        "local_sys_path_alias_insert",
        "import sys, os\np = sys.path\n"
        "p.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
    ),
    (
        "destructure_sys_alias_insert",
        "import sys, os\na, s = 1, sys\n"
        "s.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
    ),
    (
        "annassign_sys_alias_insert",
        "import sys, os\ns: object = sys\n"
        "s.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
    ),
    (
        "walrus_sys_alias_insert",
        "import sys, os\n(s := sys).path.insert(\n"
        "    0, os.path.join(os.path.dirname(__file__), '..', 'bin')\n)\n",
    ),
    (
        "safe_writable_sys_attrs",
        "import sys\nsys.dont_write_bytecode = True\nsys.argv = ['prog']\nsys.tracebacklimit = 5\n",
    ),
    (
        "zero_arg_main_helper",
        "import sys, os\ndef main():\n"
        "    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\nmain()\n",
    ),
]

_EVALUATOR_BYPASS_CASES: list[tuple[str, str, str]] = [
    (
        "pathlib_parent_cw_root_top",
        "x.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '_CW_ROOT_' / 'bin'))\n",
    ),
    (
        "pathlib_parents2_cw_root",
        "src/x.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_CW_ROOT_' / 'bin'))\n",
    ),
    (
        "pathlib_parent_cw_root_sub",
        "src/x.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '_CW_ROOT_' / 'bin'))\n",
    ),
    (
        "os_join_dotdot_cw_root",
        "src/x.py",
        "import os, sys\n"
        "sys.path.insert(0, os.path.join("
        "os.path.dirname(os.path.abspath(__file__)), '..', '..', '_CW_ROOT_', 'bin'))\n",
    ),
    (
        "os_join_top_cw_root",
        "x.py",
        "import os, sys\n"
        "sys.path.insert(0, os.path.join("
        "os.path.dirname(os.path.abspath(__file__)), '..', '_CW_ROOT_', 'bin'))\n",
    ),
    (
        "os_join_descend_then_dotdot",
        "src/x.py",
        "import os, sys\nHERE = os.path.dirname(__file__)\n"
        "sys.path.insert(0, os.path.join(HERE, '..', 'linkdir', '..', 'bin'))\n",
    ),
    (
        "pathlib_descend_then_parent",
        "src/x.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str((Path(__file__).resolve().parent / 'linkdir').parent / 'bin'))\n",
    ),
    (
        "pathlib_descend_then_parents",
        "src/x.py",
        "import sys\nfrom pathlib import Path\nsys.path.insert(\n"
        "    0,\n"
        "    str((Path(__file__).resolve().parent.parent / 'linkdir').parents[0] / 'bin'),\n)\n",
    ),
    (
        "cw_root_file_dir",
        "_CW_ROOT_/x.py",
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n",
    ),
]


@pytest.mark.parametrize(("case_id", "code"), _R1_MUST_BLOCK, ids=[c[0] for c in _R1_MUST_BLOCK])
def test_r1_sys_alias_and_escape_families_reject(case_id: str, code: str) -> None:
    """All R1 sys-alias and search-path escape families must REJECT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"
    if case_id == "del_slice":
        assert any(f.get("kind") == "sys.path.del" for f in res.findings)


def test_r1_test_helpers_in_conftest_reject_when_included() -> None:
    """monkeypatch.syspath_prepend and pytester.syspathinsert reject when tests included."""
    for code in (
        b"def pytest_configure(): pass\ndef f(monkeypatch): monkeypatch.syspath_prepend('/tmp')\n",
        b"def f(pytester): pytester.syspathinsert('/tmp')\n",
    ):
        res = check_source("tests/conftest.py", None, code, BASE_DIRS, include_tests=True)
        assert res.verdict == "REJECT"


@pytest.mark.parametrize(("case_id", "code"), _R1_MUST_PASS, ids=[c[0] for c in _R1_MUST_PASS])
def test_r1_must_pass_controls_accept(case_id: str, code: str) -> None:
    """Non-sys custom objects and read-only sys.path introspection must ACCEPT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "ACCEPT", f"{case_id}: got {res.verdict} ({res.reason})"


@pytest.mark.parametrize(
    ("case_id", "fpath", "code"),
    _EVALUATOR_BYPASS_CASES,
    ids=[c[0] for c in _EVALUATOR_BYPASS_CASES],
)
def test_r1_evaluator_sentinel_and_traversal_reject(case_id: str, fpath: str, code: str) -> None:
    """Literal _CW_ROOT_ sentinel collisions and descending-then-.. traversal must REJECT."""
    dirs = BASE_DIRS | {"_CW_ROOT_", "_CW_ROOT_/bin"}
    res = check_source(fpath, None, code.encode("utf-8"), dirs)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"
    assert res.reason == "NON_COMMITTED_SEARCH_PATH"


def test_r1_m22_join_onto_file_rejects() -> None:
    """M22: joining onto __file__ directly rejects even when target dir is committed."""
    code = b"import os, sys\nsys.path.insert(0, os.path.join(__file__, '..', 'cairnwork'))\n"
    res = check_source("src/x.py", None, code, BASE_DIRS | {"src/cairnwork"})
    assert res.verdict == "REJECT"
    assert res.reason == "NON_COMMITTED_SEARCH_PATH"


def test_r1_load_tree_dirs_defensive_symlink_prefix_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M17/R2-5b: load_tree_dirs drops symlinked dirs in base or candidate even if in tree."""
    base_oid, cand_oid = "a" * 40, "b" * 40
    dirs = {"", ".", "bin", "bin/sub", "src"}
    monkeypatch.setattr(
        git_io,
        "_ls_tree",
        lambda _r, sha: (dirs, dirs, {"bin"}, {}) if sha == base_oid else (dirs, dirs, set(), {}),
    )
    assert "bin" not in (git_io.load_tree_dirs(Path("."), base_oid, cand_oid) or ())
    monkeypatch.setattr(
        git_io,
        "_ls_tree",
        lambda _r, sha: (dirs, dirs, set(), {}) if sha == base_oid else (dirs, dirs, {"bin"}, {}),
    )
    assert "bin/sub" not in (git_io.load_tree_dirs(Path("."), base_oid, cand_oid) or ())


def test_r1_deep_ast_recursion_and_memory_guard_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep ASTs (650/20k concat, 5k attr chain) and MemoryError return ERROR without raising."""
    mid_terms = " + ".join("'a'" for _ in range(520))
    v_mid = check_source("src/x.py", None, f"y = {mid_terms}\n".encode(), BASE_DIRS)
    assert v_mid.verdict == "ERROR"
    assert v_mid.reason == "UNREADABLE_SOURCE"
    assert v_mid.findings == ({"error": "unparseable", "path": "src/x.py"},)

    terms = " + ".join(f"'{chr(97 + (i % 2))}'" for i in range(20_000))
    concat_code = f"import sys\nsys.path.insert(0, {terms})\n".encode()
    v_concat = check_source("src/x.py", None, concat_code, BASE_DIRS)
    assert v_concat.verdict == "ERROR"

    chain = "x" + ".a" * 5_000
    target_chain_code = f"import sys\nx = object()\nsys.path.insert(0, {chain})\n".encode()
    v_chain = check_source("src/x.py", None, target_chain_code, BASE_DIRS)
    assert v_chain.verdict == "ERROR"

    bare_chain_code = f"x = object()\n{chain}\n".encode()
    v_bare = check_source("src/x.py", None, bare_chain_code, BASE_DIRS)
    assert v_bare.verdict in ("ACCEPT", "ERROR")

    def raise_mem(*_args: object, **_kwargs: object) -> object:
        raise MemoryError("oom")

    monkeypatch.setattr("experimental.committed_sys_path.source_check.parse_source", raise_mem)
    v_mem = check_source("src/x.py", None, b"x = 1\n", BASE_DIRS)
    assert v_mem.verdict == "ERROR"
    assert v_mem.reason == "UNREADABLE_SOURCE"


def test_mutant_m7_tests_prefix_lookalike_not_skipped() -> None:
    """M7: tests_evil/x.py and conftest_evil.py are not treated as tests/ and reject."""
    assert is_test_file("tests_evil/x.py") is False
    assert is_test_file("conftest_evil.py") is False
    assert is_test_file("tests/conftest.py") is True
    assert is_test_file("conftest.py") is True
    res = check_source(
        "tests_evil/x.py",
        None,
        b"import sys; sys.path.insert(0, '/tmp')",
        frozenset({"src"}),
    )
    assert res.verdict == "REJECT"
