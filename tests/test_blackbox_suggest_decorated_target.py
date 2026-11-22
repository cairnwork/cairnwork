"""tools/blackbox_suggest (#370): a decorated target is what its decorator returns.

A decorator binds the name to whatever it returns, not to the def or class the reader reads.
That object can define ``__getattr__``, which pytest runs when it looks the imported name over
in the test module during collection, so its code runs before the test. Such a target refuses.
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


REFUSED = {
    "decorated_function": {"src/pkg/core.py": WRAP + "@W\n" + PLAIN},
    "decorated_async": {"src/pkg/core.py": WRAP + "@W\nasync " + PLAIN},
    "decorated_class": {
        "src/pkg/core.py": WRAP
        + "@W\nclass f:\n    def __new__(cls, x):\n        return x * 2 * SCALE\n"
    },
    "decorator_call": {"src/pkg/core.py": WRAP + "def deco():\n    return W\n@deco()\n" + PLAIN},
    "re_exported": {
        "src/pkg/impl.py": WRAP + "@W\n" + PLAIN,
        "src/pkg/core.py": "from pkg.impl import f\n",
    },
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_a_decorated_target_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain": {"src/pkg/core.py": "SCALE = 1\n" + PLAIN},
    "other_function_decorated": {"src/pkg/core.py": WRAP + PLAIN + "@W\ndef g(x):\n    return x\n"},
}


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_an_undecorated_target_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
