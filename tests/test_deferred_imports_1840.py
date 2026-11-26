"""#1840 head4: an import-bound base is never read more narrowly than the cautious reading.

The cautious reading (a base that is not import-bound may be any earlier class of the file) is
what main does. A file that rebinds the imported base name through any channel the AST does not
show as a binding must keep it, so the ids on this head are a superset of main's. A list of such
channels cannot be complete, so the second group pins the superset property over many shapes."""

import ast

import pytest

from cairnwork import deferred_imports, deferred_nodes
from cairnwork.deferred_nodes import test_functions as functions_of

HEAD = "import sys\nimport unittest\nimport pytest\nfrom unittest import mock\n"
P = "class P(unittest.TestCase):\n    def test_p(self):\n        pass\n\n"
IMP = "from helpers import Base\n"
TAIL = "\n\nclass I(Base):\n    def test_i(self):\n        pass\n"


def _ids(src: str) -> set[str]:
    return set(functions_of(ast.parse(src))[0])


def _cautious(src: str, monkeypatch) -> set[str]:
    with monkeypatch.context() as m:
        m.setattr(deferred_nodes, "imported_names", lambda tree: frozenset())
        return _ids(src)


# (before the import, after the import): the seven shapes of the review, then more of the family
BEFORE_IMPORT = {
    "a-module-attr-before-import": "import helpers\nhelpers.Base = P\n",
}
AFTER_IMPORT = {
    "b-globals-subscript": "def _f():\n    pass\n\n\n_f.__globals__['Base'] = P\n",
    "c-getframe": "sys._getframe().f_globals['Base'] = P\n",
    "d-modules-name": "from sys import modules\nmodules[__name__].Base = P\n",
    "e-self-import": "import test_self as me\nme.Base = P\n",
    "f-monkeypatch": "pytest.MonkeyPatch().setattr('test_self.Base', P)\n",
    "g-mock-patch-start": "mock.patch('test_self.Base', P).start()\n",
    "currentframe": "import inspect\ninspect.currentframe().f_globals['Base'] = P\n",
    "frame-update": "sys._getframe().f_globals.update(Base=P)\n",
    "mock-patch-object": "import helpers\nmock.patch.object(helpers, 'Base', P).start()\n",
    "monkeypatch-fixture": (
        "def test_mp(monkeypatch):\n    monkeypatch.setattr('helpers.Base', P)\n"
    ),
    "string-name-only": "NAMES = ['Base']\n",
    "dotted-string": "TARGET = 'test_self.Base'\n",
    "module-attr-after": "import helpers\nhelpers.Base = P\n",
    "module-dict-write": "import helpers\nhelpers.__dict__['Base'] = P\n",
    "builtins-setattr": "import builtins\nbuiltins.setattr(sys, 'x', 1)\n",
    "attr-store-same-name": "class NS:\n    pass\n\n\nNS.Base = P\n",
    "reload": "import importlib\nimportlib.reload(sys)\n",
    "type-call-dict": "ns = vars(sys.modules['test_self'])\n",
    "types-module": "import types\ntypes.SimpleNamespace(Base=P)\n",
    "delattr": "delattr(sys, 'Base')\n",
}
ALL = {
    **{k: ("before", v) for k, v in BEFORE_IMPORT.items()},
    **{k: ("after", v) for k, v in AFTER_IMPORT.items()},
}


def _file(kind: str, body: str) -> str:
    if kind == "before":
        return HEAD + P + body + IMP + TAIL
    return HEAD + P + IMP + body + TAIL


@pytest.mark.parametrize("name", ALL)
def test_every_rebinding_channel_keeps_main_s_ids(name, monkeypatch):
    src = _file(*ALL[name])
    assert "I::test_p" in _ids(src)
    assert _ids(src) >= _cautious(src, monkeypatch)


MUST_PASS = {
    "read": "print(Base)\n",
    "attribute-assign": "Base.attr = 1\n",
    "alias": "Alias = Base\n",
    "plain-mock-of-other": "mock.patch('os.getcwd').start()\n",
    "unrelated-string": "LABEL = 'hello'\n",
    "other-module-attr": "import os\nos.environ['X'] = '1'\n",
}


@pytest.mark.parametrize("name", MUST_PASS)
def test_a_name_that_is_only_read_or_aliased_stays_import_bound(name):
    ids = _ids(HEAD + P + IMP + MUST_PASS[name] + TAIL)
    assert "I::test_p" not in ids and "I::test_i" in ids


@pytest.mark.parametrize("name", ALL)
def test_the_property_ids_on_head_contain_main_s_ids_for_every_shape(name, monkeypatch):
    kind, body = ALL[name]
    for base in ("Base", "Base2", "hs.Base"):
        imp = {
            "Base": IMP,
            "Base2": "from helpers import Base as Base2\n",
            "hs.Base": "import helpers as hs\n",
        }[base]
        tail = TAIL.replace("(Base)", f"({base})")
        src = HEAD + P + (body + imp if kind == "before" else imp + body) + tail
        assert _ids(src) >= _cautious(src, monkeypatch)


@pytest.mark.parametrize(
    "trigger",
    ["globals", "vars", "locals", "setattr", "exec", "eval", "__import__", "importlib"],
)
def test_each_listed_name_trigger_alone_falls_back(trigger):
    src = HEAD + P + IMP + f"{trigger}\n" + TAIL
    assert "I::test_p" in _ids(src)


@pytest.mark.parametrize("attr", ["modules", "__dict__", "import_module"])
def test_each_listed_attribute_trigger_alone_falls_back(attr):
    src = HEAD + P + IMP + f"x = sys.{attr}\n" + TAIL
    assert "I::test_p" in _ids(src)


def test_a_lone_import_inside_a_block_is_not_import_bound():
    src = HEAD + P + "if True:\n    from helpers import Base\n" + TAIL
    assert "I::test_p" in _ids(src)


def test_the_trigger_set_is_documented_as_not_exhaustive():
    assert "not exhaustive" in (deferred_imports.__doc__ or "") + (
        deferred_imports.imported_names.__doc__ or ""
    )
