"""tools/blackbox_suggest (#370): a value taken from a project module stays in known-safe uses."""

import pytest

from tests.test_blackbox_suggest_module_rebind import held, run
from tools.blackbox_suggest import KINDS_NOW

IMP = "from pkg import core\nimport heapq\n"

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
    "escape_argument_to_held_receiver": held(IMP + "k = core.K\nk.index(core.REG)\n"),
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
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_value_in_an_unlisted_use_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_a_value_in_a_safe_use_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
