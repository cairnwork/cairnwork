"""Issue #1147 fixed case corpus: reject wrong changes and preserve honest access."""

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

SIGNAL = "import sys\nsys.path.insert(0, 'x')\n"
WRONG_CASES = [
    ("wrong_getattr_function_name", "def getattr(x):\n    return x\n"),
    ("wrong_getattr_class_name", "class getattr:\n    pass\n"),
    ("wrong_getattr_async_function_name", "async def getattr(x):\n    return x\n"),
    ("wrong_getattr_from_import", "from x import getattr\n"),
    ("wrong_getattr_import_alias", "import getattr as g\n"),
    ("wrong_getattr_import_asname", "import x as getattr\n"),
    ("wrong_getattr_parameter", "def f(getattr):\n    return getattr\n"),
    ("wrong_getattr_keyword", "f(getattr=1)\n"),
    ("wrong_getattr_result_call", "def f(x):\n    return getattr(x, 'n').__call__()\n"),
    ("wrong_getattr_result_subscript_write", "def f(x):\n    getattr(x, 'n')[0] = 1\n"),
    ("wrong_getattr_result_mutation", "def f(x):\n    getattr(x, 'n').append(1)\n"),
    ("wrong_getattr_param_module", "def f(m):\n    return getattr(m, 'path')\nf(sys)\n"),
    (
        "wrong_getattr_selfattr_chain",
        "class W:\n    def __init__(self):\n        self.mod = sys\n"
        "    def r(self):\n        return getattr(self.mod, 'path')\n",
    ),
    ("wrong_getattr_tuple_alias", "a, b = sys, 1\ndef r():\n    return getattr(a, 'path')\n"),
    ("wrong_getattr_walrus_alias", "(m := sys)\ndef r():\n    return getattr(m, 'path')\n"),
    (
        "wrong_getattr_ifexp_alias",
        "m = sys if True else None\ndef r():\n    return getattr(m, 'path')\n",
    ),
    (
        "wrong_getattr_subscript_target",
        "mods = [sys]\ndef r():\n    return getattr(mods[0], 'path')\n",
    ),
    (
        "wrong_getattr_for_alias",
        "def r():\n    for m in (sys,):\n        return getattr(m, 'path')\n",
    ),
    (
        "wrong_getattr_call_result_root",
        "class W:\n    def h(self):\n        return sys\n"
        "    def r(self):\n        getattr(self.h().path, 'append')('/evil')\n",
    ),
    ("wrong_getattr_module_literal", "def r():\n    return getattr(sys, 'modules')\n"),
    (
        "wrong_getattr_via_import_module",
        "import importlib\nm = importlib.import_module('sys')\n"
        "def r():\n    return getattr(m, 'path')\n",
    ),
    (
        "wrong_getattr_via_dunder_import",
        "m = __import__('sys')\ndef r():\n    return getattr(m, 'path')\n",
    ),
    (
        "wrong_getattr_via_globals",
        "m = globals()['sys']\ndef r():\n    return getattr(m, 'path')\n",
    ),
    ("wrong_getattr_nonliteral_name", "def r(n):\n    return getattr(sys, n)\n"),
    ("wrong_getattr_concat_name", "def r(o):\n    return getattr(o, 'meta' + '_path')\n"),
    ("wrong_setattr_concat_name", "sys.__setattr__('meta' + '_path', [])\n"),
    ("wrong_type_setattr", "type(sys).__setattr__(sys, n, [])\n"),
    ("wrong_object_setattr", "object.__setattr__(sys, n, [])\n"),
    ("wrong_inspect_getattr_static", "import inspect\ninspect.getattr_static(sys, n)\n"),
    ("wrong_getattr_static_alias", "from inspect import getattr_static as g\ng(sys, n)\n"),
    ("wrong_module_getattr_hook", "def __getattr__(name):\n    return 1\n"),
    ("wrong_mixed_getattr_setattr", "f(getattr(a, 'b'), setattr)\n"),
    ("wrong_getattr_word_string", "getattr(a, 'getattr')\n"),
    ("wrong_setattr_param_module", "def f(m):\n    setattr(m, 'path', [])\nf(sys)\n"),
    (
        "wrong_setattr_selfattr_chain",
        "class W:\n    def __init__(self):\n        self.mod = sys\n"
        "    def r(self):\n        setattr(self.mod, 'path', [])\n",
    ),
    ("wrong_setattr_tuple_alias", "a, b = sys, 1\ndef r():\n    setattr(a, 'path', [])\n"),
    ("wrong_setattr_module_literal", "def r():\n    setattr(sys, 'path', [])\n"),
    ("wrong_setattr_nonliteral_name", "def r(o, n):\n    setattr(o, n, 1)\n"),
    ("wrong_vars_param_module", "def f(m):\n    vars(m)['path'] = []\nf(sys)\n"),
    (
        "wrong_vars_selfattr_chain",
        "class W:\n    def __init__(self):\n        self.mod = sys\n"
        "    def r(self):\n        vars(self.mod)['path'] = []\n",
    ),
    ("wrong_vars_tuple_alias", "a, b = sys, 1\ndef r():\n    vars(a)['path'] = []\nf = 0\n"),
    ("wrong_vars_module_direct", "def r():\n    return vars(sys)\n"),
    ("wrong_vars_noarg", "def r():\n    return vars()\n"),
    (
        "wrong_indirect_getattr_alias_nonliteral",
        "g = getattr\ndef r(obj):\n    g(sys, 'mod' + 'ules')['x'] = obj\n",
    ),
    (
        "wrong_indirect_getattr_dunder_call",
        "def r():\n    return getattr.__call__(sys, 'modules')\n",
    ),
    (
        "wrong_indirect_getattr_partial",
        "import functools\ndef r():\n    return functools.partial(getattr, sys)('modules')\n",
    ),
    ("wrong_indirect_getattr_in_list", "def r():\n    return [getattr][0](sys, 'modules')\n"),
    ("wrong_indirect_setattr_alias", "s2 = setattr\ndef r():\n    s2(sys, 'modules', {})\n"),
    (
        "wrong_indirect_getattr_as_argument",
        "def call(fn, o, n):\n    return fn(o, n)\ndef r():\n"
        "    return call(getattr, sys, 'modules')\n",
    ),
    ("wrong_indirect_vars_alias", "v = vars\ndef r():\n    return v(sys)\n"),
    ("wrong_indirect_getattr_map", "def r():\n    return list(map(getattr, [sys], ['modules']))\n"),
    ("wrong_indirect_getattr_default_arg", "def r(g=getattr):\n    return g(sys, 'modules')\n"),
    ("wrong_indirect_getattr_alias_no_module", "g = getattr\ndef r(o, n):\n    return g(o, n)\n"),
    (
        "wrong_indirect_builtins_getattr",
        "import builtins\ndef r():\n    return builtins.getattr(sys, 'modules')\n",
    ),
    (
        "wrong_indirect_attrgetter",
        "import operator\ndef r():\n    return operator.attrgetter('modules')(sys)\n",
    ),
    ("wrong_indirect_default_arg_module", "def r(m=sys):\n    return getattr(m, 'modules')\n"),
    (
        "wrong_indirect_list_index_alias",
        "x = [sys][0]\ndef r():\n    return getattr(x, 'modules')\n",
    ),
    (
        "wrong_indirect_class_attr_module",
        "class W:\n    mod = sys\n    def r(self):\n        return getattr(self.mod, 'modules')\n",
    ),
    (
        "wrong_container_call_root_append",
        "class W:\n    def h(self):\n        return sys\n"
        "    def r(self):\n        getattr(self.h().path, 'append')('/evil')\n",
    ),
    (
        "wrong_container_param_insert",
        "def f(m):\n    getattr(m.path, 'insert')(0, '/evil')\nf(sys)\n",
    ),
    (
        "wrong_container_param_update",
        "def f(m):\n    getattr(m.modules, 'update')({'x': 1})\nf(sys)\n",
    ),
    (
        "wrong_container_stored_bound_method",
        "def r():\n    g = getattr(sys.path, 'append')\n    g('/evil')\n",
    ),
    (
        "wrong_vars_selfparam_module_function",
        "def f(self, k):\n    vars(self)[k] = {}\nf(sys, 'mod' + 'ules')\n",
    ),
    (
        "wrong_vars_selfparam_nested",
        "def outer():\n    def f(self):\n        return vars(self)\n    return f(sys)\n",
    ),
    (
        "wrong_vars_selfparam_lambda",
        "g = lambda self: vars(self)\ndef r():\n    return g(sys)\n",
    ),
    (
        "wrong_vars_selfparam_unbound_method",
        "class C:\n    def v(self):\n        return vars(self)\ndef r():\n    return C.v(sys)\n",
    ),
    (
        "wrong_vars_self_match_capture",
        "def r(x):\n    match x:\n        case self:\n            return vars(self)\n",
    ),
    (
        "wrong_vars_self_except_as",
        "def r():\n    try:\n        raise RuntimeError\n"
        "    except RuntimeError as self:\n        return vars(self)\n",
    ),
    (
        "wrong_vars_cls_classmethod_module",
        "class C:\n    @classmethod\n    def v(cls):\n        return vars(cls)\n"
        "def r():\n    return C.v.__func__(sys)\n",
    ),
]
HONEST_CASES = [
    (
        "honest_getattr_self_opts",
        "class W:\n    def t(self):\n        return getattr(self.opts, 'timeout', 10)\n",
        "allow",
    ),
    (
        "honest_setattr_self",
        "class W:\n    def a(self):\n        setattr(self, 'active', True)\n",
        "allow",
    ),
    ("honest_vars_self", "class W:\n    def v(self):\n        return vars(self)\n", "refuse"),
    ("honest_getattr_param", "def f(cfg):\n    return getattr(cfg, 'name', None)\n", "allow"),
    ("honest_getattr_opts_default", "def f(opts, d):\n    return getattr(opts, 'x', d)\n", "allow"),
    (
        "honest_getattr_nonliteral_name",
        "def f(cfg, n):\n    return getattr(cfg, n, None)\n",
        "refuse",
    ),
    ("honest_getattr_os_literal", "import os\ndef f():\n    return getattr(os, 'sep')\n", "allow"),
    (
        "honest_getattr_result_string_method",
        "def f(opts):\n    return getattr(opts, 'name', '').strip()\n",
        "allow",
    ),
    (
        "honest_frozen_dataclass_setattr",
        "from dataclasses import dataclass\n@dataclass(frozen=True)\n"
        "class W:\n    x: int\n    def __post_init__(self, v):\n"
        "        object.__setattr__(self, 'x', v)\n",
        "refuse",
    ),
]


def _judge(repo, body):
    base = git(repo, "rev-parse", "HEAD")
    candidate = commit_change(repo, {"pkg/calc.py": SIGNAL + body})
    changed = tuple(git(repo, "diff", "--name-only", base, candidate).split())
    return candidate_sys_path.refuse_new_sys_path_mutation(repo, base, candidate, changed)


def _assert_refused(repo, body):
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, body)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(("case", "body"), WRONG_CASES, ids=[case for case, _ in WRONG_CASES])
def test_wrong_dynamic_access_cases_refuse(repo, case, body):
    _assert_refused(repo, body)


@pytest.mark.parametrize(
    ("case", "body", "expect"), HONEST_CASES, ids=[case for case, *_ in HONEST_CASES]
)
def test_honest_dynamic_access_cases_match_expectation(repo, case, body, expect):
    if expect == "refuse":
        _assert_refused(repo, body)
    else:
        _judge(repo, body)
