"""#1440 review round 3: import-time paths to a changed def; honest changes stay removed."""

import pytest

from cairnwork.p2p_classify_child import classify_payload

TARGET = "tests/test_dynamic.py"
BASE = "class TestA:\n    def test_x(self):\n        assert True\n"
NODE = f"{TARGET}::TestA::test_x"


def _verdict(code, extra_base=""):
    payload = {
        "base_ids": [NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": [TARGET],
        "exposed": [],
        "sources": {
            "base": {TARGET: BASE + extra_base},
            "test": {TARGET: "class TestA: pass\n" + code},
        },
    }
    result = classify_payload(payload)
    assert (NODE in result["dropped"]) != (NODE in result["removed"])
    return "dropped" if NODE in result["dropped"] else "removed"


COLLISION = [
    (
        "class K:\n    def __init__(self): assert True\n"
        "class Z:\n    def __init__(self): pass\nK()\n",
        "class Z:\n    def __init__(self): pass\n",
    ),
    (
        "def helper(): assert True\nhelper()\nclass Z:\n    def helper(self): return 1\n",
        "class Z:\n    def helper(self): return 1\n",
    ),
]


@pytest.mark.parametrize("code, extra", COLLISION, ids=["init", "module-helper"])
def test_same_name_definition_cannot_shadow_a_changed_one(code, extra):
    assert _verdict(code, extra) == "dropped"


TK = "class K:\n    def target(self): assert True\n"
REACHED = [
    "class K:\n    def body(self): assert True\nK().body()\n",
    "class K:\n    @staticmethod\n    def body(): assert True\nK.body()\n",
    "def new_body(): assert True\nimport test_dynamic\ntest_dynamic.new_body()\n",
    "def new_body(): assert True\nfrom test_dynamic import new_body as nb\nnb()\n",
    "class K:\n    def __new__(cls): assert True\nK()\n",
    "class K:\n    def __call__(self): assert True\nK()()\n",
    "class K:\n    def __del__(self): assert True\nK()\n",
    "class K:\n    def __getitem__(self, i): assert True\nK()[0]\n",
    "class K:\n    def __iter__(self): assert True; return iter(())\nfor _ in K(): pass\n",
    "class K:\n    def __bool__(self): assert True; return True\nif K(): pass\n",
    "class K:\n    def __repr__(self): assert True; return ''\nrepr(K())\n",
    "class D:\n    def __get__(self, o, t): assert True\nclass K:\n    x = D()\nK.x\n",
    "class K:\n    def __class_getitem__(cls, i): assert True\nK[int]\n",
    "class M(type):\n    def __new__(m, n, b, d): assert True; return super().__new__(m, n, b, d)\n"
    "class K(metaclass=M): pass\n",
    "class M(type):\n    def __call__(cls): assert True\nclass K(metaclass=M): pass\nK()\n",
    "class W:\n    def __init__(self, f): assert True\n@W\ndef g(): pass\n",
    "class W:\n    def __call__(self, f): assert True; return f\n@W()\ndef g(): pass\n",
    "class K:\n    def __init__(self): assert True\nC = K\nC()\n",
    "def new_body(): assert True\n(lambda: 0).__globals__['new_body']()\n",
    "import inspect\ndef new_body(): assert True\ninspect.currentframe().f_locals['new_body']()\n",
    "class K:\n    def body(self): assert True\n"
    "list = lambda d: d['body'](None)\nlist(K.__dict__)\n",
    "from builtins import globals as g\ndef new_body(): assert True\ng()['new_body']()\n",
    "from builtins import eval as e\ndef new_body(): assert True\ne('new_body()')\n",
    "def new_body(): assert True\nf = lambda: new_body()\nf()\n",
    TK + "import operator\noperator.attrgetter('target')(K)()\n",
    TK + "from operator import attrgetter as ag\nag('target')(K)()\n",
    TK + "import operator\noperator.methodcaller('target')(K())\n",
    TK + "import functools\nfunctools.partial(getattr, K())('target')()\n",
    TK + "import functools\nfunctools.reduce(getattr, ['target'], K())()\n",
    TK + "vars(K)['target'](K())\n",
    TK + "K.__dict__['target'](K())\n",
    TK + "type.__getattribute__(K, 'target')(K())\n",
    TK + "import inspect\ninspect.getattr_static(K, 'target')(K())\n",
    TK + "import importlib\nimportlib.import_module(__name__).K().target()\n",
    "import threading\nclass Th(threading.Thread):\n    def run(self): assert True\nTh().start()\n",
    "import ast\nclass V(ast.NodeVisitor):\n    def visit_Module(self, n): assert True\n"
    "V().visit(ast.parse(''))\n",
    "import enum\nclass E(enum.Enum):\n    def _generate_next_value_(n, s, c, l): assert True\n"
    "    A = enum.auto()\n",
    "import enum\nclass E(enum.Enum):\n    A = 1\n    @classmethod\n"
    "    def _missing_(cls, v): assert True\nE(5)\n",
    "import json\nclass Enc(json.JSONEncoder):\n    def default(self, o): assert True\n"
    "json.dumps(object(), cls=Enc)\n",
    "import logging\nclass H(logging.Handler):\n    def emit(self, r): assert True\n"
    "logging.getLogger().addHandler(H())\n",
    "REG = []\n@REG.append\ndef new_body(): assert True\nREG[0]()\n",
    "import operator\n@operator.call\ndef new_body(): assert True\n",
    TK + "K.__getattribute__(K(), 'target')()\n",
    "class K:\n    def __init__(self): assert True\nif True:\n    K()\n",
    "class K:\n    def __init__(self): assert True\nfor _ in [0]:\n    K()\n",
    "class K:\n    def __init__(self): assert True\nwhile True:\n    K()\n    break\n",
    "class K:\n    def __init__(self): assert True\ntry:\n    pass\nfinally:\n    K()\n",
    "class K:\n    def __init__(self): assert True\n"
    "try:\n    pass\nexcept Exception:\n    pass\nelse:\n    K()\n",
]


@pytest.mark.parametrize("code", REACHED, ids=range(len(REACHED)))
def test_every_import_time_path_to_a_changed_definition_stays_dropped(code):
    assert _verdict(code) == "dropped"


HELPER = "import pytest\ndef is_ci(): return False\n"
HONEST = [
    (
        "import unittest\nclass TestN(unittest.TestCase):\n    def test_new(self): assert True\n",
        "",
    ),
    ("import threading\nLOCK = threading.Lock()\ndef test_new(): assert True\n", ""),
    (
        "class Helper:\n    def run(self): return 1\ndef test_new(): assert Helper().run() == 1\n",
        "",
    ),
    (
        "from pathlib import Path\nROOT = Path(__file__).parent\ndef test_new(): assert ROOT\n",
        "",
    ),
    ("import os\nROOT = os.__file__\nNAME = __name__\ndef test_new(): assert ROOT\n", ""),
    ("import re\nPATTERN = re.compile('x')\ndef test_new(): assert True\n", ""),
    ("class Box: pass\nNAMES = list(Box.__dict__)\ndef test_new(): assert True\n", ""),
    ("class K:\n    def run(self): return 1\ndef test_new(): assert True\n", ""),
    ("def helper(): return 1\ndef test_new(): assert helper()\n", ""),
    ("import pytest\n@pytest.fixture\ndef fx(): return 1\ndef test_new(fx): assert fx\n", ""),
    (
        HELPER + "pytestmark = pytest.mark.skipif(is_ci(), reason='x')\ndef test_new(): pass\n",
        HELPER,
    ),
    (
        "class K:\n    @property\n    def v(self): return 1\n    @v.setter\n"
        "    def v(self, x): pass\ndef test_new(): assert True\n",
        "class K:\n    @property\n    def v(self): return 1\n    @v.setter\n"
        "    def v(self, x): pass\n",
    ),
    ("def deferred(): return 1\nlater = lambda: deferred()\ndef test_new(): assert True\n", ""),
]


@pytest.mark.parametrize("code, extra", HONEST, ids=range(len(HONEST)))
def test_honest_changes_stay_removed(code, extra):
    assert _verdict(code, extra) == "removed"


NESTED = (
    "import threading\ndef f():\n    class Th(threading.Thread):\n"
    "        def run(self): pass\n    Th().start()\n"
)


def test_external_base_class_defined_inside_a_reached_function_exposes_its_methods():
    """The library may call ``Th.run``, so a changed ``run`` elsewhere cannot be ruled out."""
    assert _verdict(NESTED + "def run(): assert True\nf()\n", NESTED) == "dropped"
