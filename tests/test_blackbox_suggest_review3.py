"""tools/blackbox_suggest (#303): regressions from the PR 317 review, round 3.

The first group came from the independent reviewer, the rest from the Gemini cross-model review.
Each equivalence case would have been marked protected although pytest and the draft can disagree
(checked with real pytest on the same files by the reviewer).
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots
from tools.blackbox_suggest.render import _id

CORE = (
    "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
    "REG = {}\nclass Registered:\n    def __init_subclass__(cls, **kw):\n"
    "        REG[cls.__name__] = cls\n"
    "def known(name):\n    return name in REG\n"
    "def push(d, x):\n    d.append(x)\n    return list(d)\ndef first(e):\n    return e[0]\n"
)
EXTRA = "import pkg.core\npkg.core.SCALE = 10\n"
PLAIN = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, body, extra=None):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "src/pkg/extra.py": EXTRA,
        "tests/test_x.py": textwrap.dedent(body),
        **(extra or {}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(textwrap.dedent(text))
    return analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))


EQUIV = {
    "test_imports_project_module_with_side_effect": ("import pkg.extra\n" + PLAIN, {}),
    "from_project_import_submodule": ("from pkg import extra\n" + PLAIN, {}),
    "conftest_imports_project_module": (PLAIN, {"tests/conftest.py": "import pkg.extra\n"}),
    "project_base_class_registers_at_import": ("""
        from pkg.core import Registered, known
        class JsonCodec(Registered):
            pass
        def test_a():
            assert known("JsonCodec") is True
    """, {}),
    "list_holding_an_alias": ("""
        from pkg.core import push, first
        def test_a():
            d = []
            e = [d]
            assert push(d, 1) == [1]
            assert first(e) == [1]
    """, {}),
    "call_inside_an_assigned_expression": ("""
        from pkg.core import f, push
        def test_a():
            _ = [push([], 1)]
            assert f(2) == 2
    """, {}),
    "nose_style_module_setup": ("""
        from pkg.core import f
        def setup():
            pass
        def test_a():
            assert f(2) == 2
    """, {}),
    "pytest_name_rebound_by_an_import": ("""
        from pkg.core import push as mark
        from pkg.core import f
        X = mark([], 1)
        def test_a():
            assert f(2) == 2
    """, {}),
    "pytest_name_rebound_by_a_def": ("""
        from pkg.core import f
        def fixture():
            return 1
        X = fixture()
        def test_a():
            assert f(2) == 2
    """, {}),
}  # fmt: skip


@pytest.mark.parametrize("name", list(EQUIV))
def test_not_protected(tmp_path, name):
    body, extra = EQUIV[name]
    (item,) = run(tmp_path, body, extra)
    assert not item.protected(KINDS_NOW), item.cases


def test_module_level_list_shared_across_tests(tmp_path):
    body = """
        from pkg.core import push
        D = []
        def test_a():
            assert push(D, 1) == [1]
        def test_b():
            assert push(D, 2) == [1, 2]
    """
    found = run(tmp_path, body)
    assert not any(o.protected(KINDS_NOW) for o in found)


def test_imports_the_runner_makes_anyway_keep_the_draft(tmp_path):
    files = {
        "src/pkg/__init__.py": "from pkg import util\n",
        "src/pkg/util.py": "",
        "src/pkg/core.py": "import pkg.helpers\ndef f(x):\n    return x\n",
        "src/pkg/helpers.py": "",
    }
    body = "import pkg.helpers\nimport pkg.util\nfrom pkg.core import f\n"
    body += "def test_a():\n    assert f(2) == 2\n"
    (item,) = run(tmp_path, body, files)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


def test_only_imports_under_if_are_not_counted_as_imported(tmp_path):
    files = {"src/pkg/core.py": "if True:\n    import pkg.extra\ndef f(x):\n    return x\n"}
    (item,) = run(tmp_path, "import pkg.extra\n" + PLAIN, files)
    assert not item.protected(KINDS_NOW)


def test_tests_inherited_from_a_base_elsewhere_keep_an_unknown_count(tmp_path):
    body = "from pkg.core import Registered\nclass TestA(Registered):\n    pass\n"
    found = run(tmp_path, body)
    assert found and not any(o.exact for o in found)


def test_parametrize_by_keyword_is_counted(tmp_path):
    body = """
        import pytest
        from pkg.core import f
        @pytest.mark.parametrize(argnames="x", argvalues=[1, 2, 3])
        def test_a(x):
            assert f(x) == x
    """
    found = run(tmp_path, body)
    assert len(found) == 3 and all(o.exact for o in found)


def test_case_ids_never_repeat():
    taken: set[str] = set()
    ids = [_id(text, taken) for text in ["a", "a", "a", "a-x"] * 5]
    assert len(set(ids)) == len(ids)


def test_a_metaclass_in_the_tests_refuses(tmp_path):
    body = """
        from pkg.core import f, Registered
        class Helper(metaclass=Registered):
            pass
        def test_a():
            assert f(2) == 2
    """
    (item,) = run(tmp_path, body)
    assert "metaclass" in (item.refusal or "")


def test_usefixtures_by_a_name_keeps_the_count_unknown(tmp_path):
    body = """
        import pytest
        from pkg.core import f
        NAME = "p"
        @pytest.fixture(params=[1, 2])
        def p(request):
            return request.param
        @pytest.mark.usefixtures(NAME)
        def test_a():
            assert f(2) == 2
    """
    found = run(tmp_path, body)
    assert len(found) == 2 or not all(o.exact for o in found)
