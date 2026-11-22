"""tools/blackbox_suggest (#370): a target bound again where the reader did not look refuses.

The reader takes the last top-level def, class, import or plain assignment of a drafted target.
The name can also be bound again under if/try/for/with/match, by a walrus, a tuple target, an
import statement, an except or match name, a global statement in a function or class body, or a
star import after any binding of it, unless the star's module is read and surely does not bind
it. Then the name is not the def the reader read, so the target refuses.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

INI = "[pytest]\npythonpath = src\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"
WRAP = (
    "SCALE = 1\nclass W:\n    def __init__(self, fn):\n        self.fn = fn\n"
    "    def __call__(self, *a):\n        return self.fn(*a)\n"
    "    def __getattr__(self, name):\n        global SCALE\n        SCALE = 10\n"
    "        raise AttributeError(name)\n"
)
PLAIN = "def f(x):\n    return x * 2 * SCALE\n"
BASE = {"pytest.ini": INI, "src/pkg/__init__.py": "", "tests/test_x.py": TEST}


def run(tmp_path, files):
    for rel, body in (BASE | files).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def core(tail: str) -> dict[str, str]:
    return {"src/pkg/core.py": WRAP + PLAIN + tail}


REFUSED = {
    "under_if": core("if True:\n    f = W(f)\n"),
    "under_try": core("try:\n    f = W(f)\nexcept Exception:\n    pass\n"),
    "under_for": core("for _ in [0]:\n    f = W(f)\n"),
    "under_while": core("while SCALE == 1:\n    f = W(f)\n    break\n"),
    "under_with": core("import contextlib\nwith contextlib.nullcontext():\n    f = W(f)\n"),
    "under_match": core("match 0:\n    case 0:\n        f = W(f)\n"),
    "walrus": core("(f := W(f))\n"),
    "walrus_in_comprehension": core("[(f := W(f)) for _ in [0]]\n"),
    "walrus_in_default": core("def g(x=(f := W(f))):\n    return x\n"),
    "tuple_target": core("f, _ = W(f), 0\n"),
    "for_target": core("for f in [W(f)]:\n    pass\n"),
    "with_target": core("import contextlib\nwith contextlib.nullcontext(W(f)) as f:\n    pass\n"),
    "match_capture": core("match W(f):\n    case f:\n        pass\n"),
    "augmented": core("f += 0\n"),
    "import_as": core("import json as f\n"),
    "star_after": {
        "src/pkg/core.py": WRAP + PLAIN + "from pkg.impl import *\n",
        "src/pkg/impl.py": WRAP + "f = W(lambda x: x * 2 * SCALE)\n",
    },
    "global_in_function": core("def setup():\n    global f\n    f = W(f)\nsetup()\n"),
    "global_in_class": core("class K:\n    global f\n    f = W(f)\n"),
    "except_name": core("try:\n    raise ValueError\nexcept ValueError as f:\n    pass\n"),
    "deleted": core("del f\n"),
    "match_star": core("match [W(f)]:\n    case [*f]:\n        pass\n"),
    "match_rest": core("match {0: W(f)}:\n    case {**f}:\n        pass\n"),
    "import_plain": core("import f\n") | {"src/f.py": ""},
    "star_after_import": {
        "src/pkg/core.py": "from pkg.a import f\nfrom pkg.impl import *\n",
        "src/pkg/a.py": "SCALE = 1\n" + PLAIN,
        "src/pkg/impl.py": WRAP + "f = W(lambda x: x * 2 * SCALE)\n",
    },
    "star_after_alias": {
        "src/pkg/core.py": WRAP
        + "def h(x):\n    return x * 2 * SCALE\nf = h\nfrom pkg.impl import *\n",
        "src/pkg/impl.py": WRAP + "f = W(lambda x: x * 2 * SCALE)\n",
    },
    "star_outside_project": core("from json import *\n"),
    "star_names_it": core("from pkg.impl import *\n") | {"src/pkg/impl.py": '__all__ = ["f"]\n'},
    "star_of_a_package_with_it": core("from pkg.impl import *\n")
    | {"src/pkg/impl/__init__.py": "from . import f as _f\n", "src/pkg/impl/f.py": ""},
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_target_bound_again_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain": core(""),
    "local_in_function": core("def g():\n    f = W(1)\n    return f\n"),
    "method_in_class": core("class K:\n    def f(self):\n        return 1\n"),
    "comprehension_target": core("ALL = [f for f in [1, 2]]\n"),
    "lambda_argument": core("g = lambda f: f\n"),
    "star_before": {
        "src/pkg/core.py": "from pkg.impl import *\n" + WRAP + PLAIN,
        "src/pkg/impl.py": "def f(x):\n    return x\n",
    },
    "star_only": {
        "src/pkg/core.py": "from pkg.impl import *\n",
        "src/pkg/impl.py": "SCALE = 1\n" + PLAIN,
    },
    "other_name_nested": core("if True:\n    g = W(f)\n"),
    "alias_to_def": {"src/pkg/core.py": WRAP + "def h(x):\n    return x * 2 * SCALE\nf = h\n"},
    "star_after_def_lacking_it": core("from pkg.impl import *\n") | {"src/pkg/impl.py": "G = 1\n"},
    "star_after_import_lacking_it": {
        "src/pkg/core.py": "from pkg.a import f\nfrom pkg.impl import *\n",
        "src/pkg/a.py": "SCALE = 1\n" + PLAIN,
        "src/pkg/impl.py": "G = 1\n",
    },
}


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_a_target_bound_once_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
