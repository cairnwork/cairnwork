"""#1413: inherited deletion accepts only the closed test-module grammar."""

import pytest
from test_p2p_inherited_allowlist_1413 import EVIL_HELPER, _payload
from test_p2p_inherited_runtime_1413 import HONEST, NODE, _side

from cairnwork.p2p_classify_child import classify_payload

HOOK_SHAPES = {
    "attach_run_delete": "class Hook(object):\n"
    "    def __init_subclass__(cls):\n"
    "        def body(self):\n            assert True\n"
    "        setattr(cls, 'test_' + 'x', body)\n"
    "        getattr(cls, 'test_' + 'x')(None)\n"
    "        delattr(cls, 'test_' + 'x')\n",
    "exec": "class Hook(object):\n"
    "    def __init_subclass__(cls):\n        exec('import os; open(os.devnull)')\n"
    "",
    "evil": "import evil\nclass Hook(object):\n"
    "    def __init_subclass__(cls):\n        import evil; evil.mark.attach(cls)\n"
    "",
    "new_class": "class Hook(object):\n"
    "    def __init_subclass__(cls):\n"
    "        import types\n"
    "        types.new_class('H2', (), exec_body=lambda ns: ns.update({'test_' + 'x': f}))\n"
    "",
}

CLASS_SHAPES = {
    "set_name": "    def __set_name__(self, owner, name): pass\n",
    "class_getitem": "    def __class_getitem__(cls, key): pass\n",
    "init": "    def __init__(self): pass\n",
    "fixture": "    @pytest.fixture\n    def test_k(self): pass\n",
    "staticmethod": "    @staticmethod\n    def test_k(): pass\n",
    "property": "    @property\n    def test_k(self): return 1\n",
    "annotation": "    def test_k(self, a: int): pass\n",
    "default": "    def test_k(self, a=len): pass\n",
    "async": "    async def test_k(self): pass\n",
    "nested_class": "    class Inner: pass\n",
    "annotated_assignment": "    value: int = 1\n",
}

BLOCKED = {**HOOK_SHAPES, **CLASS_SHAPES} | {
    "helper_class": "class Helper: pass\n",
    "helper_def": "def helper(): return 1\n",
    "metaclass": "class TestExtra(metaclass=type): pass\n",
    "non_test_base": "class Helper: pass\nclass TestExtra(Helper): pass\n",
    "pytestmark": "import pytest\npytestmark = pytest.mark.slow\n",
    "dunder_assignment": "__all__ = ['x']\n",
    "pytest_plugins": "pytest_plugins = ['evil']\n",
    "pytest_plugins_str": "pytest_plugins = 'evil'\n",
    "class_pytestmark": "class TestExtra:\n    pytestmark = 'x'\n",
    "fixture_call": "import pytest\n@pytest.fixture(autouse=True)\ndef test_k(): pass\n",
    "mark_name_arg": "import pytest\n@pytest.mark.skipif(len([]))\ndef test_k(): pass\n",
    "builtin_base": "class TestExtra(object): pass\n",
    "attribute_base": "import pytest\nclass TestExtra(pytest.Class): pass\n",
    "later_base": "class TestExtra(TestB): pass\n",
}


def _source(name):
    if name in HOOK_SHAPES:
        hook = HOOK_SHAPES[name]
        return hook + HONEST.replace("class TestA:", "class TestA(Hook):")
    if name in CLASS_SHAPES:
        prefix = "import pytest\n" if name == "fixture" else ""
        insert = CLASS_SHAPES[name]
        return prefix + HONEST.replace("class TestA:", "class TestA:\n" + insert)
    return BLOCKED[name] + HONEST


@pytest.mark.parametrize("name", sorted(BLOCKED))
def test_closed_grammar_rejects_non_shapes(name):
    result = classify_payload(_payload(_source(name)))
    assert NODE in result["dropped"] and NODE not in result["removed"]


@pytest.mark.parametrize("name", ["attach_run_delete", "evil"])
def test_hook_shapes_rejected_with_collect_fact(tmp_path, name):
    extra = (("evil.py", EVIL_HELPER),) if name == "evil" else ()
    side = _side(tmp_path, _source(name), real_fact=True, extra=extra)
    assert NODE in side.dropped and NODE not in side.removed


def test_pytest_plugins_constant_rejected_with_collect_fact(tmp_path):
    """A literal pytest_plugins loads an unchanged module during collection."""
    source = "pytest_plugins = ['evil_plugin']\n" + HONEST
    side = _side(tmp_path, source, real_fact=True, extra=(("evil_plugin.py", "RAN = True\n"),))
    assert NODE in side.dropped and NODE not in side.removed


PASSING = {
    "honest": HONEST,
    "parametrize": "import pytest\nclass TestA:\n"
    "    @pytest.mark.parametrize('a', [1, 2])\n"
    "    def test_k(self, a):\n        assert a\n"
    "class TestB(TestA): pass\n",
    "class_and_function_marks": "import pytest\n@pytest.mark.slow\nclass TestA:\n"
    "    @pytest.mark.slow\n    def test_k(self): pass\n"
    "class TestB(TestA): pass\n",
    "future": "from __future__ import annotations\n" + HONEST,
    "docstring": '"""module doc"""\n' + HONEST,
    "constants": "N = (1, [2])\nclass TestA:\n    VALUE = {'x': 3}\n"
    "    def test_k(self, x=1, *args, y=(2,), **kwargs): pass\n"
    "class TestB(TestA): pass\n",
    "utf8_cookie": "# coding: utf8\n" + HONEST,
    "utf8_hyphen_cookie": "# -*- coding: utf-8 -*-\n" + HONEST,
    "body_calls": "class TestA:\n    def test_k(self): do_anything()\nclass TestB(TestA): pass\n",
}


@pytest.mark.parametrize("source", PASSING.values(), ids=PASSING.keys())
def test_closed_grammar_keeps_supported_shapes_removed(source):
    result = classify_payload(_payload(source))
    assert NODE in result["removed"]
