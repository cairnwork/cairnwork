"""tools/blackbox_suggest (#370): harmless hooks with default arguments.

A hook that only declares options is kept because pytest passes it its own ``parser``. A
parameter with a default may keep that default when the hook is called under another spec, so
the reader refuses a harmless hook with any default instead of guessing what it receives.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
STAND_IN = """
class P:
    def addoption(*a):
        pass
"""


def run(tmp_path, conftest):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
        "tests/conftest.py": HEAD + STAND_IN + textwrap.dedent(conftest),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "defaulted_parser": """
        def pytest_addoption(parser=P):
            parser.addoption("--x")
    """,
    "defaulted_parser_after_another": """
        def pytest_addoption(item, parser=P):
            parser.addoption("--x")
    """,
    "defaulted_parser_in_a_renamed_hook": """
        @pytest.hookimpl.__call__(specname="pytest_runtest_setup")
        def pytest_addoption(item, parser=P):
            parser.addoption("--x")
    """,
    "keyword_only_default": """
        def pytest_addoption(parser, *, extra=P):
            parser.addoption("--x")
    """,
    "defaulted_argument_in_another_harmless_hook": """
        def pytest_report_header(config, extra=P):
            pass
    """,
}


@pytest.mark.parametrize("conftest", REFUSED.values(), ids=REFUSED.keys())
def test_a_harmless_hook_with_a_default_refuses(tmp_path, conftest):
    item = run(tmp_path, conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_a_harmless_hook_without_defaults_keeps_the_draft(tmp_path):
    conftest = """
        def pytest_addoption(parser):
            parser.addoption("--x", action="store_true")
        def pytest_report_header(config):
            pass
    """
    item = run(tmp_path, conftest)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
