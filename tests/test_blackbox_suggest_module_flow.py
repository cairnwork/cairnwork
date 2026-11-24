"""tools/blackbox_suggest (#370): a value taken from a project module that reaches a store."""

import pytest

from tests.test_blackbox_suggest_module_rebind import held, run
from tools.blackbox_suggest import KINDS_NOW

IMP = "from pkg import core\n"

REFUSED = {
    "flow_second_pass": held(IMP + "b = a\na = core.K\nb.S = 99\n"),
    "flow_starred_argument": held(IMP + "def go(a, b):\n    b.S = 99\ngo(*core.K)\n"),
    "flow_with": held(IMP + "with core.K as k:\n    k.S = 99\n"),
    "flow_walrus": held(IMP + "if (k := core.K):\n    k.S = 99\n"),
    "flow_starred_target": held(IMP + "x, *rest = [core.K]\nrest.S = 99\n"),
    "flow_dict_key": held(IMP + "D = {core.K: 1}\nfor k in D:\n    k.S = 99\n"),
    "flow_list_comp": held(IMP + "L = [core.K for _ in (1,)]\nfor k in L:\n    k.S = 99\n"),
    "flow_set_comp": held(IMP + "L = {core.K for _ in (1,)}\nfor k in L:\n    k.S = 99\n"),
    "flow_generator": held(IMP + "L = (core.K for _ in (1,))\nfor k in L:\n    k.S = 99\n"),
    "flow_dict_comp": held(IMP + "L = {core.K: 1 for _ in (1,)}\nfor k in L:\n    k.S = 99\n"),
    "flow_self_method": held(
        IMP + "class H:\n    def go(self, k):\n        k.S = 99\nh = H()\nh.go(core.K)\n"
    ),
    "flow_cls_method": held(
        IMP + "class H:\n    @classmethod\n    def go(cls, k):\n        k.S = 99\nH.go(core.K)\n"
    ),
    "flow_yield": held(IMP + "def g():\n    yield core.K\ng().S = 99\n"),
    "flow_annotated": held(IMP + "k: object = core.K\nk.S = 99\n"),
    "flow_async": held(IMP + "async def g():\n    return core.K\ng().S = 99\n"),
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_value_flow_into_a_store_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "flow_fresh_walrus": held(IMP + "if (k := object()):\n    k.S = 99\n"),
    "flow_fresh_with": held(IMP + "import io\nwith io.StringIO() as k:\n    k.S = 99\n"),
    "flow_fresh_generator": held(IMP + "def g():\n    yield 1\ng().S = 99\n"),
}


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_a_fresh_value_flow_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
