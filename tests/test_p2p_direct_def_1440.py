"""#1440: direct-def deletions must not hide a live import-time test."""

import pytest
from test_collect_exposed_1413 import TARGET, collect
from test_p2p_runtime_gate_1413 import BASE_CLASS, BASE_TOP, KEEP, _e2e

from cairnwork.p2p_classify_child import classify_payload

NODE = f"{TARGET}::TestA::test_x"
BASE = "class TestA:\n    def test_x(self):\n        assert True\n"


def _payload(source, *, base=BASE, node=NODE):
    return {
        "base_ids": [node],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": [TARGET],
        "exposed": [],
        "sources": {"base": {TARGET: base}, "test": {TARGET: source}},
    }


def _top_payload(source, base=BASE_TOP):
    node = f"{TARGET}::test_x"
    return _payload(source, base=base, node=node)


def _class_source(extra=""):
    return "class TestA:\n    pass\n" + extra


IMPORT_USE = {
    "module_call": "def new_body(): pass\nnew_body()\n",
    "decorator": "def deco(f): return f\n@deco\ndef new_body(): pass\n",
    "default": "def new_body(): pass\ndef consume(f=new_body): pass\n",
    "class_body": "def new_body(): pass\nclass Helper:\n    value = new_body()\n",
    "unchanged_helper": "def new_body(): pass\ndef stable(): return new_body()\nstable()\n",
    "globals_lookup": "def new_body(): pass\nresult = globals()['new_body']()\n",
}

DYNAMIC_USE = {
    "vars": "vars()['new_body']\n",
    "getattr": "getattr(object, 'new_body', None)\n",
    "exec": "exec('new_body()')\n",
    "eval": "eval('new_body')\n",
    "import_call": "__import__('builtins')\n",
    "sys_modules": "import sys\nsys.modules.get('test')\n",
    "importlib": "import importlib\nimportlib.import_module('os')\n",
    "dict_lookup": "class Holder: pass\nHolder.__dict__.get('new_body')\n",
}


@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
@pytest.mark.parametrize("shape", sorted(IMPORT_USE))
def test_direct_def_kept_by_import_time_execution_is_dropped(shape, top):
    use = IMPORT_USE[shape]
    source = ("def test_keep(): pass\n" if top else _class_source()) + use
    if shape == "unchanged_helper":
        base = "def stable(): return new_body()\n" + (BASE_TOP if top else BASE)
    else:
        base = BASE_TOP if top else BASE
    payload = _top_payload(source, base) if top else _payload(source, base=base)
    result = classify_payload(payload)
    node = f"{TARGET}::test_x" if top else NODE
    assert node in result["dropped"] and node not in result["removed"]


@pytest.mark.parametrize("shape", sorted(DYNAMIC_USE))
def test_dynamic_import_lookup_with_changed_def_stays_dropped(shape):
    source = _class_source("def new_body(): pass\n" + DYNAMIC_USE[shape])
    result = classify_payload(_payload(source))
    assert NODE in result["dropped"]


def test_dynamic_lookup_through_unchanged_import_helper_stays_dropped():
    base = "def stable(): return globals()['new_body']\n" + BASE
    source = (
        "def new_body(): pass\ndef stable(): return globals()['new_body']\n"
        "stable()\nclass TestA: pass\n"
    )
    assert NODE in classify_payload(_payload(source, base=base))["dropped"]


def test_changed_helper_used_only_in_test_body_does_not_block_removal():
    source = _class_source("def helper(): return 2\ndef test_new():\n    assert helper() == 2\n")
    assert NODE in classify_payload(_payload(source))["removed"]


def test_unchanged_import_helper_call_does_not_block_removal():
    base = "def is_ci(): return False\n" + BASE
    source = (
        "import pytest\ndef is_ci(): return False\n"
        "pytestmark = pytest.mark.skipif(is_ci())\nclass TestA: pass\n"
    )
    assert NODE in classify_payload(_payload(source, base=base))["removed"]


@pytest.mark.parametrize(
    "holder",
    [
        "H = type('H', (), {'__module__': 'foreign', 'test_orphan': f})",
        "import types\nH = types.new_class('H', (), {}, "
        "lambda ns: ns.update(test_orphan=f))\nH.__module__ = 'foreign'",
        "import abc\nH = abc.ABCMeta('H', (), {'__module__': 'foreign', 'test_orphan': f})",
        "H = type('H', (), {'__module__': 'foreign', 'test_orphan': staticmethod(f)})",
        "H = type('H', (), {'__module__': 'foreign', 'test_orphan': classmethod(f)})",
        "import functools\nH = type('H', (), {'__module__': 'foreign', "
        "'test_orphan': functools.partial(f)})",
        "bound = f.__get__(object(), object)\nH = type('H', (), "
        "{'__module__': 'foreign', 'test_orphan': bound})",
        "H = type('H', (), {'__module__': 'foreign'})\nsetattr(H, 'test_orphan', f)",
    ],
    ids=[
        "type",
        "new_class",
        "abcmeta",
        "staticmethod",
        "classmethod",
        "partial",
        "bound-method",
        "setattr",
    ],
)
def test_collect_only_reports_test_callable_by_origin_module(tmp_path, holder):
    source = "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\n" + holder + "\n"
    record = collect(tmp_path, source)
    assert record["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]


def test_other_module_classes_do_not_consume_the_pair_cap(tmp_path):
    source = (
        "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\n"
        "H = type('H', (), {'__module__': 'foreign', 'test_orphan': f})\n"
    )
    record = collect(tmp_path, source, pair_cap=0)
    assert record["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]


@pytest.mark.sandbox
@pytest.mark.parametrize("top", [False, True], ids=["class", "top-level"])
def test_real_entry_refuses_import_time_reachability(repo, tmp_path, top):
    base = BASE_TOP if top else BASE_CLASS
    changed = (
        "def new_body(): pass\ndef stable(): return new_body()\nstable()\n"
        if top
        else "class TestA:\n" + KEEP + "    pass\ndef new_body(): pass\n"
        "def stable(): return new_body()\nstable()\n"
    )
    result = _e2e(repo, tmp_path, base, changed)
    assert not result.verified
    assert result.reason.startswith(("TEST_COMMIT_DROPS_BASE_TESTS", "DEFERRED_TESTS_UNREADABLE"))
