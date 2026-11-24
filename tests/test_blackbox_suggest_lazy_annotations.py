"""tools/blackbox_suggest (#370): annotations are read as the whole module leaves them.

Python 3.14 evaluates a function's annotations lazily, when pytest reads its signature during
collection, so by then every later statement of the module has run. A name an annotation uses
that the module binds again further down (``from pkg.core import Box as list``) can then run
project code (``__class_getitem__``) before the tests. Such a module refuses.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

INI = "[pytest]\npythonpath = src\n"
CORE = (
    "SCALE = 1\ndef f(x):\n    return x * SCALE\nclass Box:\n"
    "    def __class_getitem__(cls, key):\n        global SCALE\n        SCALE = 10\n"
    "        return cls\n"
)
HEAD = "from pkg.core import f\n"
TAIL = "def test_a():\n    assert f(2) == 2\n"
BASE = {"pytest.ini": INI, "src/pkg/__init__.py": "", "src/pkg/core.py": CORE}


def run(tmp_path, body):
    for rel, text in (BASE | {"tests/test_x.py": HEAD + body + TAIL}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


LATER = "from pkg.core import Box as list\n"
REFUSED = {
    "argument": "def test_b(v: list[1] = 1):\n    pass\n" + LATER,
    "return": "def test_b() -> list[1]:\n    pass\n" + LATER,
    "method": "class TestB:\n    def test_b(self, v: list[1] = 1):\n        pass\n" + LATER,
    "assigned_later": (
        "from pkg.core import Box\ndef test_b(v: list[1] = 1):\n    pass\nlist = Box\n"
    ),
    "typing_name": (
        "from typing import List\ndef test_b(v: List[1] = 1):\n    pass\n"
        "from pkg.core import Box as List\n"
    ),
}


@pytest.mark.parametrize("body", REFUSED.values(), ids=REFUSED.keys())
def test_an_annotation_name_bound_again_later_refuses(tmp_path, body):
    item = run(tmp_path, body)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "builtin": "def test_b(v: list[int] = 1):\n    pass\n",
    "typing": "from typing import List\ndef test_b(v: List[int] = 1):\n    pass\n",
    "unrelated_import_later": (
        "def test_b(v: list[int] = 1):\n    pass\nfrom pkg.core import Box\n"
    ),
}


@pytest.mark.parametrize("body", KEPT.values(), ids=KEPT.keys())
def test_annotations_of_names_never_rebound_keep_the_draft(tmp_path, body):
    item = run(tmp_path, body)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
