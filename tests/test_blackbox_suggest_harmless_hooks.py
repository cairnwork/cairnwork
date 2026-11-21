"""tools/blackbox_suggest (#370): the hooks the reader lets through must do nothing.

``pytest_addoption``, ``pytest_report_header`` and ``pytest_assertrepr_compare`` run before or
around the tests, so a body that does anything can change what the tests see. The reader keeps a
module that defines one only when its body is a docstring, ``pass``, or ``parser.addoption`` calls
with literal arguments (``pytest_addoption`` only), and only when it is bound by a ``def``.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"


def run(tmp_path, conftest):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
        "tests/conftest.py": HEAD + textwrap.dedent(conftest),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "addoption_store": """
        def pytest_addoption(parser):
            core.SCALE = 10
    """,
    "report_header_store": """
        def pytest_report_header(config):
            core.SCALE = 10
    """,
    "assertrepr_compare_store": """
        def pytest_assertrepr_compare(config, op, left, right):
            core.SCALE = 10
    """,
    "addoption_call_after_an_option": """
        def pytest_addoption(parser):
            parser.addoption("--x", action="store_true")
            f(3)
    """,
    "addoption_type_callable": """
        def pytest_addoption(parser):
            parser.addoption("--x", default="1", type=f)
    """,
    "addoption_computed_argument": """
        def pytest_addoption(parser):
            parser.addoption("--" + "x", default=f(1))
    """,
    "addoption_type_string": """
        def pytest_addoption(parser):
            parser.addoption("--x", default="1", type="custom")
    """,
    "addoption_action_not_built_in": """
        def pytest_addoption(parser):
            parser.addoption("--x", action="custom")
    """,
    "addoption_action_help": """
        def pytest_addoption(parser):
            parser.addoption("--x", action="help")
    """,
    "addoption_action_not_a_string": """
        def pytest_addoption(parser):
            parser.addoption("--x", action=None)
    """,
    "addoption_computed_in_a_list": """
        def pytest_addoption(parser):
            parser.addoption("--x", choices=["a", f(1)])
    """,
    "addoption_double_star": """
        def pytest_addoption(parser):
            parser.addoption("--x", **{"default": 1})
    """,
    "addoption_on_another_receiver": """
        def pytest_addoption(parser):
            core.addoption("--x")
    """,
    "addoption_on_a_group": """
        def pytest_addoption(parser):
            parser.getgroup("g").addoption("--x")
    """,
    "addoption_in_another_hook": """
        def pytest_report_header(config, parser):
            parser.addoption("--x")
    """,
    "addini": """
        def pytest_addoption(parser):
            parser.addini("x", "help")
    """,
    "returns_a_value": """
        def pytest_report_header(config):
            return "x"
    """,
    "nested_def": """
        def pytest_addoption(parser):
            def g():
                pass
    """,
    "conditional": """
        def pytest_addoption(parser):
            if parser:
                pass
    """,
    "async_body": """
        async def pytest_report_header(config):
            core.SCALE = 10
    """,
    "bound_by_assignment": """
        pytest_report_header = lambda config: f(3)
    """,
    "bound_by_import": """
        from pkg.core import f as pytest_addoption
    """,
    "in_a_class_body": """
        class Plugin:
            def pytest_report_header(self, config):
                core.SCALE = 10
    """,
}


@pytest.mark.parametrize("conftest", REFUSED.values(), ids=REFUSED.keys())
def test_a_harmless_hook_that_does_something_refuses(tmp_path, conftest):
    item = run(tmp_path, conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "docstring_and_pass": '''
        def pytest_report_header(config):
            """Nothing."""
            pass
    ''',
    "literal_options": """
        def pytest_addoption(parser, pluginmanager):
            parser.addoption("--x", action="store_true", default=False, help="x")
            parser.addoption("--y", choices=["a", "b"], default=None)
    """,
    "built_in_actions": """
        def pytest_addoption(parser):
            parser.addoption("--a", action="store", default="1")
            parser.addoption("--b", action="store_false")
            parser.addoption("--c", action="store_const", const=1)
            parser.addoption("--d", action="append", default=[])
            parser.addoption("--e", action="append_const", const=1)
            parser.addoption("--f", action="count", default=0)
            parser.addoption("--g", action="extend", nargs="+")
    """,
    "ellipsis": """
        def pytest_assertrepr_compare(config, op, left, right):
            ...
    """,
}


@pytest.mark.parametrize("conftest", KEPT.values(), ids=KEPT.keys())
def test_an_empty_harmless_hook_keeps_the_draft(tmp_path, conftest):
    item = run(tmp_path, conftest)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
