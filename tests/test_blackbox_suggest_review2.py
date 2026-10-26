"""tools/blackbox_suggest (#303): regressions from the PR 317 review, round 2.

The counterexamples are the independent reviewer's. Each equivalence case was run under real
pytest 9.1.1 with a synthetic candidate that passes the draft and fails the test (or the other way
round); each count against pytest --collect-only. The rest pin mutants that survived round 2.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, Obligation, analyze_file, default_roots
from tools.blackbox_suggest.coverage import task_coverage

CORE = "SCALE = 1\ndef f(*a, **k):\n    return 1\ndef add(a, b):\n    return a + b\n"
CORE += "def sub(a, b):\n    return a - b\ndef push(d, x):\n    d.append(x)\n    return list(d)\n"
CORE += "REG = {}\ndef register(fn):\n    REG[fn.__name__] = fn\n    return fn\n"


def run(tmp_path, body, extra=None):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("")
    (tmp_path / "src/pkg/core.py").write_text(CORE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(textwrap.dedent(body))
    for rel, text in (extra or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(textwrap.dedent(text))
    return analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))


P_FIXTURE = (
    "import pytest\n@pytest.fixture(params=[1, 2, 3])\ndef p(request):\n    return request.param\n"
)
PLAIN = "from pkg.core import f\ndef test_a():\n    assert f(2) == 1\n"
EQUIV = {
    "class_body_code": (
        """
        import pkg.core
        from pkg.core import f
        class Helper:
            pkg.core.SCALE = 10
        def test_a():
            assert f(2) == 1
    """,
        None,
    ),
    "helper_default_argument": (
        """
        import os
        from pkg.core import f
        def helper(_=os.environ.update(MODE="strict")):
            pass
        def test_a():
            assert f(5) == 1
    """,
        None,
    ),
    "helper_decorator_registers": (
        """
        from pkg.core import f, register
        @register
        def json_codec():
            pass
        def test_a():
            assert f(1) == 1
    """,
        None,
    ),
    "side_effect_import_of_test_helper": (
        "import patch_scale\n" + PLAIN,
        {"tests/patch_scale.py": "import pkg.core\npkg.core.SCALE = 10\n"},
    ),
    "tests_package_init": (PLAIN, {"tests/__init__.py": "import pkg.core\npkg.core.SCALE = 10\n"}),
    "local_alias_of_imported_callee": (
        """
        from pkg.core import add, sub
        def test_a():
            add = sub
            assert add(1, 1) == 2
    """,
        None,
    ),
    "aliased_mutable_argument": (
        """
        from pkg.core import push
        def test_a():
            d = []
            e = d
            assert push(d, 1) == [1]
            assert push(e, 2) == [1, 2]
    """,
        None,
    ),
    "addopts_Werror": (PLAIN, {"pytest.ini": "[pytest]\naddopts = -Werror\n"}),
    "pyproject_native_tool_pytest": (
        PLAIN,
        {"pyproject.toml": '[tool.pytest]\nfilterwarnings = ["error"]\n'},
    ),
    "pytest_toml": (PLAIN, {"pytest.toml": '[pytest]\nfilterwarnings = ["error"]\n'}),
    "config_closer_to_the_test": (
        PLAIN,
        {"tests/pytest.ini": "[pytest]\nfilterwarnings = error\n"},
    ),
}


@pytest.mark.parametrize("name", list(EQUIV))
def test_not_protected_when_something_runs_around_the_test(tmp_path, name):
    body, extra = EQUIV[name]
    (item,) = run(tmp_path, body, extra)
    assert not item.protected(KINDS_NOW), item.cases


COUNTS = {  # pytest --collect-only counts (pytest 9.1.1, same files)
    "class_autouse_params": (
        """
        import pytest
        from pkg.core import f
        class TestA:
            @pytest.fixture(autouse=True, params=[1, 2])
            def env(self, request):
                yield
            def test_a(self):
                assert f(1) == 1
    """,
        None,
        2,
    ),
    "usefixtures_mark_with_params": (
        """
        import pytest
        from pkg.core import f
        @pytest.fixture(params=[1, 2, 3])
        def p(request):
            return request.param
        @pytest.mark.usefixtures("p")
        def test_a():
            assert f(1) == 1
    """,
        None,
        3,
    ),
    "ini_usefixtures_with_params": (
        PLAIN,
        {
            "tests/conftest.py": P_FIXTURE,
            "pytest.ini": "[pytest]\nusefixtures = p\n",
        },
        3,
    ),
    "pytest_plugins_fixture_params": (
        """
        from pkg.core import f
        def test_a(p):
            assert f(p) == 1
    """,
        {
            "tests/conftest.py": "pytest_plugins = ['myplugin']\n",
            "tests/myplugin.py": P_FIXTURE,
        },
        3,
    ),
}


@pytest.mark.parametrize("name", list(COUNTS))
def test_count_is_pytests_or_flagged_as_lower_bound(tmp_path, name):
    body, extra, pytest_count = COUNTS[name]
    found = run(tmp_path, body, extra)
    assert len(found) == pytest_count or not all(o.exact for o in found), len(found)


@pytest.mark.parametrize(
    "extra, reason",
    [
        ({".pytest.ini": "[pytest]\nfilterwarnings = error\n"}, "warnings into errors"),
        ({"pytest.ini": "[pytest]\naddopts = -W error\n"}, "addopts"),
        ({"pytest.ini": "[pytest]\naddopts = --cov=pkg --cov-fail-under=90\n"}, "addopts"),
        ({"pytest.ini": "[pytest]\nasyncio_mode = auto\n"}, "not known"),
    ],
)
def test_more_configuration_shapes_refuse(tmp_path, extra, reason):
    (item,) = run(tmp_path, PLAIN, extra)
    assert reason in (item.refusal or "")


def test_inert_configuration_and_type_checking_imports_keep_the_draft(tmp_path):
    extra = {"pytest.ini": "[pytest]\naddopts = -ra -q --tb=short -p no:cacheprovider\n"
                           "markers =\n    slow: slow\nxfail_strict = true\n"}  # fmt: skip
    body = "import os\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import json\n"
    (item,) = run(tmp_path, body + PLAIN, extra)
    assert item.protected(KINDS_NOW) and item.exact


@pytest.mark.parametrize(
    "body",
    [
        "del os\n",
        "assert True\n",
        "global X\n",
        "import json as j\nj.x = 1\n",
        "from . import helper\n",
    ],
)
def test_any_other_module_statement_refuses(tmp_path, body):
    (item,) = run(tmp_path, "import os\n" + body + PLAIN)
    assert item.refusal is not None


def test_autouse_fixture_with_unknown_params_makes_the_count_inexact(tmp_path):
    body = (
        "import pytest\nfrom pkg.core import f\nVALUES = list(range(3))\n"
        "@pytest.fixture(autouse=True, params=VALUES)\ndef env(request):\n    yield\n"
        "def test_a():\n    assert f(1) == 1\n"
    )
    (item,) = run(tmp_path, body)
    assert not item.exact


def test_non_test_class_with_a_foreign_base_makes_the_count_inexact(tmp_path):
    body = (
        "from pkg.core import f\nfrom pkg.core import f as Mixin\n"
        "class Helper(Mixin):\n    def test_x(self):\n        assert f(1) == 1\n"
        "def test_a():\n    assert f(1) == 1\n"
    )
    found = run(tmp_path, body)  # Helper may be a TestCase: one unseen placeholder stands in
    assert [o.test_id.endswith("<unseen>") for o in found] == [True, False]
    assert not any(o.exact for o in found) and not found[0].protected({"function"})


def test_a_fixture_unknown_here_makes_the_count_inexact(tmp_path):
    (item,) = run(tmp_path, "from pkg.core import f\ndef test_a(db):\n    assert f(1) == 1\n")
    assert not item.exact
    (item,) = run(tmp_path / "b", "from pkg.core import f\ndef test_a(tmp_path):\n    pass\n")
    assert item.exact


def test_parametrize_shadowing_a_fixture_is_not_counted_twice(tmp_path):
    body = (
        "import pytest\nfrom pkg.core import f\n"
        "@pytest.fixture(params=[1, 2, 3])\ndef x(request):\n    return request.param\n"
        "@pytest.fixture\ndef y(x):\n    return x\n"
        "@pytest.mark.parametrize('x', [5])\ndef test_a(x, y):\n    assert f(x) == 1\n"
    )
    assert len(run(tmp_path, body)) == 1


def test_task_coverage_is_exact_only_when_every_count_is():
    exact = Obligation("t.py::a", "t.py", 1, True, "r", [])
    lower = Obligation("t.py::b", "t.py", 1, False, "r", [])
    assert task_coverage([exact, lower], {"function"})["exact"] is False
    assert task_coverage([exact, exact], {"function"})["exact"] is True


def test_a_test_helper_importable_from_the_root_is_not_the_project(tmp_path):
    extra = {"tests/helpers.py": "import pkg.core\npkg.core.SCALE = 10\n"}
    (item,) = run(tmp_path, "import tests.helpers\n" + PLAIN, extra)
    assert "outside the project" in (item.refusal or "")


def test_reporting_addopts_pass_and_selection_makes_the_count_unknown(tmp_path):
    ini = "[pytest]\naddopts = --cov=pkg --cov-report term --cov-report=xml -ra --verbose\n"
    (item,) = run(tmp_path, PLAIN, {"pytest.ini": ini})
    assert item.protected(KINDS_NOW) and item.exact
    (item,) = run(tmp_path / "b", PLAIN, {"pytest.ini": "[pytest]\naddopts = -m 'not slow'\n"})
    assert item.protected(KINDS_NOW) and not item.exact
