"""tools/blackbox_suggest (#303): which tests become draft blackbox cases, and why the rest cannot.

Every project here is written to tmp_path and read statically; the import-side-effect tests pin
that neither the project nor the tests are ever imported or run.
"""

import sys
import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

PKG = {
    "src/pkg/__init__.py": "from .core import add, Stack\nfrom .errors import *\n",
    "src/pkg/errors.py": "class BadInput(ValueError):\n    pass\n",
    "src/pkg/core.py": textwrap.dedent(
        """
        def add(a, b):
            return a + b
        def half(x):
            return x / 2
        alias = add
        wrapped = staticmethod(add)
        class Stack:
            def __init__(self, items):
                self.items = list(items)
            def top(self):
                return self.items[-1]
        """
    ),
    "src/pkg/sub/__init__.py": "",
    "src/pkg/sub/deep.py": "from ..core import add as plus\nfrom . import other\n",
    "src/pkg/sub/other.py": "def f():\n    return 1\n",
}
HEADER = (
    "import pytest\nfrom pkg import add, Stack, BadInput\n"
    "from pkg.core import half, alias, wrapped\n"
)


def project(tmp_path, test_body, extra=None):
    files = dict(PKG, **(extra or {}))
    files["tests/test_x.py"] = HEADER + textwrap.dedent(test_body)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))


def one(tmp_path, body, extra=None):
    found = project(tmp_path, body, extra)
    assert len(found) == 1, [o.test_id for o in found]
    return found[0]


def refused(tmp_path, body, extra=None):
    item = one(tmp_path, body, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW | {"float", "method"})
    return item.refusal


def test_function_case_with_args_kwargs_and_module_constant(tmp_path):
    item = one(
        tmp_path,
        """
        EXPECTED = {"a": [1, None, "x"]}
        def test_it():
            '''doc'''
            assert add({"a": [1]}, b=[None, "x"]) == EXPECTED
            assert -3 == add(-1, -2)
            assert add(0, 0) is None
    """,
    )
    assert item.protected(KINDS_NOW) and item.needs == {"function"} and item.exact
    first, second, third = item.cases
    assert first["target"] == {"module": "pkg", "function": "add"}
    assert first["args"] == [{"a": [1]}] and first["kwargs"] == {"b": [None, "x"]}
    assert first["expected"] == {"value": {"a": [1, None, "x"]}, "compare": "exact"}
    assert second["args"] == [-1, -2] and second["expected"]["value"] == -3
    assert third["expected"] == {"value": None, "compare": "exact"}
    assert item.test_id == "tests/test_x.py::test_it" and first["line"] == 8


def test_parametrize_counts_every_case_and_binds_values(tmp_path):
    found = project(
        tmp_path,
        """
        XF = pytest.mark.xfail
        @pytest.mark.parametrize("a,b,out", [(1, 2, 3), pytest.param(2, 2, 4, marks=XF), [0, 0, 0]])
        @pytest.mark.parametrize("flag", [True, False])
        def test_table(a, b, out, flag):
            assert add(a, b) == out
    """,
    )  # noqa: E501
    assert len(found) == 6 and all(o.exact for o in found)
    assert [o.test_id.rsplit("::", 1)[1] for o in found][:2] == ["test_table[0]", "test_table[1]"]
    assert [o.protected(KINDS_NOW) for o in found] == [True, True, False, False, True, True]
    assert "marks" in (found[2].refusal or "")
    assert found[4].cases[0]["args"] == [0, 0] and found[4].cases[0]["expected"]["value"] == 0


def test_non_static_parametrize_counts_once_and_says_the_count_is_a_lower_bound(tmp_path):
    item = one(
        tmp_path,
        """
        @pytest.mark.parametrize("v", make_values())
        def test_v(v):
            assert add(v, 0) == v
    """,
    )
    assert not item.exact and "not static" in (item.refusal or "")


def test_parametrize_values_from_a_module_list_are_counted(tmp_path):
    found = project(
        tmp_path,
        """
        CASES = [(1, 1), (2, 2), (3, 3)]
        @pytest.mark.parametrize("a,out", CASES)
        def test_v(a, out):
            assert add(a, 0) == out
    """,
    )
    assert len(found) == 3 and all(o.protected(KINDS_NOW) for o in found)


def test_fixture_params_multiply_the_count(tmp_path):
    found = project(
        tmp_path,
        """
        @pytest.fixture(params=[1, 2, 3])
        def num(request):
            return request.param
        def test_v(num):
            assert add(num, 0) == num
    """,
    )
    assert len(found) == 3 and all("fixture" in (o.refusal or "") for o in found)


@pytest.mark.parametrize(
    "body, reason",
    [
        ("def test_v(tmp_path):\n    assert add(1, 1) == 2\n", "uses fixture(s): tmp_path"),
        (
            "@pytest.mark.skipif(True, reason='x')\ndef test_v():\n    assert add(1, 1) == 2\n",
            "marked skipif",
        ),
        (
            "@given(1)\ndef test_v():\n    assert add(1, 1) == 2\n",
            "module-level decorator runs at import time",
        ),
        ("async def test_v():\n    assert add(1, 1) == 2\n", "async test"),
        ("def test_v():\n    assert add([1], [2]) == (1, 2)\n", "tuple"),
        ("def test_v():\n    assert add((1,), [2]) == [1, 2]\n", "tuple"),
        ("def test_v():\n    assert add({1: 2}, 0) == 1\n", "non-string dict key"),
        ("def test_v():\n    assert add(b'x', b'y') == 1\n", "bytes"),
        ("def test_v():\n    assert add(1, 1)\n", "single comparison"),
        ("def test_v():\n    assert add(1, 1) != 3\n", "NotEq"),
        ("def test_v():\n    assert add(1, 1) == add(2, 0)\n", "not a literal"),
        ("def test_v():\n    assert len([1]) == 1\n", "not imported"),
        ("def h(x):\n    return x\ndef test_v():\n    assert h(1) == 1\n", "rebound in the test"),
        ("import json\ndef test_v():\n    assert json.dumps(1) == '1'\n", "not a module of the"),
        ("def test_v():\n    r = add(1, 1)\n    assert add(2, 2) == 4\n", "never checked"),
        ("def test_v():\n    add(1, 1)\n    assert add(2, 2) == 4\n", "side effect"),
        ("def test_v():\n    for i in [1]:\n        assert add(i, 0) == i\n", "For is not a check"),
        ("def test_v():\n    x = 1\n    x = 2\n    assert add(x, 0) == 2\n", "bound twice"),
        ("def test_v():\n    assert add(n, 0) == 1\n", "not bound to a literal"),
        ("def test_v():\n    assert add(1, 1) == f'{2}'\n", "f-string"),
        ("def test_v():\n    assert add(1, 0) == float('nan')\n", "not a literal"),
        (
            "def test_v():\n    with pytest.raises(ValueError, match='x'):\n        add(1, 2)\n",
            "match=",
        ),
        (
            "def test_v():\n    with pytest.raises((ValueError, TypeError)):\n        add(1, 2)\n",
            "several exception types",
        ),
        (
            "def test_v():\n    with pytest.raises(ValueError):\n"
            "        add(1, 2)\n        add(2, 3)\n",
            "more than one statement",
        ),
        ("def test_v():\n    assert wrapped(1, 2) == 3\n", "not a plain def or class"),
        ("def test_v():\n    s = Stack([1])\n    assert add(1, 1) == 2\n", "no method of it"),
        ("def test_v():\n    pass\n", "no assertion"),
    ],
)
def test_refusals_carry_a_reason(tmp_path, body, reason):
    assert reason in refused(tmp_path, body)


def test_autouse_fixture_in_a_conftest_refuses_every_test(tmp_path):
    conftest = "import pytest\n@pytest.fixture(autouse=True)\ndef reset():\n    yield\n"
    reason = refused(
        tmp_path,
        "def test_v():\n    assert add(1, 1) == 2\n",
        {"tests/conftest.py": conftest},
    )
    assert reason == "autouse fixture reset is in effect"


def test_class_tests_and_setup_hooks(tmp_path):
    found = project(
        tmp_path,
        """
        class TestPlain:
            def test_a(self):
                assert add(1, 2) == 3
        class TestHooked:
            def setup_method(self):
                pass
            def test_b(self):
                assert add(1, 2) == 3
        class NotATest:
            def test_c(self):
                assert add(1, 2) == 3
    """,
    )
    assert [o.test_id.split("::", 1)[1] for o in found] == [
        "TestPlain::test_a",
        "TestHooked::test_b",
    ]
    assert found[0].protected(KINDS_NOW) and "setup_method" in (found[1].refusal or "")


def test_unittest_assertions(tmp_path):
    found = project(
        tmp_path,
        """
        import unittest
        class Case(unittest.TestCase):
            def test_eq(self):
                self.assertEqual(add(1, 2), 3)
                self.assertIsNone(add(None, None))
            def test_raises(self):
                self.assertRaises(TypeError, add, 1, "x")
            def test_true(self):
                self.assertTrue(add(1, 2))
    """,
    )
    eq, raises, true = found
    assert eq.protected(KINDS_NOW) and len(eq.cases) == 2
    assert raises.needs == {"function", "raises"} and not raises.protected(KINDS_NOW)
    assert raises.cases[0]["expected"] == {
        "raises": {"module": "builtins", "name": "TypeError", "defined_as": "TypeError"}
    }
    assert "assertTrue" in (true.refusal or "")


def test_later_kinds_are_recognised_but_not_declarable_today(tmp_path):
    found = project(
        tmp_path,
        """
        def test_float():
            assert half(3) == pytest.approx(1.5, rel=0.01)
        def test_exact_float():
            assert half(3) == 1.5
        def test_method():
            s = Stack([1, 2])
            assert s.top() == 2
        def test_inline_method():
            assert Stack([4]).top() == 4
        def test_sequence():
            s = Stack([1])
            assert s.top() == 1
            assert s.top() == 1
        def test_project_exception():
            with pytest.raises(BadInput):
                add(1, 2)
        def test_constructor_raises():
            with pytest.raises(TypeError):
                Stack()
    """,
    )
    needs = [sorted(o.needs) for o in found]
    assert needs == [
        ["float", "function"], ["float", "function"], ["method"], ["method"],
        ["method", "sequence"], ["function", "raises"], ["construct", "raises"],
    ]  # fmt: skip
    assert not any(o.protected(KINDS_NOW) for o in found) and all(o.refusal is None for o in found)
    assert found[0].cases[0]["expected"]["tolerance"] == {"rel_tol": 0.01, "abs_tol": 1e-12}
    assert found[1].cases[0]["expected"]["tolerance"] == {"rel_tol": 0.0, "abs_tol": 0.0}
    assert found[2].cases[0]["target"]["init_args"] == [[1, 2]]
    assert found[5].cases[0]["expected"]["raises"] == {
        "module": "pkg",
        "name": "BadInput",
        "defined_as": "pkg.errors.BadInput",
    }
    assert all(o.protected({"function", "float", "method", "sequence", "raises", "construct"})
               for o in found)  # fmt: skip


def test_submodule_and_relative_reexports_resolve(tmp_path):
    found = project(
        tmp_path,
        """
        from pkg.sub import deep
        import pkg.sub.other
        from pkg.sub.deep import plus
        def test_a():
            assert deep.plus(1, 2) == 3
        def test_b():
            assert pkg.sub.other.f() == 1
        def test_c():
            assert plus(1, 1) == 2
        def test_d():
            assert deep.other.f() == 1
    """,
    )
    assert [o.protected(KINDS_NOW) for o in found] == [True, True, True, False]
    assert found[0].cases[0]["target"] == {"module": "pkg.sub.deep", "function": "plus"}
    assert found[1].cases[0]["target"] == {"module": "pkg.sub.other", "function": "f"}


def test_nothing_is_imported_or_run(tmp_path):
    sentinel = tmp_path / "ran"
    side_effect = f"open({str(sentinel)!r}, 'w').write('x')\n"
    extra = {"src/pkg/core.py": side_effect + PKG["src/pkg/core.py"],
             "src/pkg/__init__.py": side_effect + PKG["src/pkg/__init__.py"]}  # fmt: skip
    before = set(sys.modules)
    item = one(tmp_path, "def test_v():\n    assert add(1, 2) == 3\n", extra)
    assert item.protected(KINDS_NOW)
    extra["tests/conftest.py"] = side_effect
    item = one(tmp_path, side_effect + "def test_v():\n    assert add(1, 2) == 3\n", extra)
    assert item.refusal == "module-level Expr at import time"
    assert not sentinel.exists()
    assert not {m for m in set(sys.modules) - before if m == "pkg" or m.startswith("pkg.")}


@pytest.mark.parametrize(
    "where, code, reason, exact",
    [
        ("tests/conftest.py", "def pytest_generate_tests(metafunc):\n    pass\n",
         "hook pytest_generate_tests changes what is collected", False),
        ("tests/conftest.py", "def pytest_configure(config):\n    pass\n",
         "hook pytest_configure runs around the tests", True),
        ("tests/conftest.py", "pytest_plugins = ['p']\n", "pytest_plugins loads plugins", False),
        ("tests/test_x.py", "import pkg\npkg.MODE = 1\n", "attribute or item", True),
        ("tests/test_x.py", "import os\nos.environ['A'] = '1'\n", "attribute or item", True),
        ("tests/test_x.py", "from pkg import add\nX = add(1, 2)\n", "call at import time", True),
        ("tests/test_x.py", "if True:\n    pass\n", "module-level If", False),
    ],
)  # fmt: skip
def test_import_time_code_refuses_every_test_in_its_scope(tmp_path, where, code, reason, exact):
    body = "def test_v():\n    assert add(1, 2) == 3\n"
    if where == "tests/test_x.py":
        item = one(tmp_path, code + body)
    else:
        item = one(tmp_path, body, {where: code})
    assert reason in (item.refusal or "") and item.exact is exact


def test_unparsable_test_file_is_one_refused_obligation(tmp_path):
    item = one(tmp_path, "def test_v(:\n")
    assert not item.exact and "cannot parse" in (item.refusal or "")
