"""tools/blackbox_suggest (#370): code built from text or swapped into a function refuses.

A module the drafted test imports can build code from source text with the compile builtin, or
replace what a def runs (its code object, defaults, closure cells or the wrapped function and
signature that introspection follows). The def the reader read is then not what runs, so any
mention of these refuses, as exec and eval already do.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

INI = "[pytest]\npythonpath = src\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"
PLAIN = "def f(x):\n    return x * 2\n"
BASE = {"pytest.ini": INI, "src/pkg/__init__.py": "", "tests/test_x.py": TEST}


def run(tmp_path, files):
    for rel, body in (BASE | files).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def core(tail: str) -> dict[str, str]:
    return {"src/pkg/core.py": PLAIN + tail}


LAMBDA = "(lambda x: x).__code__"
REFUSED = {
    "code_from_text": core(
        'f.__code__ = compile("def f(x):\\n    return x", "s", "exec").co_consts[0]\n'
    ),
    "code_from_lambda": core(f"f.__code__ = {LAMBDA}\n"),
    "code_replaced": core("f.__code__ = f.__code__.replace(co_consts=(None, 3))\n"),
    "code_through_alias": core(f"g = f\ng.__code__ = {LAMBDA}\n"),
    "code_in_function": core(f"def s():\n    f.__code__ = {LAMBDA}\ns()\n"),
    "code_as_string": core(f'setattr(f, "__code__", {LAMBDA})\n'),
    "defaults": core("def h(x, k=1):\n    return x\nf.__defaults__ = ()\n"),
    "keyword_defaults": core("f.__kwdefaults__ = {}\n"),
    "closure_cell": core(
        "def m(v):\n    def f(x):\n        return x * v\n    return f\nc = m(2).__closure__\n"
    ),
    "cell_contents": core("def k(c):\n    c.cell_contents = 3\n"),
    "wrapped": core("def h(x):\n    return x\nh.__wrapped__ = f\n"),
    "signature": core("f.__signature__ = None\n"),
    "compile_alone": core('C = compile("x * 3", "s", "eval")\n'),
    "compile_other_module": {
        "src/pkg/core.py": "import pkg.side\n" + PLAIN,
        "src/pkg/side.py": 'C = compile("x", "s", "eval")\n',
    },
}


@pytest.mark.parametrize("files", REFUSED.values(), ids=REFUSED.keys())
def test_code_built_or_swapped_refuses(tmp_path, files):
    item = run(tmp_path, files)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain": core(""),
    "regex_compile": core("import re\nPAT = re.compile('a+')\n"),
    "a_name_like_it": core("compiled = 1\ncode = 2\ndefaults = {}\n"),
    "method_called_compile": core("class K:\n    def compile(self):\n        return 1\n"),
}


@pytest.mark.parametrize("files", KEPT.values(), ids=KEPT.keys())
def test_plain_code_keeps_the_draft(tmp_path, files):
    item = run(tmp_path, files)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
