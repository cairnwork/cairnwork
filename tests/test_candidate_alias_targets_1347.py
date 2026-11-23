"""A writer bound by a for, with, case, comprehension or parameter default is still a writer
(#1347). Every snippet puts a search-path edit next to a write the detector must see."""

from __future__ import annotations

import ast

import pytest

from cairnwork.candidate_alias import runtime_shadow_added
from cairnwork.candidate_alias_targets import target_binding_sources

SHAPES = {
    "for_tuple": "import shutil\nfor w in (shutil.copy,):\n    w('a', 'b')",
    "for_list": "import shutil\nfor w in [shutil.copy2]:\n    w('a', 'b')",
    "for_wrapped": "import shutil\nfor w in tuple(map(str, [shutil.move])):\n    w('a', 'b')",
    "async_for": (
        "import shutil\nasync def f():\n    async for w in gen(shutil.copy):\n        w('a', 'b')"
    ),
    "with_as": (
        "import shutil, contextlib\nwith contextlib.nullcontext(shutil.copy) as w:\n    w('a', 'b')"
    ),
    "async_with": (
        "import shutil\nasync def f():\n    async with ctx(shutil.copy) as w:\n        w('a', 'b')"
    ),
    "comprehension": "import shutil\n[w('a', 'b') for w in (shutil.copy,)]",
    "genexp": "import shutil\nlist(w('a', 'b') for w in [shutil.copy])",
    "match_subject": ("import shutil\nmatch shutil.copy:\n    case w:\n        w('a', 'b')"),
    "param_default": "import shutil\ndef f(w=shutil.copy):\n    w('a', 'b')\nf()",
    "kwonly_default": "import shutil\ndef f(*, w=shutil.copy):\n    w('a', 'b')\nf()",
    "lambda_default": "import shutil\n(lambda w=shutil.copy: w('a', 'b'))()",
    "nested_for": (
        "import shutil\ndef outer():\n    def inner():\n        for w in (shutil.copy,):\n"
        "            w('a', 'b')\n    inner()"
    ),
    "for_unpack": "import shutil\nfor (a, w) in [(1, shutil.copy)]:\n    w('a', 'b')",
    "for_star": "import shutil\nfor *a, w in [(1, shutil.copy)]:\n    w('a', 'b')",
    "for_dict_values": "import shutil\nfor w in {'k': shutil.copy}.values():\n    w('a', 'b')",
    "match_tuple": "import shutil\nmatch (shutil.copy,):\n    case (w,):\n        w('a', 'b')",
    "with_tuple_target": (
        "import shutil, contextlib\n"
        "with contextlib.nullcontext((shutil.copy,)) as (w,):\n    w('a', 'b')"
    ),
    "default_tuple": "import shutil\ndef f(w=(shutil.copy,)):\n    w[0]('a', 'b')\nf()",
    "lambda_kwonly": "import shutil\n(lambda *, w=shutil.copy: w('a', 'b'))()",
    "os_writers": "import os\nfor w in (os.rename, os.replace):\n    w('a', 'b')",
}
PATH_EDIT = "import sys\nsys.path.insert(0, 'deep')\n"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_writer_bound_by_a_target_is_flagged(shape):
    source = (PATH_EDIT + SHAPES[shape] + "\n").encode()
    assert runtime_shadow_added(None, source) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "for item in (1, 2):\n    print(item)",
        "import shutil\nfor item in names:\n    print(item)",
        "with open_reader() as handle:\n    handle.read()",
        "def f(value=3):\n    return value",
        "[x for x in range(3)]",
        "match command:\n    case other:\n        pass",
        "with open('x') as f:\n    f.read()",
        "with open('x', 'rb') as f, open('y') as g:\n    pass",
        "for line in open('x'):\n    print(line)",
        "[line for line in open('x')]",
        "with open('x'):\n    pass",
        "import shutil\nwith ctx(shutil.copy):\n    pass",
    ],
)
def test_binding_without_a_writer_is_not_flagged(snippet):
    source = (PATH_EDIT + snippet + "\n").encode()
    assert runtime_shadow_added(None, source) is False


def _with_item(code: str) -> ast.withitem:
    node = ast.parse(code).body[0]
    assert isinstance(node, ast.With)
    return node.items[0]


def test_a_with_context_without_as_binds_nothing():
    assert target_binding_sources(_with_item("with ctx(shutil.copy):\n    pass")) == []
    assert len(target_binding_sources(_with_item("with ctx(shutil.copy) as w:\n    pass"))) == 1
