"""tools/blackbox_suggest (#303): regressions from the PR 317 review, round 1.

Equivalence: each test below would have been marked protected although its draft does not
reproduce what pytest checks. Counts: each one was lower than pytest's collected count (checked
with pytest --collect-only on the same files) while ``exact`` stayed True. The counterexamples
are the independent reviewer's; the rest pin mutants that survived round 1.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots
from tools.blackbox_suggest.coverage import category

CORE = "def f(*a, **k):\n    return 1\n"


def run(tmp_path, body, conftest=None, extra=None):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("")
    (tmp_path / "src/pkg/core.py").write_text(CORE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(textwrap.dedent(body))
    if conftest:
        (tmp_path / "tests/conftest.py").write_text(textwrap.dedent(conftest))
    for rel, text in (extra or {}).items():
        (tmp_path / rel).write_text(textwrap.dedent(text))
    return analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))


PLAIN = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"

EQUIV = {
    "module_level_mutation": (
        'from pkg.core import f\nEXPECTED = {"a": 1}\nEXPECTED["b"] = 2\n'
        "def test_a():\n    assert f() == EXPECTED\n", None),
    "imported_name_rebound": (
        "import functools\nfrom pkg.core import f\nf = functools.partial(f, 10)\n"
        "def test_a():\n    assert f(1) == 11\n", None),
    "imported_name_aliased": (
        "from pkg.core import f\ng = f\nf = g\ndef test_a():\n    assert f(1) == 1\n", None),
    "test_module_top_level_side_effect": (
        'import os\nfrom pkg.core import f\nos.environ["MODE"] = "strict"\n'
        "def test_a():\n    assert f(5) == 5\n", None),
    "conftest_top_level_patch": (PLAIN, "import pkg.core\npkg.core.SCALE = 10\n"),
    "conftest_runtest_setup_hook": (PLAIN, "def pytest_runtest_setup(item):\n    pass\n"),
    "conftest_runtest_teardown_hook": (PLAIN, "def pytest_runtest_teardown(item):\n    pass\n"),
    "parametrize_name_rebound": (
        "import pytest\nfrom pkg.core import f\n@pytest.mark.parametrize('x', [1])\n"
        "def test_a(x):\n    y = x\n    x = 2\n    assert f(y) == 1\n", None),
    "filterwarnings_mark": (
        "import pytest\nfrom pkg.core import f\n@pytest.mark.filterwarnings('error')\n"
        "def test_a():\n    assert f(3) == 3\n", None),
    "custom_mark": (
        "import pytest\nfrom pkg.core import f\n@pytest.mark.slow\n"
        "def test_a():\n    assert f(3) == 3\n", None),
    "module_pytestmark": (
        "import pytest\nfrom pkg.core import f\npytestmark = pytest.mark.filterwarnings('error')\n"
        "def test_a():\n    assert f(3) == 3\n", None),
    "shared_mutable_argument": (
        "from pkg.core import f\ndef test_a():\n    x = [1]\n    assert f(x) == 1\n"
        "    assert f(x) == 1\n", None),
    "module_setup_hook": (
        "from pkg.core import f\ndef setup_module():\n    pass\n"
        "def test_a():\n    assert f(3) == 3\n", None),
}  # fmt: skip


@pytest.mark.parametrize("name", list(EQUIV))
def test_not_protected_when_draft_is_not_equivalent(tmp_path, name):
    body, conftest = EQUIV[name]
    (item,) = run(tmp_path, body, conftest)
    assert not item.protected(KINDS_NOW) and item.refusal, item.cases


@pytest.mark.parametrize(
    "config, reason",
    [
        ({"pytest.ini": "[pytest]\nfilterwarnings =\n    error\n"}, "warnings into errors"),
        ({"pyproject.toml": '[tool.pytest.ini_options]\nfilterwarnings = ["error"]\n'},
         "warnings into errors"),
        ({"setup.cfg": "[tool:pytest]\nusefixtures = db\n"}, "usefixtures"),
        ({"tox.ini": "[pytest]\naddopts = -p myplugin\n"}, "addopts"),
        ({"pyproject.toml": '[tool.pytest.ini_options]\nrequired_plugins = ["x"]\n'}, "not known"),
        ({"pyproject.toml": "not toml ["}, "unreadable"),
    ],
)  # fmt: skip
def test_pytest_configuration_that_changes_every_test_refuses(tmp_path, config, reason):
    (item,) = run(tmp_path, PLAIN, extra=config)
    assert reason in (item.refusal or "")


def test_harmless_configuration_keeps_the_draft(tmp_path):
    config = (
        '[tool.pytest.ini_options]\nfilterwarnings = ["ignore::DeprecationWarning"]\n'
        "xfail_strict = true\nminversion = 7.0\n"
    )
    (item,) = run(tmp_path, PLAIN, extra={"pyproject.toml": config})
    assert item.protected(KINDS_NOW) and item.exact


def test_collection_options_make_the_count_a_lower_bound(tmp_path):
    (item,) = run(tmp_path, PLAIN, extra={"pytest.ini": "[pytest]\npython_functions = check_*\n"})
    assert not item.exact


COUNTS = {  # pytest --collect-only counts on the same files
    "nested_class": ("""
        from pkg.core import f
        class TestOuter:
            class TestInner:
                def test_a(self):
                    assert f(1) == 1
            def test_b(self):
                assert f(1) == 1
    """, 2),
    "autouse_params": ("""
        import pytest
        from pkg.core import f
        @pytest.fixture(autouse=True, params=[1, 2, 3])
        def env(request):
            yield
        def test_a():
            assert f(1) == 1
    """, 3),
    "transitive_fixture_params": ("""
        import pytest
        from pkg.core import f
        @pytest.fixture(params=[1, 2, 3])
        def base(request):
            return request.param
        @pytest.fixture
        def derived(base):
            return base
        def test_a(derived):
            assert f(derived) == 1
    """, 3),
    "pytest_generate_tests": ("""
        from pkg.core import f
        def pytest_generate_tests(metafunc):
            if "x" in metafunc.fixturenames:
                metafunc.parametrize("x", [1, 2, 3, 4])
        def test_a(x):
            assert f(x) == 1
    """, 4),
    "conditional_def": ("""
        import sys
        from pkg.core import f
        if sys.version_info >= (3, 0):
            def test_a():
                assert f(1) == 1
        def test_b():
            assert f(1) == 1
    """, 2),
    "unittest_indirect_base": ("""
        import unittest
        from pkg.core import f
        class Base(unittest.TestCase):
            pass
        class MyChecks(Base):
            def test_a(self):
                self.assertEqual(f(1), 1)
    """, 1),
    "aliased_test": ("""
        from pkg.core import f
        def test_a():
            assert f(1) == 1
        test_b = test_a
    """, 2),
    "base_from_elsewhere": ("""
        from pkg.core import f
        from helpers import Mixin
        class TestA(Mixin):
            def test_a(self):
                assert f(1) == 1
    """, 2),
}  # fmt: skip


@pytest.mark.parametrize("name", list(COUNTS))
def test_count_is_pytests_or_flagged_as_lower_bound(tmp_path, name):
    body, pytest_count = COUNTS[name]
    found = run(tmp_path, body)
    assert len(found) == pytest_count or not all(o.exact for o in found), len(found)


def test_counts_that_are_exact(tmp_path):
    found = run(
        tmp_path,
        """
        import pytest
        from pkg.core import f
        class Base:
            def test_inherited(self):
                assert f(1) == 1
        class TestChild(Base):
            pass
        @pytest.mark.parametrize("x", [])
        def test_empty(x):
            assert f(x) == 1
        @pytest.mark.parametrize("x", [1, 2], indirect=True)
        def test_indirect(x):
            assert f(x) == 1
    """,
    )
    ids = [o.test_id.split("::", 1)[1] for o in found]
    assert ids == ["TestChild::test_inherited", "test_empty", "test_indirect[0]",
                   "test_indirect[1]"]  # fmt: skip
    assert all(o.exact for o in found)
    assert found[0].protected(KINDS_NOW)
    assert "empty parametrize" in (found[1].refusal or "")
    assert "indirect" in (found[2].refusal or "")


def test_overriding_fixture_without_params_replaces_the_params(tmp_path):
    conftest = "import pytest\n@pytest.fixture(params=[1, 2])\ndef num(request):\n    pass\n"
    found = run(tmp_path, """
        import pytest
        from pkg.core import f
        @pytest.fixture
        def num():
            return 1
        def test_a(num):
            assert f(num) == 1
    """, conftest)  # fmt: skip
    assert len(found) == 1 and found[0].exact


def test_methods_resolve_like_the_mro(tmp_path):
    found = run(
        tmp_path,
        """
        from pkg.core import f
        class A:
            def test_m(self):
                assert f(1) == 1
        class B:
            def test_m(self):
                assert f(2) == 2
        class TestX(A, B):
            pass
    """,
    )
    (item,) = found
    assert item.cases[0]["args"] == [1]


def test_is_with_a_non_singleton_is_refused(tmp_path):
    (item,) = run(tmp_path, "from pkg.core import f\ndef test_a():\n    assert f(1) is 1\n")
    assert "only ==, or is None/True/False" in (item.refusal or "")


@pytest.mark.parametrize(
    "reason",
    [
        "'g' in secretpkg is not a plain def or class",
        "cannot find 'g' in secretpkg by reading its source",
        "secretpkg is not a module of the project (stdlib or a dependency?)",
        "line 3: secretpkg.thing is not a top-level name of a project module",
        "line 3: with secretpkg(...) is not pytest.raises",
        "uses fixture(s): secretpkg",
        "a reason this table has never seen, naming secretpkg",
    ],
)
def test_categories_are_fixed_labels(reason):
    label = category(reason)
    assert "secretpkg" not in label and label
