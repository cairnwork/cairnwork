"""tools/blackbox_suggest (#370): hook options the reader does not follow.

``@pytest.hookimpl(specname=...)`` registers a ``pytest_*`` def under another hook's name, so a
def named like a harmless hook can run around every test; ``wrapper`` and ``hookwrapper`` make it
run around other hook calls. The reader keeps a module only when a hookimpl carries no options but
``tryfirst``, ``trylast`` and ``optionalhook``.
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
    "specname_on_addoption": """
        @pytest.hookimpl(specname="pytest_runtest_setup")
        def pytest_addoption(item):
            core.SCALE = 10
    """,
    "specname_on_report_header": """
        @pytest.hookimpl(specname="pytest_runtest_call")
        def pytest_report_header(item):
            core.SCALE = 10
    """,
    "specname_on_assertrepr_compare_with_tryfirst": """
        @pytest.hookimpl(tryfirst=True, specname="pytest_runtest_setup")
        def pytest_assertrepr_compare(item):
            core.SCALE = 10
    """,
    "wrapper_on_addoption": """
        @pytest.hookimpl(wrapper=True)
        def pytest_addoption(parser):
            core.SCALE = 10
            return (yield)
    """,
    "hookwrapper_on_report_header": """
        @pytest.hookimpl(hookwrapper=True)
        def pytest_report_header(config):
            core.SCALE = 10
            yield
    """,
    "specname_on_a_non_hook_name": """
        @pytest.hookimpl(specname="pytest_runtest_setup")
        def helper(item):
            core.SCALE = 10
    """,
    "positional_hookimpl_argument": """
        @pytest.hookimpl("x")
        def pytest_addoption(parser):
            pass
    """,
}


@pytest.mark.parametrize("conftest", REFUSED.values(), ids=REFUSED.keys())
def test_hookimpl_options_the_reader_does_not_follow_refuse(tmp_path, conftest):
    item = run(tmp_path, conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_tryfirst_trylast_optionalhook_on_a_harmless_hook_keep_the_draft(tmp_path):
    item = run(
        tmp_path,
        """
        @pytest.hookimpl(tryfirst=True)
        def pytest_addoption(parser):
            pass
        @pytest.hookimpl(trylast=True, optionalhook=True)
        def pytest_report_header(config):
            pass
        @pytest.hookimpl
        def pytest_assertrepr_compare(op, left, right):
            pass
        """,
    )
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
