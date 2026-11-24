"""Pin external-base class shapes around the import-time reachability rule (Refs #1745).

A test-prefixed method of an external-base class is not a library hook, so a
module-level function of the same name stays unreached.
"""

import ast

import pytest

from cairnwork.p2p_import_time import _import_time_uses_changed as uses

NESTED = (
    "import threading\ndef f():\n    class Th(threading.Thread):\n"
    "        def run(self): pass\n    Th().start()\n"
)
HOLD = "import lib\nclass H(lib.Base):\n"
CASES = {
    "ext_base_method_uses_changed": (
        HOLD + "    pass\nH\n",
        "import lib\ndef new_body(): pass\nclass H(lib.Base):\n"
        "    def run(self): return new_body()\nH\n",
        True,
    ),
    "ext_base_method_aliases_module_fn": (
        "def run(): return 1\n" + HOLD + "    def run(self): pass\nH\n",
        "def new_body(): pass\ndef run(): return new_body()\n"
        + HOLD
        + "    def run(self): pass\nH\n",
        True,
    ),
    "ext_base_test_prefixed_alias": (
        "def test_zz(): return 1\n" + HOLD + "    def test_zz(self): pass\nH\n",
        "def new_body(): pass\ndef test_zz(): return new_body()\n"
        + HOLD
        + "    def test_zz(self): pass\nH\n",
        False,
    ),
    "dunder_uses_changed": (
        "class H:\n    pass\nH\n",
        "def new_body(): pass\nclass H:\n    def __init__(self): new_body()\nH()\n",
        True,
    ),
    "control_unreferenced_class": (
        "class H:\n    pass\n",
        "def new_body(): pass\nclass H:\n    def run(self): return new_body()\n",
        False,
    ),
    "local_class_method_only_named": (
        "class H:\n    def run(self): return 1\nH()\n",
        "class H:\n    def run(self): return 2\nH()\n",
        False,
    ),
    "ext_base_class_in_reached_function": (
        NESTED + "f()\n",
        NESTED + "def run(): assert True\nf()\n",
        True,
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_external_base_shapes(name):
    base, test, want = CASES[name]
    assert uses(ast.parse(base), ast.parse(test)) is want
