"""tools/blackbox_suggest (#320 FU1-FU3): what a test module runs when pytest imports it.

A module stays inert only if its class bases, module-level values and ``if TYPE_CHECKING:``
guards run no project code. Each refused case below can run project code at import time that the
blackbox runner would not run; each control must keep its draft.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = (
    "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
    "REG = {}\nclass Registered:\n    def __init_subclass__(cls, **kw):\n"
    "        REG[cls.__name__] = cls\n"
    "def __getattr__(name):\n    global SCALE\n    SCALE = 10\n    return 1\n"
)
PLAIN = "def test_a():\n    assert f(2) == 2\n"


def run(tmp_path, body):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "src/pkg/extra.py": "import pkg.core\npkg.core.SCALE = 10\n",
        "tests/test_x.py": textwrap.dedent(body),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f, Registered\n"


@pytest.mark.parametrize(
    "body",
    [
        "R = Registered\nclass Mine(R):\n    pass\n",
        "from pkg.core import Registered as R\nclass Mine(R):\n    pass\n",
        "class Mine(Registered):\n    pass\n",
        "class Mine(core.Registered):\n    pass\n",
        "R = object\nclass Mine(R):\n    pass\n",
        "import enum\nclass Mine(enum.Enum):\n    A = 1\n",
        "from pkg.core import *\nTestCase = Registered\nclass Mine(TestCase):\n    pass\n",
    ],
)
def test_fu1_a_base_not_defined_in_the_file_refuses(tmp_path, body):
    item = run(tmp_path, HEAD + body + PLAIN)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize(
    "body",
    [
        "class Base:\n    pass\nclass Mine(Base, object):\n    pass\n",
        "import unittest\nclass TestU(unittest.TestCase):\n    pass\n",
        "from unittest import TestCase\nclass TestU(TestCase):\n    pass\n",
    ],
)
def test_fu1_bases_in_the_file_object_and_testcase_keep_the_draft(tmp_path, body):
    item = run(tmp_path, HEAD + body + PLAIN)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


@pytest.mark.parametrize(
    "body",
    [
        "X = core.LAZY\n",
        "import pkg.core as alias\nX = alias.LAZY\n",
        "X = [core.LAZY]\n",
        "X = core.TABLE[0]\n",
        "X = -core.N\n",
        "X = {**core.TABLE}\n",
        "X = [*core.ITEMS]\n",
        'X = f"{core.N}"\n',
        "X = core.N == 1\n",
        "X = Registered\n",
        "X: core.T = 1\n",
        'SKIP = pytest.mark.skipif(core.LAZY, reason="r")\n',
        "CASES = [pytest.param(core.LAZY)]\n",
        "class Helper:\n    X = core.LAZY\n",
        'import importlib\nM = importlib.import_module("pkg.extra")\n',  # refused before too
        'M = __import__("pkg.extra")\n',  # refused before too
    ],
)
def test_fu2_a_module_value_that_is_not_a_literal_refuses_its_siblings(tmp_path, body):
    item = run(tmp_path, HEAD + body + PLAIN)  # test_a never reads X
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_fu2_literals_names_bound_to_them_and_marks_keep_the_draft(tmp_path):
    body = (
        'X = 1\nY = X\nZ: int = -2\nD = {"a": (1, None), "b": [Y, 2.5]}\nS = {1, "x"}\n'
        "XF = pytest.mark.xfail\nCASES = [pytest.param(1, marks=XF), (2, 2)]\n"
        "class Helper:\n    W = [X]\n"
    )
    item = run(tmp_path, HEAD + body + "def test_a():\n    assert f(1) == X\n")
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


@pytest.mark.parametrize(
    "flag",
    [
        "TYPE_CHECKING = True\n",
        "TYPE_CHECKING = False\n",
        "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\n",
        "TYPE_CHECKING = True\nclass Helper:\n    from typing import TYPE_CHECKING\n",
        "from typing import TYPE_CHECKING\ndef TYPE_CHECKING():\n    pass\n",
        "from pkg.core import SCALE as TYPE_CHECKING\n",
        "from typing import TYPE_CHECKING\nfrom pkg.core import *\n",
        "class typing:\n    TYPE_CHECKING = True\n",
    ],
)
def test_fu3_a_type_checking_flag_not_from_typing_refuses(tmp_path, flag):
    guard = "if typing.TYPE_CHECKING:\n" if "class typing" in flag else "if TYPE_CHECKING:\n"
    for imported in ("    import pkg.extra\n", "    from pkg import extra\n"):
        item = run(tmp_path / str(len(imported)), HEAD + flag + guard + imported + PLAIN)
        assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize(
    "flag",
    [
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n",
        "import typing\nif typing.TYPE_CHECKING:\n",
    ],
)
def test_fu3_type_checking_from_typing_keeps_the_draft(tmp_path, flag):
    item = run(tmp_path, HEAD + flag + "    import pkg.extra\n" + PLAIN)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
