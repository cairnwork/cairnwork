"""tools/blackbox_suggest (#370): a value taken from a project module stays in known-safe uses."""

import pytest

from tests.test_blackbox_suggest_module_rebind import held, run
from tools.blackbox_suggest import KINDS_NOW

IMP = "from pkg import core\nimport heapq\nimport types\n"
NS = "x = types.SimpleNamespace(get=heapq.heapify, dumps=heapq.heapify, submit=heapq.heapify)\n"
ESCAPE = "it hands a value taken from a project module to a use this reader does not follow"

REFUSED = {
    "escape_unlisted_method": held(IMP + "core.K.reset()\n"),
    "escape_unlisted_method_alias": held(IMP + "k = core.K\nk.reset()\n"),
    "escape_unknown_callee": held(IMP + "heapq.heapify(core.REG)\n"),
    "escape_unknown_callee_alias": held(IMP + "h = core.REG\nheapq.heappush(h, 1)\n"),
    "escape_unknown_callee_keyword": held(IMP + "heapq.heapify(x=core.REG)\n"),
    "escape_unknown_callee_starred": held(IMP + "h = core.REG\nheapq.heapify(*h)\n"),
    "escape_method_as_value": held(IMP + "list(map(core.REG.pop, ['k']))\n"),
    "escape_method_on_unheld_receiver": held(IMP + "x = []\nx.extend(core.REG)\n"),
    "escape_in_place_operator": held(IMP + "r = core.REG\nr |= {'x': 1}\n"),
    "escape_unclassified_position": held(IMP + "x = (lambda: core.REG)\n"),
    "escape_method_named_like_own_function": held(IMP + "x = object()\nx.K(core.REG)\n"),
    "escape_argument_to_held_receiver": held(IMP + "k = core.K\nk.index(core.REG)\n"),
    "escape_element_through_read_method": held(IMP + "core.REG.get('k').reset()\n"),
    "escape_element_through_copy": held(IMP + "dict(core.REG)['k'].reset()\n"),
    "escape_element_through_builtin": held(IMP + "next(iter(core.REG.values())).reset()\n"),
    "escape_element_through_builtin_alias": held(
        IMP + "x = list(core.REG.values())\nx[0].reset()\n"
    ),
    "escape_element_through_min": held(IMP + "x = min(core.REG.values())\nx.reset()\n"),
    "escape_element_through_max_alias": held(IMP + "x = max(core.REG)\nx.reset()\n"),
    "escape_element_of_new_container": held(IMP + "x = [core.REG['k']]\nx[0].reset()\n"),
    "escape_renamed_library_name": held(IMP + "from heapq import heapify as len\nlen(core.REG)\n"),
    "escape_rebound_library_name": held(IMP + "len = heapq.heapify\nlen(core.REG)\n"),
    "escape_parameter_named_like_library": held(IMP + "def f(len):\n    len(core.REG)\n"),
    "escape_renamed_later_name": held(
        IMP + "from heapq import heapify as register\nregister(core.REG)\n"
    ),
    "escape_library_name_on_unknown_receiver": held(IMP + NS + "x.dumps(core.REG)\n"),
    "escape_later_on_unknown_receiver": held(IMP + NS + "x.submit(core.REG)\n"),
    "escape_element_through_zip": held(
        IMP + "for a, b in zip(core.REG.values(), [1]):\n    a.reset()\n"
    ),
    "escape_element_through_enumerate": held(
        IMP + "for i, a in enumerate(core.REG.values()):\n    a.reset()\n"
    ),
    "escape_element_through_next_iter": held(
        IMP + "x = list(core.REG.values())\ny = next(iter(x))\ny.reset()\n"
    ),
    "escape_element_of_assigned_copy": held(IMP + "x = dict(core.REG)\nx['k'].reset()\n"),
    "escape_element_of_method_copy": held(IMP + "x = core.REG.copy()\nx['k'].reset()\n"),
    "escape_element_of_copy_in_container": held(
        IMP + "L = [dict(core.REG)]\nfor d in L:\n    d['k'].reset()\n"
    ),
    "escape_element_after_concatenation": held(IMP + "x = [core.K] + [1]\nk, y = x\nk.reset()\n"),
    "escape_method_as_callback": held(IMP + "def run(fn):\n    fn()\nrun(core.K.reset)\n"),
    "escape_container_in_unlisted_position": held(IMP + "x = [core.K]\ng = lambda: x\n"),
}
KEPT = {
    "safe_read_method": held(IMP + "x = core.REG.get('k')\n"),
    "safe_reading_builtin": held(IMP + "n = len(core.REG)\n"),
    "safe_module_function": held(IMP + "core.get()\n"),
    "safe_function_for_later": held(IMP + "list(map(core.get, []))\n"),
    "safe_own_function": held(IMP + "def f(v):\n    return len(v)\nf(core.REG)\n"),
    "safe_compare": held(IMP + "if core.REG == {}:\n    pass\n"),
    "safe_read_method_argument": held(IMP + "x = {}.get(core.REG['k'])\n"),
    "safe_display_starred": held(IMP + "x = [*core.REG]\n"),
    "safe_library_module_function": held(IMP + "import json\ns = json.dumps(core.TABLE)\n"),
    "safe_library_imported_name": held(
        IMP + "from pathlib import Path\np = Path(str(core.TABLE))\n"
    ),
    "safe_literal_method_argument": held(IMP + "s = ', '.join(core.TABLE)\n"),
    "safe_element_read": held(IMP + "x = core.REG.get('k')\nn = str(x.S)\n"),
    "safe_element_through_builtin": held(IMP + "n = len(list(core.REG.values()))\n"),
    "safe_library_later": held(IMP + "from functools import partial\ng = partial(core.get)\n"),
    "safe_new_container_changed": held(IMP + "x = [core.REG]\nx.append(1)\n"),
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_value_in_an_unlisted_use_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and item.refusal.endswith(ESCAPE)
    assert not item.protected(KINDS_NOW)


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_a_value_in_a_safe_use_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
