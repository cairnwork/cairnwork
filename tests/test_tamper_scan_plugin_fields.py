"""#848: ``hook_names_bound`` reads every identifier field of the AST, on every Python version.

One minimal shape per (node class, field). On Python 3.13+ the list of identifier fields is read
from ``ast`` itself, so a binding form added later turns ``test_every_identifier_field_has_a_shape``
red until a shape is added here.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork.tamper_scan_plugin import HOOK_NAMES
from cairnwork.tamper_scan_walk import hook_names_bound

H = "pytest_pyfunc_call"
SHAPES = {  # (class, field): (source, first Python minor version that parses it)
    ("Name", "id"): (f"{H} = 1\n", 0),
    ("arg", "arg"): (f"def f({H}=1): pass\n", 0),
    ("arg", "arg:kwonly"): (f"def f(*, {H}): pass\n", 0),
    ("arg", "arg:lambda"): (f"lambda {H}: 0\n", 0),
    ("keyword", "arg"): (f"f({H}=1)\n", 0),
    ("alias", "name"): (f"import {H}\n", 0),
    ("alias", "asname"): (f"import x as {H}\n", 0),
    ("FunctionDef", "name"): (f"def {H}(): pass\n", 0),
    ("AsyncFunctionDef", "name"): (f"async def {H}(): pass\n", 0),
    ("ClassDef", "name"): (f"class {H}: pass\n", 0),
    ("Global", "names"): (f"global {H}\n", 0),
    ("Nonlocal", "names"): (f"nonlocal {H}\n", 0),
    ("ExceptHandler", "name"): (f"try:\n    pass\nexcept E as {H}:\n    pass\n", 0),
    ("ImportFrom", "module"): (f"from {H} import x\n", 0),
    ("Attribute", "attr"): (f"x.{H}\n", 0),
    ("MatchAs", "name"): (f"match 1:\n    case {H}:\n        pass\n", 0),
    ("MatchStar", "name"): (f"match 1:\n    case [*{H}]:\n        pass\n", 0),
    ("MatchMapping", "rest"): (f"match 1:\n    case {{**{H}}}:\n        pass\n", 0),
    ("TypeVar", "name"): (f"def f[{H}](): pass\n", 12),
    ("ParamSpec", "name"): (f"def f[**{H}](): pass\n", 12),
    ("TypeVarTuple", "name"): (f"def f[*{H}](): pass\n", 12),
    ("MatchClass", "kwd_attrs"): (f"match 1:\n    case C({H}=1):\n        pass\n", 0),
    ("Constant", "value"): (f"x = '{H}'\n", 0),
    ("Constant", "value:bytes"): (f"x = b'{H}'\n", 0),
}
SKIP = {
    "type_comment",
    "kind",
    "tag",
}  # TypeIgnore.tag: only with type_comments=True  # strings, but not names


def _usable():
    return {k: v[0] for k, v in SHAPES.items() if sys.version_info >= (3, v[1])}


@pytest.mark.parametrize("key", sorted(_usable()))
def test_each_identifier_field_is_read(key):
    assert hook_names_bound(_usable()[key].encode(), HOOK_NAMES) == {H}


def test_the_least_shape_from_the_review_is_read():
    assert hook_names_bound(f"def _mk({H}=1): return locals()\n".encode(), HOOK_NAMES) == {H}


def test_other_names_are_not_read():
    assert hook_names_bound(b"def f(a, *, b=1): return [c for c in a]\n", HOOK_NAMES) == set()


def _identifier_fields():
    """(class, field) pairs typed ``str`` or ``list[str]`` by ``ast`` itself (3.13+)."""
    out = set()
    for name, cls in vars(ast).items():
        types = getattr(cls, "_field_types", None) if isinstance(cls, type) else None
        for field, kind in (types or {}).items():
            if (
                field not in SKIP
                and str(kind) in ("<class 'str'>", "list[str]")
                and name != "Constant"
            ):
                out.add((name, field))
    return out


@pytest.mark.skipif(sys.version_info < (3, 13), reason="ast has no _field_types before 3.13")
def test_every_identifier_field_has_a_shape():
    covered = {(c, f.split(":")[0]) for c, f in _usable()}
    assert _identifier_fields() <= covered, _identifier_fields() - covered


@pytest.mark.parametrize("minor", ["3.11", "3.12", "3.13"])
def test_the_shapes_hold_on_each_python(minor):
    """Run the same table under 3.11, 3.12 and 3.13 where they are installed."""
    exe = Path.home() / ".local" / "bin" / f"python{minor}"
    if not exe.exists():
        pytest.skip(f"no python{minor}")
    src = str(Path(__file__).parent.parent / "src")
    code = (
        "import sys, importlib.util\n"
        f"sys.path.insert(0, {src!r})\n"
        "spec = importlib.util.spec_from_file_location('plugin', "
        f"{src + '/cairnwork/tamper_scan_walk.py'!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['plugin'] = m\n"
        "spec.loader.exec_module(m)\n"
        f"shapes = {_shapes_for(minor)!r}\n"
        f"names = {set(HOOK_NAMES)!r}\n"
        f"want = {{{H!r}}}\n"
        "bad = [k for k, s in shapes.items() if m.hook_names_bound(s.encode(), names) != want]\n"
        "print(bad)\n"
    )
    done = subprocess.run([str(exe), "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "[]", done.stderr or done.stdout


def _shapes_for(minor):
    need = int(minor.split(".")[1])
    return {f"{k[0]}.{k[1]}": v[0] for k, v in SHAPES.items() if need >= v[1]}


def test_hook_names_is_not_empty():
    assert H in HOOK_NAMES
