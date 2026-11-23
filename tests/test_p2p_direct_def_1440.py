"""#1440: a direct-def deletion is not `removed` while the body still runs or is re-attached.

Two families: (1) the deleted body kept as a changed def that code run at import reaches;
(2) the body attached under a computed name to a class whose `__module__` is not the test file.
"""

import pytest
from test_collect_exposed_1413 import TARGET, collect
from test_p2p_runtime_gate_1413 import BASE_CLASS, BASE_TOP, KEEP, REFUSED, _e2e

from cairnwork.p2p_collect import classify

pytestmark = pytest.mark.sandbox

KEEP_TOP = "def test_keep():\n    assert True\n"
# the deleted body survives as a new def `_body`; each shape makes import-time code reach it
RUNS_AT_IMPORT = {
    "call": "_body({a})",
    "decorator": "@_body\ndef other():\n    pass",
    "default": "def other(a=_body({a})):\n    pass",
    "class_body": "class Box:\n    value = _body({a})",
    "dynamic": "globals()['_bo' + 'dy']({a})",
    "list_item": "[_body][0]({a})",
    "string_name": "getattr(__import__('sys').modules[__name__], '_body')({a})",
    "lambda": "_body = lambda *a: None\n_body({a})",
    "comprehension": "[_body({a}) for _ in (1,)]",
    "fstring": "print(f'{{_body({a})}}')",
    # review of fdc9b8fe: shapes that slipped through the first rule
    "lambda_call": "(lambda: _body({a}))()",
    "import_module": "__import__('importlib').import_module(__name__).__dict__['_bo' + 'dy']({a})",
    "dunder_import": "__import__(__name__).__dict__['_bo' + 'dy']({a})",
    "ann_lambda": "_go: object = lambda: _body({a})\n_go()",
    "walrus_lambda": "(_go := lambda: _body({a}))()",
    "tuple_lambda": "_go, _other = (lambda: _body({a})), None\n_go()",
    "list_lambda": "_fs = [lambda: _body({a})]\n_fs[0]()",
    "enum_hook": "import enum\nclass E(enum.Enum):\n"
    "    def _generate_next_value_(name, start, count, last):\n        _body({a})\n"
    "        return name\n    a = enum.auto()",
    "class_init": "class K:\n    def __init__(self):\n        _body({a})\nK()",
    # review of fb9d82c6: delta and cross-model findings
    "string_lookup": "__import__('sys').modules[__name__].__dict__['_body']({a})",
    "string_name_only": "import pkgutil\npkgutil.resolve_name(__name__ + '._body')({a})",
    "module_dict_read": "import sys as _s\n_s.modules[__name__].__dict__['_bo' + 'dy']({a})",
    "builtins_alias": "from builtins import vars as v\nv()['_bo' + 'dy']({a})",
    "unchanged_enum_hook": "import enum\nclass E(enum.Enum):\n"
    "    def _generate_next_value_(name, start, count, last):\n        return _body({a}) or name\n"
    "    a = enum.auto()",
    "globals_attr": "def _hook():\n    pass\n_hook.__globals__['_bo' + 'dy']({a})",
    "getattribute_attr": "K = type('K', (), {{}})\nK.__getattribute__(K, '__doc__')\n_body({a})",
    "builtins_name": "print(__builtins__)\n_body({a})",
    "moved_body_is_a_method": "class K:\n    def __init__(self):\n        assert True\nK()",
    "ann_lambda_body": "_body: object = lambda *a: None\n_body({a})",
    "walrus_lambda_body": "(_body := lambda *a: None)\n_body({a})",
    "unpack_lambda_body": "_body, _o = (lambda *a: None), 1\n_body({a})",
    "list_lambda_body": "_body = [lambda *a: None][0]\n_body({a})",
    "decorated_class": "@_body\nclass Box:\n    pass",
    # hooks that run without being named at the call site
    "init_subclass": "class Base:\n    def __init_subclass__(cls, **kw):\n        _body({a})\n"
    "class Sub(Base):\n    pass",
    "set_name": "class D:\n    def __set_name__(self, o, n):\n        _body({a})\n"
    "class K:\n    x = D()",
    "metaclass_new": "class M(type):\n    def __new__(m, n, b, d):\n        _body({a})\n"
    "        return super().__new__(m, n, b, d)\nclass K(metaclass=M):\n    pass",
}
# an unchanged helper (same in base and test) that reaches a changed one, called at import
VIA_HELPER_BASE = "def _hook():\n    return 1\ndef _setup():\n    return _hook()\n"


def _file(top: bool, shape: str) -> str:
    arg = "" if top else "None"
    body = "def _body({}):\n    assert True\n".format("*a" if top else "self")
    if shape == "via_helper":
        return "def _hook():\n    assert True\ndef _setup():\n    return _hook()\n_setup()\n" + (
            KEEP_TOP if top else "class TestA:\n" + KEEP
        )
    prelude = "" if shape == "lambda" else body
    return (
        prelude
        + RUNS_AT_IMPORT[shape].format(a=arg)
        + "\n"
        + (KEEP_TOP if top else "class TestA:\n" + KEEP)
    )


def _classify(tmp_path, base, test, top):
    record = collect(tmp_path, test)
    node = f"{TARGET}::test_x" if top else f"{TARGET}::TestA::test_x"
    ids = set(record["collected"])
    args = ({node, *ids}, ids, {TARGET}, lambda _: test, lambda _: base)
    return node, args, record


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", [*RUNS_AT_IMPORT, "via_helper"])
def test_a_changed_def_reached_at_import_keeps_the_deletion_dropped(tmp_path, shape, top):
    base = (VIA_HELPER_BASE if shape == "via_helper" else "") + (BASE_TOP if top else BASE_CLASS)
    node, args, record = _classify(tmp_path, base, _file(top, shape), top)
    assert classify(*args, exposed=tuple(record["uncollected_exposed"])).dropped == {node}


HONEST_NEW = {
    "helper_only_in_a_test_body": ("def _h():\n    return 1\n", True),
    # a changed dunder on a class that only a test body uses runs nothing at import
    "dunder_class_only_in_test_body": (
        "class _H:\n    def __init__(self):\n        self.v = 1\ndef _h():\n    return _H()\n",
        True,
    ),
    # words that look like dynamic lookups, but only inside string constants
    # reading a class __dict__ at import is not a lookup of a module-level def
    "class_dict_read": (
        "class _B:\n    pass\n_N = list(_B.__dict__)\ndef _h():\n    return 1\n",
        True,
    ),
    "unused_sunder_helper": (
        "def _generate_next_value_(n, s, c, last):\n    return n\ndef _h():\n    return 1\n",
        True,
    ),
    "dynamic_words_in_strings": (
        "def _h():\n    return 1\n"
        "pytestmark = pytest.mark.skipif(False, reason='needs a temp dir, no exec or getattr')\n"
        "_P = pytest.mark.parametrize('n', ['exec', 'eval', 'globals'])\n",
        True,
    ),
    "unchanged_helper_called_at_import": (
        "def _ci():\n    return False\npytestmark = pytest.mark.skipif(_ci(), reason='x')\n",
        False,
    ),
}


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", sorted(HONEST_NEW))
def test_honest_deletions_next_to_helpers_stay_removed(tmp_path, shape, top):
    helper, used_in_test = HONEST_NEW[shape]
    call = "_h()" if used_in_test else "assert True"
    keep = (
        f"def test_keep():\n    {call}\n"
        if top
        else f"class TestA:\n    def test_keep(self):\n        {call}\n"
    )
    base_keep = BASE_TOP if top else BASE_CLASS
    base = "import pytest\n" + ("" if used_in_test else helper) + base_keep
    test = "import pytest\n" + helper + keep
    node, args, _ = _classify(tmp_path, base, test, top)
    assert classify(*args, exposed=()).removed == {node}


# (2) the holding class is not in the test module: the runtime fact must still name it
OTHER_MODULE = {
    "new_class_concat": "import types\ndef _fill(ns):\n    ns['test_' + 'orphan'] = f\n"
    "H = types.new_class('H', (), exec_body=_fill)",
    "abc_meta_concat": "import abc\nH = abc.ABCMeta('H', (), {'test_' + 'orphan': f})",
    "class_of_stdlib_module": "import json\nH = json.JSONDecoder\n"
    "setattr(H, 'test_' + 'orphan', f)",
    "staticmethod_wrapped": "import types\nH = types.new_class('H')\n"
    "setattr(H, 'test_' + 'orphan', staticmethod(f))",
    "classmethod_wrapped": "import types\nH = types.new_class('H')\n"
    "setattr(H, 'test_' + 'orphan', classmethod(f))",
    "partial_wrapped": "import functools, types\nH = types.new_class('H')\n"
    "setattr(H, 'test_' + 'orphan', functools.partial(f))",
    "bound_method": "import types\nH = types.new_class('H')\n"
    "setattr(H, 'test_' + 'orphan', types.MethodType(f, H()))",
    "name_spoof": "__name__ = 'elsewhere'\nimport abc\n"
    "H = abc.ABCMeta('H', (), {'test_' + 'orphan': f})",
}


@pytest.mark.parametrize("shape", sorted(OTHER_MODULE))
def test_collector_names_a_target_callable_held_by_a_class_of_another_module(tmp_path, shape):
    body = (
        f"def test_orphan():\n    pass\nf = test_orphan\ndel test_orphan\n{OTHER_MODULE[shape]}\n"
    )
    record = collect(tmp_path, body)
    assert [e.rpartition("::")[2] for e in record["uncollected_exposed"]] == ["test_orphan"]
    assert record["uncollected_exposed"][0].startswith(f"{TARGET}::")


def test_collector_ignores_a_callable_whose_origin_is_not_a_test_module(tmp_path):
    helper = "def test_x(): pass\n"
    body = (
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\nimport helper_mod\n"
        "import types\nH = types.new_class('H')\nH.test_x = helper_mod.test_x\n"
    )
    record = collect(tmp_path, body, extra=[("helper_mod.py", helper)])
    assert record["uncollected_exposed"] == []


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", ["new_class_concat", "abc_meta_concat", "class_of_stdlib_module"])
def test_other_module_reattachment_is_dropped_with_the_real_fact(tmp_path, shape, top):
    base = BASE_TOP if top else BASE_CLASS
    prefix = "def _x():\n    assert True\n" if top else "def _x(self):\n    assert True\n"
    test = (
        prefix
        + "f = _x\ndel _x\n"
        + OTHER_MODULE[shape].replace("orphan", "x")
        + "\n"
        + (KEEP_TOP if top else "class TestA:\n" + KEEP)
    )
    node, args, record = _classify(tmp_path, base, test, top)
    assert record["uncollected_exposed"], "the collector must name the re-attached test"
    assert classify(*args, exposed=tuple(record["uncollected_exposed"])).dropped == {node}


# end to end through the real entry (Seatbelt, CAIRNWORK_REQUIRE_SANDBOX=1)
E2E = {
    "call": ("call", "def _body({s}):\n    assert True\n_body({a})\n"),
    "new_class": (
        "new_class_concat",
        "def _x({s}):\n    assert True\nf = _x\ndel _x\n"
        + OTHER_MODULE["new_class_concat"].replace("orphan", "x"),
    ),
    "abc_meta": (
        "abc_meta_concat",
        "def _x({s}):\n    assert True\nf = _x\ndel _x\n"
        + OTHER_MODULE["abc_meta_concat"].replace("orphan", "x"),
    ),
    "stdlib_class": (
        "class_of_stdlib_module",
        "def _x({s}):\n    assert True\nf = _x\ndel _x\n"
        + OTHER_MODULE["class_of_stdlib_module"].replace("orphan", "x"),
    ),
}


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("name", sorted(E2E))
def test_e2e_the_real_entry_refuses_both_families(repo, tmp_path, name, top):
    template = E2E[name][1]
    code = template.replace("{s}", "" if top else "self").replace("{a}", "" if top else "None")
    test = code + "\n" + (KEEP_TOP if top else "class TestA:\n" + KEEP)
    result = _e2e(repo, tmp_path, BASE_TOP if top else BASE_CLASS, test)
    assert not result.verified
    assert result.reason.startswith(REFUSED), result.reason


def test_object_enumeration_is_a_dynamic_lookup_at_import():
    """gc.get_objects cannot run inside the collect sandbox, so this one is checked on the AST."""
    import ast

    from cairnwork.p2p_import_time import import_time_reaches_changed_def as reaches

    test = ast.parse(
        "import gc, types\ndef _body(): pass\n"
        "[f for f in gc.get_objects() if isinstance(f, types.FunctionType)][0]()\n"
    )
    assert reaches(test, ast.parse("x = 1\n"))
