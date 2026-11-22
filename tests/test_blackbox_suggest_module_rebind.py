"""tools/blackbox_suggest (#370): a project module that may rebind another module's names refuses.

The reader takes a drafted target's last plain binding in its own module. A project module the
target imports, the target's own module included, can still change that binding or a global the
target reads from outside the def: by a store on an attribute of an imported name, by passing a
module on as a value, or by starting a patcher. Each of these refuses the draft.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

INI = "[pytest]\npythonpath = src\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"
PLAIN = "SCALE = 1\ndef f(x):\n    return x * 2 * SCALE\n"
BASE = {"pytest.ini": INI, "src/pkg/__init__.py": "", "tests/test_x.py": TEST}


def run(tmp_path, files):
    for rel, body in (BASE | files).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def core(tail: str) -> dict[str, str]:
    return {"src/pkg/core.py": PLAIN + tail}


DEFS = "class K:\n    S = 1\nREG = {'k': K()}\nTABLE = {'k': 1}\ndef get():\n    return K\n"
OTHER = {"src/pkg/other.py": DEFS}


def held(tail: str) -> dict[str, str]:
    return core(DEFS + tail)


def star(tail: str) -> dict[str, str]:
    return {"src/pkg/core.py": "from pkg.other import *\n" + PLAIN + tail, **OTHER}


REFUSED = {
    "store_a": core("import pkg.core\npkg.core.SCALE = 10\n"),
    "store_b": core("import pkg.core as me\nme.SCALE = 10\n"),
    "store_c": core("from pkg import core\ncore.SCALE = 10\n"),
    "store_d": core("import pkg.core\npkg.core.f = lambda x: 0\n"),
    "store_e": core("import pkg.core\ndef s():\n    pkg.core.SCALE = 10\ns()\n"),
    "store_f": core("class K:\n    import pkg.core as m\nK.m.SCALE = 10\n"),
    "store_g": core("import pkg.core\ndel pkg.core.SCALE\n"),
    "store_h": core("import pkg.core\npkg.core.SCALE += 9\n"),
    "store_i": core("import pkg.core\n(lambda: pkg.core)().SCALE = 10\n"),
    "store_j": core("import os\nos.sep = 'x'\n"),
    "store_k": {
        "src/pkg/core.py": "import pkg.other\n" + PLAIN,
        "src/pkg/other.py": "import pkg.core\npkg.core.SCALE = 10\n",
    },
    "store_l": {"src/pkg/__init__.py": "from . import core\ncore.SCALE = 10\n", **core("")},
    "store_m": held("from pkg.core import REG\nREG['k'].v = 1\n"),
    "store_n": held("from pkg import core\ncore.get().x = 1\n"),
    "store_o": held("from pkg.core import K as Q\nQ.S = 99\n"),
    "held_a": held("from pkg import core\nKx = core.K\nKx.S = 99\n"),
    "held_b": held("from pkg import core\ndef s():\n    k = core.K\n    k.S = 99\ns()\n"),
    "held_c": held("from pkg import core\ndef go(k):\n    k.S = 99\ngo(core.K)\n"),
    "held_d": held("from pkg import core\nfor k in [core.K]:\n    k.S = 99\n"),
    "held_e": held(
        "from pkg import core\nclass H:\n    def __init__(self):\n        self.k = core.K\n"
        "H().k.S = 99\n"
    ),
    "held_f": held("import pkg.core\ndef g():\n    return pkg.core.K\ng().S = 99\n"),
    "held_g": held(
        "from pkg import core\nclass H:\n    def go(self, k):\n        k.S = 99\nH().go(core.K)\n"
    ),
    "held_h": held("from pkg import core\nx = core.REG['k']\nx.v = 1\n"),
    "held_i": held("from pkg import core\nk = core.K if 1 else None\nk.S = 99\n"),
    "held_j": held("from pkg import core\na, b = core.K, core.TABLE\na.S = 99\n"),
    "held_k": held("from pkg import core\ndef go(k):\n    k.S = 99\ngo(k=core.K)\n"),
    "held_l": held("from pkg import core\nL = [core.K]\na, = L\na.S = 99\n"),
    "held_m": held("from pkg import core\nL = [[core.K]]\nfor x in L:\n    x[0].S = 99\n"),
    "package_value": core("import pkg.core\nprint(pkg)\n"),
    "table_a": held("from pkg.core import TABLE\nTABLE['k'] = 99\n"),
    "table_b": held("from pkg import core\ncore.TABLE['k'] = 99\n"),
    "table_c": held("from pkg import core\ncore.TABLE.update(k=99)\n"),
    "table_d": held("from pkg import core\nT = core.TABLE\nT['k'] = 99\n"),
    "table_e": held("from pkg.core import TABLE\ndel TABLE['k']\n"),
    "star_a": star("K.S = 99\n"),
    "star_b": star("TABLE['k'] = 99\n"),
    "star_c": star("REG.clear()\n"),
    "value_a": core("import pkg.core\nm = pkg.core\nm.SCALE = 10\n"),
    "value_b": core("import pkg.core\ndef s(m):\n    m.SCALE = 10\ns(pkg.core)\n"),
    "value_c": core("import pkg.core\nfor m in [pkg.core]:\n    m.SCALE = 10\n"),
    "value_d": core("import pkg.core\nclass K:\n    mod = pkg.core\nK.mod.SCALE = 10\n"),
    "value_e": core("def g():\n    import pkg.core as m\n    return m\ng().SCALE = 10\n"),
    "value_f": core("import pkg\ngetattr(pkg, 'core').SCALE = 10\n"),
    "value_g": core("import pkg.core\nx = pkg\nx.core.SCALE = 10\n"),
    "value_h": core("from pkg import core\ndef s(m):\n    m.SCALE = 10\ns(core)\n"),
    "patcher_a": core("from unittest import mock\nmock.patch('pkg.core.SCALE', 10).start()\n"),
    "patcher_b": core(
        "import pkg.core\nfrom unittest import mock\n"
        "mock.patch.object(pkg.core, 'SCALE', 10).start()\n"
    ),
    "patcher_c": core("import pytest\npytest.MonkeyPatch().setattr('pkg.core.SCALE', 10)\n"),
    "patcher_d": core("import unittest.mock\nunittest.mock.patch('pkg.core.SCALE', 10).start()\n"),
    "patcher_e": core("import pytest\nM = pytest.MonkeyPatch\nM().setattr('pkg.core.SCALE', 10)\n"),
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_module_that_may_rebind_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain": core(""),
    "method_store": core("class K:\n    def __init__(self):\n        self.x = 1\nK()\n"),
    "object_store": core("class N:\n    pass\nn = N()\nn.x = 1\n"),
    "class_store": core("class K:\n    n = 1\nK.n = 2\n"),
    "object_from_call": core("import types\nns = types.SimpleNamespace()\nns.x = 1\n"),
    "module_call": core("import os\nP = os.path.join('a', 'b')\n"),
    "module_attribute_read": {
        "src/pkg/core.py": "import pkg.other\n" + PLAIN + "X = pkg.other.V\n",
        "src/pkg/other.py": "V = 1\n",
    },
    "local_containers": core("D = {}\nD['k'] = 1\nL = []\nL.append(1)\n"),
    "star_read": star("X = K.S + TABLE['k']\n"),
    "held_read": held("from pkg import core\nT = core.TABLE\nX = T['k']\n"),
    "held_container_append": held("from pkg import core\nL = [core.K]\nL.append(1)\n"),
    "unpacked_fresh_container": held("from pkg import core\nk, L = core.K, []\nL.append(1)\n"),
    "copy_in_container": held(
        "from pkg import core\nfor d in [dict(core.TABLE)]:\n    d['k'] = 2\n"
    ),
    "module_function_named_like_mutator": {
        "src/pkg/core.py": "from pkg import other\n" + PLAIN + "other.clear()\n",
        "src/pkg/other.py": "def clear():\n    pass\n",
    },
    "stdlib_value": core("import json\nALL = [json]\n"),
    "hasattr_probe": core("import os\nHAS = hasattr(os, 'getuid')\n"),
    "getattr_probe": core("import os\nO = getattr(os, 'O_PATH', 0)\n"),
    "class_named_like_module": {
        "src/pkg/__init__.py": "class Backend:\n    pass\n",
        "src/pkg/backend.py": "",
        **core("from pkg import Backend\nALL = [Backend]\n"),
    },
}


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_a_module_that_rebinds_nothing_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
