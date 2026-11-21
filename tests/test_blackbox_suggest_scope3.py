"""tools/blackbox_suggest (#370): marks whose arguments pytest calls or evaluates later.

pytest calls a parametrize or fixture ``ids`` callable for each case at collection, and it
evaluates a skipif or xfail condition at setup (a string with ``eval``). Either can run code from
the file and change what other tests see, so the reader keeps a module only when ``ids`` is a
literal list (or ``str``/``repr`` never rebound) and every condition is ``True`` or ``False``,
a ``scope`` is a literal, a filterwarnings category is builtin or from the standard library,
and no argument names a ``def`` or ``class`` of the file.
Each refused module below was checked with real pytest: ``test_a`` fails in a whole-file run.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
IDS = "def _ids(v):\n    core.SCALE = 10\n    return 'x'\n"


def run(tmp_path, body, conftest=None):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + body + PLAIN,
    }
    if conftest is not None:
        files["tests/conftest.py"] = HEAD + conftest
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def case(id, *parts, conftest=None):
    body = "".join(textwrap.dedent(p) if p.startswith("\n") else p for p in parts)
    return pytest.param(body, None if conftest is None else textwrap.dedent(conftest), id=id)


REFUSED = [
    case("ids_same_file_def", IDS, """
        @pytest.mark.parametrize("v", [1], ids=_ids)
        def test_b(v):
            pass
    """),
    case("ids_same_file_def_positional", IDS, """
        @pytest.mark.parametrize("v", [1], False, _ids)
        def test_b(v):
            pass
    """),
    case("ids_on_class_decorator", IDS, """
        @pytest.mark.parametrize("v", [1], ids=_ids)
        class TestK:
            def test_b(self, v):
                pass
    """),
    case("ids_in_class_pytestmark", IDS, """
        class TestK:
            pytestmark = pytest.mark.parametrize("v", [1], ids=_ids)
            def test_b(self, v):
                pass
    """),
    case("ids_in_module_pytestmark_list", IDS, """
        pytestmark = [pytest.mark.parametrize("v", [1], ids=_ids)]
    """),
    case("ids_same_file_class", """
        class _Ids:
            def __init__(self, v):
                core.SCALE = 10
        @pytest.mark.parametrize("v", [1], ids=_Ids)
        def test_b(v):
            pass
    """),
    case("ids_alias_of_same_file_def", IDS, """
        _alias = _ids
        @pytest.mark.parametrize("v", [1], ids=_alias)
        def test_b(v):
            pass
    """),
    case("ids_builtin_other_than_str", """
        @pytest.mark.parametrize("v", ["core.SCALE = 10"], ids=exec)
        def test_b(v):
            pass
    """),
    case("ids_str_rebound_in_file", """
        def str(v):
            core.SCALE = 10
            return "x"
        @pytest.mark.parametrize("v", [1], ids=str)
        def test_b(v):
            pass
    """),
    case("ids_list_with_a_name", IDS, """
        @pytest.mark.parametrize("v", [1], ids=[_ids])
        def test_b(v):
            pass
    """),
    case("fixture_ids_in_conftest", conftest=IDS + textwrap.dedent("""
        @pytest.fixture(params=[1], ids=_ids)
        def unused(request):
            return request.param
    """)),
    case("skipif_string_condition", """
        @pytest.mark.skipif("core.__dict__.update(SCALE=10)", reason="r")
        def test_b():
            pass
    """),
    case("xfail_string_condition", """
        @pytest.mark.xfail("core.__dict__.update(SCALE=10)", reason="r")
        def test_b():
            pass
    """),
    case("skipif_condition_keyword_string", """
        @pytest.mark.skipif(condition="core.__dict__.update(SCALE=10)", reason="r")
        def test_b():
            pass
    """),
    case("skipif_condition_name", """
        FLAG = "core.__dict__.update(SCALE=10)"
        @pytest.mark.skipif(FLAG, reason="r")
        def test_b():
            pass
    """),
    case("skipif_string_in_param_marks", """
        @pytest.mark.parametrize("v", [pytest.param(1, marks=pytest.mark.skipif("1"))])
        def test_b(v):
            pass
    """),
    case("skipif_string_in_module_pytestmark", """
        pytestmark = pytest.mark.skipif("core.__dict__.update(SCALE=10)", reason="r")
    """),
    case("xfail_string_condition_in_conftest_fixture_mark", conftest="""
        pytestmark = [pytest.mark.xfail("1", reason="r")]
    """),
    case("ids_str_after_a_stdlib_star_import", """
        from builtins import *
        @pytest.mark.parametrize("v", [1], ids=str)
        def test_b(v):
            pass
    """),
    case("ids_str_bound_by_an_import", """
        from os import system as str
        @pytest.mark.parametrize("v", ["true"], ids=str)
        def test_b(v):
            pass
    """),
    case("fixture_scope_callable_in_conftest", conftest=IDS + textwrap.dedent("""
        @pytest.fixture(scope=_ids)
        def unused():
            return 1
    """)),
    case("parametrize_scope_positional_name", IDS, """
        @pytest.mark.parametrize("v", [1], False, None, _ids)
        def test_b(v):
            pass
    """),
    case("same_file_class_as_a_param_value", """
        class Box:
            pass
        @pytest.mark.parametrize("v", [Box])
        def test_b(v):
            pass
    """),
    case("same_file_def_through_a_list_alias", IDS, """
        CASES = [1, _ids]
        @pytest.mark.parametrize("v", CASES)
        def test_b(v):
            pass
    """),
    case("filterwarnings_dotted_project_category", """
        @pytest.mark.filterwarnings("ignore::pkg.core.Warn")
        def test_b():
            pass
    """),
    case("filterwarnings_not_a_literal", """
        FILTER = "ignore"
        @pytest.mark.filterwarnings(FILTER)
        def test_b():
            pass
    """),
]  # fmt: skip


@pytest.mark.parametrize(("body", "conftest"), REFUSED)
def test_marks_pytest_calls_or_evaluates_later_refuse(tmp_path, body, conftest):
    item = run(tmp_path, body, conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


CONTROLS = [
    case("ids_literal_list_none_and_str", """
        @pytest.mark.parametrize("v", [1, 2], ids=["one", "two"])
        def test_b(v):
            pass
        @pytest.mark.parametrize("v", [1], ids=None)
        def test_c(v):
            pass
        @pytest.mark.parametrize("v", [1], ids=str)
        def test_d(v):
            pass
        @pytest.mark.parametrize("v", [1], ids=repr)
        def test_e(v):
            pass
    """),
    case("bool_conditions_and_reason_only_marks", """
        @pytest.mark.skipif(False, reason="r")
        def test_b():
            pass
        @pytest.mark.xfail(True, reason="r")
        def test_c():
            pass
        @pytest.mark.xfail(reason="r")
        def test_d():
            pass
        @pytest.mark.xfail
        def test_e():
            pass
        @pytest.mark.skip(reason="r")
        def test_f():
            pass
    """),
    case("literal_scope_and_stdlib_filters", """
        @pytest.mark.parametrize("v", [1], scope="module")
        @pytest.mark.filterwarnings("ignore::DeprecationWarning")
        @pytest.mark.filterwarnings("ignore::warnings.WarningMessage")
        def test_b(v):
            pass
    """),
    case("plain_fixture_ids_in_conftest", conftest="""
        @pytest.fixture(params=[1], ids=["one"])
        def unused(request):
            return request.param
    """),
]  # fmt: skip


@pytest.mark.parametrize(("body", "conftest"), CONTROLS)
def test_literal_ids_and_bool_conditions_keep_the_draft(tmp_path, body, conftest):
    item = run(tmp_path, body, conftest)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


def _ids_type(tmp_path, before=""):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "SCALE = 1\ndef f(x):\n    return x * SCALE\n",
        "tests/test_t.py": "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
        + before
        + "@pytest.mark.parametrize('v', [1], ids=type)\ndef test_b(v):\n    pass\n"
        + "def test_a():\n    assert f(2) == 2\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_t.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def test_ids_type_keeps_the_draft(tmp_path):
    item = _ids_type(tmp_path)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


def test_ids_type_bound_to_a_same_file_class_refuses(tmp_path):
    item = _ids_type(tmp_path, "class type:\n    def __init__(self, v):\n        core.SCALE = 10\n")
    assert item.refusal is not None and not item.protected(KINDS_NOW)
