"""tools/blackbox_suggest (#370): marks and hookimpls the reader cannot name.

The checks on marks are keyed by the mark's name, read from the last part of the call's dotted
target. A call that reaches a mark or a hookimpl through another attribute hides that name, so
the reader refuses any such call in a decorator or a mark value instead of guessing.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"


def run(tmp_path, body, conftest=""):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + textwrap.dedent(body) + PLAIN,
        "tests/conftest.py": HEAD + textwrap.dedent(conftest),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "skipif_with_args": """
        @pytest.mark.skipif.with_args("x", reason="r")
        def test_b():
            pass
    """,
    "xfail_with_args": """
        @pytest.mark.xfail.with_args("x", reason="r")
        def test_b():
            pass
    """,
    "skipif_call": """
        @pytest.mark.skipif.__call__("x", reason="r")
        def test_b():
            pass
    """,
    "with_args_function_called_with_the_mark": """
        @pytest.mark.skipif.with_args.__func__(pytest.mark.skipif, "x", reason="r")
        def test_b():
            pass
    """,
    "condition_keyword_with_args": """
        @pytest.mark.skipif.with_args(condition="x", reason="r")
        def test_b():
            pass
    """,
    "parametrize_positional_ids_with_args": """
        @pytest.mark.parametrize.with_args("v", [1], False, exec)
        def test_b(v):
            pass
    """,
    "parametrize_positional_scope_call": """
        @pytest.mark.parametrize.__call__("v", [1], False, None, len)
        def test_b(v):
            pass
    """,
    "filterwarnings_with_args": """
        @pytest.mark.filterwarnings.with_args("ignore::pkg.side.Warn")
        def test_b():
            pass
    """,
    "filterwarnings_call": """
        @pytest.mark.filterwarnings.__call__("ignore::pkg.side.Warn")
        def test_b():
            pass
    """,
    "class_pytestmark": """
        class TestB:
            pytestmark = [pytest.mark.skipif.with_args("x", reason="r")]
            def test_c(self):
                pass
    """,
    "class_decorator": """
        @pytest.mark.skipif.with_args("x", reason="r")
        class TestB:
            def test_c(self):
                pass
    """,
    "param_marks": """
        @pytest.mark.parametrize(
            "v", [pytest.param(1, marks=pytest.mark.skipif.with_args("x", reason="r"))]
        )
        def test_b(v):
            pass
    """,
    "mark_imported_from_pytest": """
        from pytest import mark
        @mark.skipif.with_args("x", reason="r")
        def test_b():
            pass
    """,
}


@pytest.mark.parametrize("body", REFUSED.values(), ids=REFUSED.keys())
def test_a_mark_called_through_another_attribute_refuses(tmp_path, body):
    item = run(tmp_path, body)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


SPECIAL = {
    "skipif_wrapped": """
        @pytest.mark.skipif.__wrapped__("x", reason="r")
        def test_b():
            pass
    """,
    "skipif_func": """
        @pytest.mark.skipif.__func__("x", reason="r")
        def test_b():
            pass
    """,
    "skipif_self": """
        @pytest.mark.skipif.__self__("x", reason="r")
        def test_b():
            pass
    """,
    "filterwarnings_wrapped": """
        @pytest.mark.filterwarnings.__wrapped__("ignore::pkg.side.Warn")
        def test_b():
            pass
    """,
    "parametrize_func": """
        @pytest.mark.parametrize.__func__("v", [1], False, exec)
        def test_b(v):
            pass
    """,
    "special_in_the_middle": """
        @pytest.mark.__class__.slow("x")
        def test_b():
            pass
    """,
}


@pytest.mark.parametrize("body", SPECIAL.values(), ids=SPECIAL.keys())
def test_a_mark_through_another_special_attribute_refuses(tmp_path, body):
    item = run(tmp_path, body)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


HOOKIMPL_REFUSED = {
    "hookimpl_call_with_options": """
        @pytest.hookimpl.__call__(specname="pytest_runtest_setup")
        def pytest_report_header(item):
            pass
    """,
    "hookimpl_call_with_a_class_default": """
        class P:
            def addoption(*a):
                pass
        @pytest.hookimpl.__call__(specname="pytest_runtest_setup")
        def pytest_addoption(item, parser=P):
            parser.addoption("--x")
    """,
}


@pytest.mark.parametrize("conftest", HOOKIMPL_REFUSED.values(), ids=HOOKIMPL_REFUSED.keys())
def test_a_hookimpl_called_through_another_attribute_refuses(tmp_path, conftest):
    item = run(tmp_path, "", conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_plain_marks_and_hookimpls_keep_the_draft(tmp_path):
    body = """
        @pytest.mark.filterwarnings("ignore::DeprecationWarning")
        @pytest.mark.skipif(False, reason="r")
        @pytest.mark.xfail(condition=True, reason="r")
        @pytest.mark.parametrize("v", [pytest.param(1, marks=pytest.mark.slow)], ids=["one"])
        def test_b(v):
            pass
    """
    conftest = """
        @pytest.hookimpl(tryfirst=True)
        def pytest_addoption(parser):
            pass
    """
    item = run(tmp_path, body, conftest)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
