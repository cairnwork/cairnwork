# ruff: noqa: E501
"""Second observer, new PR: class targets, filterwarnings, xunit setup, residual fields, nits."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from second_observer_support import FIXED, Project

from experimental.second_observer.autouse import autouse_files, marked_na
from experimental.second_observer.shape import target_shape_na, xunit_setup_na

RUNNER = Path(__file__).resolve().parents[1] / "experimental" / "second_observer" / "runner.py"
CALC = "from calc import add\n\n\n"
TARGET = "tests/test_calc.py::test_add"
READY = "READY = []\n\n\n"


def module(tmp_path, text):
    path = tmp_path / "test_m.py"
    path.write_text(text)
    return path


def row_of(project, candidate, target=TARGET):
    code, row, _ = project.shadow(project.shadow_input(candidate, target))
    assert code == 0
    return row


def run_runner(tmp_path, text, name):
    """Run the bare runner directly (no sandbox needed for these small modules)."""
    (tmp_path / "test_m.py").write_text(text)
    result = tmp_path / f"result-{len(list(tmp_path.glob('result-*')))}.json"
    subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-B", str(RUNNER), str(tmp_path), "test_m.py", name, str(result), "[]"],
        check=False, capture_output=True, timeout=60,
    )  # fmt: skip
    return json.loads(result.read_text())


# 1. class-level target
def test_class_target_is_na_static_and_in_the_runner(tmp_path):
    text = "class TestCalc:\n    def test_a(self):\n        assert False\n"
    assert target_shape_na(module(tmp_path, text), "TestCalc") is True
    got = run_runner(tmp_path, text, "TestCalc")
    assert (got["status"], got["detail"]) == ("NA", "TARGET_SHAPE")


def test_function_and_method_targets_keep_their_shape(tmp_path):
    text = "def test_a():\n    pass\n\n\nclass TestC:\n    def test_b(self):\n        pass\n"
    path = module(tmp_path, text)
    assert target_shape_na(path, "test_a") is False
    assert target_shape_na(path, "TestC.test_b") is False
    assert run_runner(tmp_path, text, "test_a")["status"] == "PASS"


# 2. filterwarnings mark
def test_filterwarnings_mark_is_na_static_and_in_the_runner(tmp_path):
    text = "import pytest\n\n\n@pytest.mark.filterwarnings('error')\ndef test_a():\n    pass\n"
    assert marked_na(module(tmp_path, text), "test_a") is True
    assert run_runner(tmp_path, text, "test_a")["status"] == "NA"


# 3. xunit setup
@pytest.mark.parametrize(
    "hook",
    ["setup_module", "teardown_module", "setup_function", "teardown_function",
     "setup", "teardown", "setUpModule", "tearDownModule"],
)  # fmt: skip
def test_function_target_with_module_xunit_is_na(tmp_path, hook):
    path = module(tmp_path, f"def {hook}(*a):\n    pass\n\n\ndef test_a():\n    pass\n")
    assert xunit_setup_na(path, "test_a") is True


def test_function_target_without_setup_is_still_judged(tmp_path):
    path = module(tmp_path, "def helper():\n    pass\n\n\ndef test_a():\n    pass\n")
    assert xunit_setup_na(path, "test_a") is False


@pytest.mark.parametrize(
    "hook", ["setup_method", "teardown_method", "setup_class", "teardown_class"]
)
def test_unittest_target_with_class_xunit_is_na(tmp_path, hook):
    text = f"import unittest\n\n\nclass T(unittest.TestCase):\n    def {hook}(self):\n        pass\n\n    def test_a(self):\n        pass\n"
    assert xunit_setup_na(module(tmp_path, text), "T.test_a") is True


@pytest.mark.parametrize(
    "hook", ["setup_module", "teardown_module", "setup_function", "teardown_function"]
)
def test_unittest_target_with_module_xunit_is_na(tmp_path, hook):
    text = f"import unittest\n\n\ndef {hook}(*a):\n    pass\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
    assert xunit_setup_na(module(tmp_path, text), "T.test_a") is True


@pytest.mark.parametrize(
    "hook", ["setup_module", "teardown_module", "setup_function", "teardown_function"]
)
def test_runner_sees_module_xunit_for_a_unittest_target(tmp_path, hook):
    text = f"import unittest\n\n\ndef {hook}(*a):\n    pass\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
    assert run_runner(tmp_path, text, "T.test_a")["detail"] == "XUNIT_SETUP"


def test_unittest_own_setup_hooks_do_not_count(tmp_path):
    text = (
        "import unittest\n\n\ndef setUpModule():\n    pass\n\n\nclass T(unittest.TestCase):\n"
        "    @classmethod\n    def setUpClass(cls):\n        pass\n\n    def setUp(self):\n"
        "        pass\n\n    def test_a(self):\n        pass\n"
    )
    assert xunit_setup_na(module(tmp_path, text), "T.test_a") is False


# 4. nit mutants: unittest skip / expected failure are not clean; autouse=<name> counts
def test_unittest_skip_and_expected_failure_are_not_a_pass(tmp_path):
    skip = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.skipTest('x')\n"
    xfail = (
        "import unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.expectedFailure\n"
        "    def test_a(self):\n        assert False\n"
    )
    ok = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
    assert run_runner(tmp_path, skip, "T.test_a")["status"] == "FAIL"
    assert run_runner(tmp_path, xfail, "T.test_a")["status"] == "FAIL"
    assert run_runner(tmp_path, ok, "T.test_a")["status"] == "PASS"


def test_autouse_given_by_name_counts_as_autouse(tmp_path):
    (tmp_path / "conftest.py").write_text(
        "import pytest\nFLAG = True\n\n\n@pytest.fixture(autouse=FLAG)\ndef f(): pass\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    assert autouse_files(tmp_path, "tests/test_x.py", []) == ["conftest.py"]


# end to end rows (static NA needs no sandbox; the judged ones do)
def test_rows_for_class_filterwarnings_and_xunit_are_na_with_reasons(tmp_path):
    cases = {
        "cls": (f"{CALC}class TestCalc:\n    def test_add(self):\n        assert add(2, 3) == 5\n",
                "tests/test_calc.py::TestCalc", "TARGET_SHAPE"),
        "fw": (f"import pytest\n{CALC}@pytest.mark.filterwarnings('error')\ndef test_add():\n    assert add(2, 3) == 5\n",
               TARGET, "MARKS"),
        "xu": (f"{CALC}{READY}def setup_module(m):\n    READY.append(1)\n\n\ndef test_add():\n    assert READY\n    assert add(2, 3) == 5\n",
               TARGET, "XUNIT_SETUP"),
    }  # fmt: skip
    for key, (text, target, reason) in cases.items():
        project = Project(tmp_path / key, tests={"tests/test_calc.py": text})
        row = row_of(project, project.candidate({"src/calc.py": FIXED}), target)
        assert (row["observer"], row["detail"]) == ("NA", reason), key


@pytest.mark.sandbox
def test_normal_function_and_setupclass_unittest_are_still_judged(tmp_path):
    plain = Project(tmp_path / "p")
    assert row_of(plain, plain.candidate({"src/calc.py": FIXED}))["observer"] == "PASS"
    text = (
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    @classmethod\n    def setUpClass(cls):\n        cls.n = 2\n\n"
        "    def test_add(self):\n        self.assertEqual(add(self.n, 3), 5)\n"
    )
    project = Project(tmp_path / "u", tests={"tests/test_calc.py": text})
    row = row_of(
        project, project.candidate({"src/calc.py": FIXED}), "tests/test_calc.py::T::test_add"
    )
    assert row["observer"] == "PASS"


# 5. residual
def test_a_plain_project_has_no_residual_but_the_field_exists(tmp_path):
    project = Project(tmp_path, tests={"tests/test_calc.py": "def test_add():\n    pass\n"})
    row = row_of(project, project.base)
    from cairnwork import pytest_config

    installed = pytest_config.installed_plugins(sys.executable)
    assert row["residual"] == (["TABLE_PLUGINS"] if installed else [])


def test_residual_lists_each_known_class(tmp_path):
    ini = "[pytest]\nfilterwarnings =\n    error\n"
    files = {
        "pytest.ini": ini,
        "conftest.py": "import os\nfrom helpers import *\nos.environ['A'] = '1'\npytest_plugins = ['a' + 'b']\n",
    }
    project = Project(tmp_path, base_files=files)
    got = set(row_of(project, project.base)["residual"])
    assert {"INI_KEYS", "IMPORTED_NAMES", "CONFTEST_CODE", "PYTEST_PLUGINS_NONLITERAL"} <= got
    assert not any(x.startswith("unknown") for x in got - {"unknown:TABLE_PLUGINS"})


def test_residual_strict_args_and_import_mode(tmp_path):
    ini = "[pytest]\naddopts = --strict-markers --import-mode=append\n"
    project = Project(tmp_path, base_files={"pytest.ini": ini})
    got = row_of(project, project.base)["residual"]
    assert "STRICT_ARGS" in got and "IMPORT_MODE" in got


def test_residual_is_unknown_when_the_input_is_unusable(tmp_path):
    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET, interpreter="/no/such/python")
    code, row, _ = project.shadow(raw)
    assert code == 0 and row["residual"] == ["unknown"]


# review round 1: xunit names bound at run time are caught by the runner (static cannot see them)
BINDINGS = {
    "call": "def make():\n    return lambda *a: None\n\n\nsetup_module = make()\n",
    "attr": "import types\n\nhelper = types.SimpleNamespace(f=lambda *a: None)\nsetup_module = helper.f\n",
    "from_import": "from os.path import join as setup_module\n",
    "teardown": "teardown_function = lambda *a: None\n",
}


@pytest.mark.parametrize("kind", list(BINDINGS))
def test_runner_sees_xunit_names_bound_without_def(tmp_path, kind):
    text = BINDINGS[kind] + "\n\ndef test_a():\n    pass\n"
    assert xunit_setup_na(module(tmp_path, text), "test_a") is False  # static is blind here
    got = run_runner(tmp_path, text, "test_a")
    assert (got["status"], got["detail"]) == ("NA", "XUNIT_SETUP")


def test_runner_sees_xunit_on_class_inheritance_and_module(tmp_path):
    base = "import unittest\n\n\nclass B(unittest.TestCase):\n    setup_class = classmethod(lambda c: None)\n\n\n"
    inherited = base + "class T(B):\n    def test_a(self):\n        pass\n"
    assert run_runner(tmp_path, inherited, "T.test_a")["detail"] == "XUNIT_SETUP"
    mod = "import unittest\n\nsetup_function = lambda *a: None\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
    assert run_runner(tmp_path, mod, "T.test_a")["detail"] == "XUNIT_SETUP"


def test_setup_attribute_on_the_function_or_method_object_is_still_judged(tmp_path):
    """pytest runs no setup from these two places, so the observer must not call it NA."""
    func = "def test_a():\n    pass\n\n\ntest_a.setup = lambda *a: None\n"
    assert run_runner(tmp_path, func, "test_a")["status"] == "PASS"
    meth = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n\n    test_a.setup_method = 1\n"
    assert run_runner(tmp_path, meth, "T.test_a")["status"] == "PASS"


@pytest.mark.sandbox
def test_ini_filterwarnings_error_is_observed_pass_and_listed_as_residual(tmp_path):
    """Measured round-3 row 4: the ini form is not NA; cw FAIL, observer PASS, INI_KEYS."""
    warn = "import warnings\n\n\ndef add(a, b):\n    warnings.warn('w')\n    return a + b\n"
    ini = "[pytest]\nfilterwarnings =\n    error\n"
    project = Project(tmp_path, base_files={"pytest.ini": ini})
    cand = project.candidate({"src/calc.py": warn})
    verdict = project.cw_verdict(cand, TARGET)
    code, row, _ = project.shadow(project.shadow_input(cand, TARGET, cw_verdict=verdict))
    assert (code, verdict, row["observer"]) == (0, "FAIL", "PASS")
    assert "INI_KEYS" in row["residual"]


def test_an_na_row_keeps_its_residual(tmp_path):
    ini = "[pytest]\nfilterwarnings =\n    error\n"
    project = Project(tmp_path, base_files={"pytest.ini": ini})
    row = row_of(project, project.base, TARGET + "[5]")
    assert row["observer"] == "NA" and "INI_KEYS" in row["residual"]


def test_runner_still_judges_without_xunit_names(tmp_path):
    plain = "def test_a():\n    pass\n"
    uni = "import unittest\n\n\nclass T(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        pass\n\n    def test_a(self):\n        pass\n"
    assert run_runner(tmp_path, plain, "test_a")["status"] == "PASS"
    assert run_runner(tmp_path, uni, "T.test_a")["status"] == "PASS"


# review round 1: residual defaults to unknown; cw_verdict is required
def test_a_rebuild_failure_leaves_residual_unknown(tmp_path, monkeypatch):
    from experimental.second_observer import cli

    def boom(*a, **k):
        raise RuntimeError("rebuild broke")

    monkeypatch.setattr(cli, "rebuild", boom)
    project = Project(tmp_path)
    row = row_of(project, project.base)
    assert row["observer"] == "OBS_ERROR" and row["residual"] == ["unknown"]


def test_missing_cw_verdict_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET, cw_verdict=None)
    code, row, _ = project.shadow(raw)
    assert row["observer"] == "UNCOMPARABLE" and "cw_verdict" in row["detail"]
