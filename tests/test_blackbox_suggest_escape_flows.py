"""tools/blackbox_suggest (#370): a value taken from a project module is followed through
every way it can be handed on, and a use that changes it refuses the draft."""

import pytest

from tests.test_blackbox_suggest_module_rebind import held, run
from tools.blackbox_suggest import KINDS_NOW

IMP = "from pkg import core\nimport heapq, bisect, contextlib, functools\n"

REFUSED = {
    "flows_operator_loop": "L = [core.TABLE]\nfor t in L:\n    t |= {'k': 1}\n",
    "flows_operator_parameter": "def go(t):\n    t |= {'k': 1}\ngo(core.TABLE)\n",
    "flows_operator_chain": "a = b = core.TABLE\nb |= {'k': 1}\n",
    "flows_default_value": "def go(t=core.K):\n    t.S = 9\ngo()\n",
    "flows_default_keyword_only": "def go(*, t=core.TABLE):\n    t['k'] = 9\ngo()\n",
    "flows_default_lambda": "f = lambda t=core.TABLE: t.update(k=1)\nf()\n",
    "flows_default_second": "def go(a, t=core.TABLE):\n    t.pop('k')\ngo(1)\n",
    "flows_container_item_store": "D = {}\nD['t'] = core.TABLE\nD['t']['k'] = 9\n",
    "flows_container_item_store_view": (
        "D = {}\nD['t'] = core.TABLE\nfor t in D.values():\n    t['k'] = 9\n"
    ),
    "flows_container_index": "L = [core.K]\nt = L[0]\nt.S = 9\n",
    "flows_container_items": "D = {'t': core.TABLE}\nfor k, t in D.items():\n    t.pop('k')\n",
    "flows_container_get": "D = {'t': core.TABLE}\nt = D.get('t')\nt['k'] = 9\n",
    "flows_container_append_iterate": (
        "L = []\nL.append(core.TABLE)\nfor t in L:\n    t['k'] = 9\n"
    ),
    "flows_container_insert_index": "L = []\nL.insert(0, core.K)\nL[0].S = 9\n",
    "flows_class_attribute": "class H:\n    t = core.TABLE\nH.t['k'] = 9\n",
    "flows_class_attribute_annotated": "class H:\n    t: dict = core.TABLE\nH.t['k'] = 9\n",
    "flows_class_attribute_instance": "class H:\n    t = core.TABLE\nH().t['k'] = 9\n",
    "flows_class_attribute_method": "class H:\n    t = core.TABLE\nH.t.update(k=9)\n",
    "flows_library_function": "heapq.heapify(core.REG)\n",
    "flows_library_function_alias": "r = core.REG\nbisect.insort(r, 1)\n",
    "flows_library_function_keyword": "heapq.heappush(heap=core.REG, item=1)\n",
    "flows_method_value_alias": "u = core.TABLE.update\nu(k=9)\n",
    "flows_method_value_second_alias": "d = core.TABLE\nu = d.pop\nu('k')\n",
    "flows_method_value_getattr": "getattr(core.TABLE, 'update')(k=9)\n",
    "flows_method_value_getattr_alias": "m = getattr(core.TABLE, 'update')\nm(k=9)\n",
    "flows_method_value_partial": "functools.partial(core.TABLE.update, k=9)()\n",
    "flows_method_value_map": "list(map(core.TABLE.update, [{'k': 9}]))\n",
    "flows_lambda_call": "(lambda t: t.update(k=9))(core.TABLE)\n",
    "flows_lambda_named": "f = lambda t: t.update(k=9)\nf(core.TABLE)\n",
    "flows_lambda_map": "list(map(lambda t: t.update(k=9), [core.TABLE]))\n",
    "flows_lambda_sorted": "sorted(core.REG, key=lambda t: t.update(k=9))\n",
    "flows_match_subject": "match core.TABLE:\n    case t:\n        t['k'] = 9\n",
    "flows_match_mapping": "match core.REG:\n    case {'k': v}:\n        v.x = 1\n",
    "flows_match_sequence": "match [core.K]:\n    case [k]:\n        k.S = 9\n",
    "flows_match_guard": "match core.K:\n    case x if x:\n        x.S = 9\n",
    "flows_match_star": "match [core.TABLE, 1]:\n    case [*rest]:\n        rest[0]['k'] = 9\n",
    "flows_handback_with": ("with contextlib.nullcontext(core.TABLE) as t:\n    t['k'] = 9\n"),
    "flows_handback_with_closing": "with contextlib.closing(core.K) as k:\n    k.S = 9\n",
    "flows_handback_exception": (
        "try:\n    raise ValueError(core.TABLE)\nexcept ValueError as e:\n    e.args[0]['k'] = 9\n"
    ),
    "flows_handback_exception_value": "e = ValueError(core.K)\ne.args[0].S = 9\n",
}
KEPT = {
    "flows_operator_copy": "t = dict(core.TABLE)\nt |= {'k': 1}\n",
    "flows_default_copy": "def go(t=dict(core.TABLE)):\n    t['k'] = 1\ngo()\n",
    "flows_default_read": "def go(t=core.TABLE):\n    return t['k']\ngo()\n",
    "flows_container_copy": "D = {}\nD['t'] = dict(core.TABLE)\nD['t']['k'] = 9\n",
    "flows_container_read": "D = {}\nD['t'] = core.TABLE\nx = D['t']['k']\n",
    "flows_container_view_read": "D = {'t': core.TABLE}\nfor t in D.values():\n    x = t['k']\n",
    "flows_class_attribute_copy": "class H:\n    t = dict(core.TABLE)\nH.t['k'] = 9\n",
    "flows_class_attribute_read": "class H:\n    t = core.TABLE\nx = H.t['k']\n",
    "flows_library_function_read": "n = len(core.REG)\n",
    "flows_method_value_read": "g = core.TABLE.get\nx = g('k')\n",
    "flows_method_value_module_function": "g = core.get\nx = g()\n",
    "flows_lambda_copy": "(lambda t: t.update(k=9))(dict(core.TABLE))\n",
    "flows_match_read": "match core.TABLE:\n    case {'k': v}:\n        y = v\n",
    "flows_match_copy": "match dict(core.TABLE):\n    case t:\n        t['k'] = 9\n",
    "flows_handback_with_copy": (
        "with contextlib.nullcontext(dict(core.TABLE)) as t:\n    t['k'] = 9\n"
    ),
    "flows_handback_exception_copy": ("e = ValueError(dict(core.TABLE))\ne.args[0]['k'] = 9\n"),
}


@pytest.mark.parametrize("tail", REFUSED.values(), ids=REFUSED.keys())
def test_a_value_handed_on_and_changed_refuses(tmp_path, tail):
    item = run(tmp_path, held(IMP + tail))
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("tail", KEPT.values(), ids=KEPT.keys())
def test_a_value_handed_on_and_only_read_keeps_the_draft(tmp_path, tail):
    item = run(tmp_path, held(IMP + tail))
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
