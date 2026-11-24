"""#1413 PR-1: collect-only records name test callables left on live uncollected classes."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import report_guard
from cairnwork.collect_reader import Collected, read_collected
from cairnwork.verifier_spec import VerifierSpec

NONCE = "exposed-1413"
TARGET = "tests/test_dynamic.py"
HELPERS = (
    "setattr(H, 'test_orphan', f)",
    "s = setattr\ns(H, 'test_orphan', f)",
    "import functools\nfunctools.partial(setattr, H)('test_orphan', f)",
    "exec(\"setattr(H, 'test_orphan', f)\")",
    "H = type('H', (), {'__module__': __name__, 'test_orphan': f})",
    "class Meta(type):\n"
    "    def __new__(m, n, b, d):\n"
    "        d.setdefault('__module__', __name__)\n"
    "        return super().__new__(m, n, b, d)\n"
    "class H(metaclass=Meta):\n    test_orphan = f",
    "class H:\n    test_orphan = f\nheld = [H]\ndel H",
)


def collect(
    tmp_path: Path,
    body: str,
    *,
    fail_hook: str = "",
    extra=(),
    collect_files=(),
    cap=None,
    pair_cap=None,
):
    test_file = tmp_path / TARGET
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    for name, text in extra:
        (test_file.parent / name).write_text(text)
    guard, record_path = tmp_path / "guard", tmp_path / "record.json"
    guard.mkdir()
    report_guard.install_plugin(guard)
    plugin_file = guard / f"{report_guard.MODULE}.py"
    if fail_hook:
        source = plugin_file.read_text()
        source = source.replace(
            "return _exposed_uncollected(session)",
            f"return (_ for _ in ()).throw({fail_hook})",
            1,
        )
        plugin_file.write_text(source)
    if cap is not None:
        source = plugin_file.read_text()
        plugin_file.write_text(
            source.replace("_MAX_REPORT_BYTES = ", f"_MAX_REPORT_BYTES = {cap} or ", 1)
        )
    if pair_cap is not None:
        source = plugin_file.read_text()
        plugin_file.write_text(
            source.replace("_MAX_PAIRS = 2000000", f"_MAX_PAIRS = {pair_cap}", 1)
        )
    env = {
        **os.environ,
        "PATH": "/usr/bin:/bin",
        report_guard.PATH_ENV: str(record_path),
        report_guard.NONCE_ENV: NONCE,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "-c",
            "pytest.ini",
            "-p",
            report_guard.MODULE,
            TARGET,
            *collect_files,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode in (0, 5), done.stdout + done.stderr
    return json.loads(record_path.read_text())


@pytest.mark.parametrize("setup", HELPERS)
def test_real_collect_only_finds_runtime_assembled_class_attributes(tmp_path, setup):
    body = (
        f"def test_orphan():\n    pass\nf = test_orphan\ndel test_orphan\nclass H: pass\n{setup}\n"
    )
    record = collect(tmp_path, body)
    assert record["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]


def test_collected_class_and_parameterized_callable_are_not_exposed(tmp_path):
    body = (
        "import pytest\n"
        "class TestA:\n"
        "    def test_k(self): pass\n"
        "    @pytest.mark.parametrize('value', [1, 2])\n"
        "    def test_param(self, value): pass\n"
    )
    record = collect(tmp_path, body)
    assert record["uncollected_exposed"] == []
    assert f"{TARGET}::TestA::test_k" in record["collected"]
    assert f"{TARGET}::TestA::test_param[1]" in record["collected"]


def test_honest_dynamic_helpers_do_not_expose_a_test_callable(tmp_path):
    body = (
        "import re\n"
        "def test_honest(monkeypatch):\n"
        "    monkeypatch.setattr(re, 'compile', re.compile)\n"
        "    assert re.compile('ok').match('ok')\n"
    )
    assert collect(tmp_path, body)["uncollected_exposed"] == []


@pytest.mark.parametrize("error", ["RuntimeError('hook')", "SystemExit(3)", "KeyboardInterrupt()"])
def test_hook_exception_omits_only_the_optional_record_key(tmp_path, error):
    body = (
        "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\n"
        "class H: pass\nsetattr(H, 'test_orphan', f)\n"
    )
    normal = collect(tmp_path / "normal", body)
    failed = collect(tmp_path / "failed", body, fail_hook=error)
    assert normal["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]
    assert "uncollected_exposed" not in failed
    assert {key: value for key, value in normal.items() if key != "uncollected_exposed"} == failed


def reader_run(exposed=None, **changes):
    fields = {
        "spec": VerifierSpec("collect", (TARGET, "tests/test_other.py"), collect_only=True),
        "returncode": 0,
        "forged": (),
        "timed_out": False,
        "survivors": 0,
        "mutated": (),
        "report_too_big": False,
        "log_too_big": False,
        "recorded": True,
        "outcomes": {},
        "collected": (f"{TARGET}::test_ok",),
        "collect_errors": (),
        "collect_skipped": (),
        "collection_finished": True,
        "uncollected_exposed": exposed,
    }
    return SimpleNamespace(**(fields | changes))


def test_reader_absent_exposed_fact_is_none():
    assert read_collected(reader_run()).exposed is None


@pytest.mark.parametrize(
    "exposed",
    [
        "not-a-list",
        ("tests/test_dynamic.py::H::test_x", 3),
        ("tests/test_dynamic.py::H::test_x", "tests/test_dynamic.py::H::test_x"),
        tuple(f"tests/test_dynamic.py::H::test_{index}" for index in range(5001)),
        ("tests/outside.py::H::test_x",),
        ("tests/test_dynamic.py::H::" + "x" * 1025,),
    ],
)
def test_reader_invalid_exposed_fact_is_none(exposed):
    assert read_collected(reader_run(exposed=exposed)).exposed is None


def test_reader_rejects_exposed_fact_for_error_or_skipped_files():
    exposed = ("tests/test_other.py::H::test_x",)
    error = reader_run(exposed, returncode=2, collect_errors=("tests/test_other.py::H",))
    skipped = reader_run(exposed, collect_skipped=("tests/test_other.py::H",))
    assert read_collected(error).exposed is None
    assert read_collected(skipped).exposed is None


def test_reader_sorts_valid_entries_without_changing_other_fields():
    values = ("tests/test_other.py::Z::test_z", f"{TARGET}::H::test_x")
    got = read_collected(reader_run(values))
    assert got == Collected(ids=(f"{TARGET}::test_ok",), exposed=tuple(sorted(values)))


@pytest.mark.parametrize(
    "bad",
    [
        f"{TARGET}",
        f"{TARGET}::",
        f"{TARGET}::H::",
        f"{TARGET}::::test_x",
        f"{TARGET}::H::test_x[1]",
        f"{TARGET}::test_ok",
        f"{TARGET}::H::test_x\n::error::x",
    ],
)
def test_reader_rejects_malformed_exposed_nodeids(bad):
    assert read_collected(reader_run((bad,))).exposed is None


@pytest.mark.parametrize(
    "good",
    [
        f"{TARGET}::H::test_x",
        f"{TARGET}::Outer::Inner::test_x",
        "tests/test_other.py::A::B::test_y",
    ],
)
def test_reader_accepts_class_exposed_nodeids(good):
    assert read_collected(reader_run((good,))).exposed == (good,)


def test_reader_rejects_non_ascii_and_whitespace():
    for value in (f"{TARGET}::H::tést", f"{TARGET}::H::test x", f"{TARGET}::H::test\tx"):
        assert read_collected(reader_run((value,))).exposed is None


def test_candidate_scan_is_collect_only(tmp_path):
    body = "def test_ok(): pass\n"
    test_file = tmp_path / TARGET
    test_file.parent.mkdir(parents=True)
    test_file.write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard, record_path = tmp_path / "guard", tmp_path / "record.json"
    guard.mkdir()
    report_guard.install_plugin(guard)
    env = {
        **os.environ,
        report_guard.PATH_ENV: str(record_path),
        report_guard.NONCE_ENV: NONCE,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            "pytest.ini",
            "-p",
            report_guard.MODULE,
            TARGET,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "uncollected_exposed" not in json.loads(record_path.read_text())


@pytest.mark.parametrize("escape", ["SystemExit(73)", "SystemExit(0)", "KeyboardInterrupt()"])
# pytest's own collection already calls a metaclass __getattr__/__getattribute__ on every
# module-level class, so those cannot be new to this hook; only hooks it never calls are listed.
@pytest.mark.parametrize("hook", ["__dir__", "__hash__", "__eq__", "__module__"])
def test_candidate_metaclass_hooks_are_never_called(tmp_path, hook, escape):
    body = (
        "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\n"
        "class M(type):\n"
        f"    def {hook}(self, *args): raise {escape}\n"
        "class H(metaclass=M): test_orphan = f\n"
    )
    record = collect(tmp_path, body)
    assert record["collection_finished"] is True
    assert record["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]


@pytest.mark.parametrize("assignment", ["staticmethod(f)", "classmethod(f)"])
def test_uncollected_static_and_class_methods_are_exposed(tmp_path, assignment):
    body = (
        "def test_x(self=None): pass\nf = test_x\ndel test_x\n"
        f"class Helper: test_x = {assignment}\n"
    )
    assert collect(tmp_path, body)["uncollected_exposed"] == [f"{TARGET}::Helper::test_x"]


def test_non_callable_shadow_hides_inherited_test(tmp_path):
    body = "class TestA:\n    def test_k(self): pass\nclass Helper(TestA): test_k = None\n"
    assert collect(tmp_path, body)["uncollected_exposed"] == []


def test_non_callable_attribute_does_not_count(tmp_path):
    assert collect(tmp_path, "class Helper: test_x = 3\n")["uncollected_exposed"] == []


@pytest.mark.parametrize("raise_", ["pytest.skip('x')", "pytest.fail('x')", "SystemExit(0)"])
def test_descriptor_get_is_never_called(tmp_path, raise_):
    body = (
        "import pytest\nclass D:\n    def __call__(self): pass\n"
        f"    def __get__(self, obj, owner=None): raise {raise_}\n"
        "class Helper: test_x = D()\n"
    )
    record = collect(tmp_path, body)
    assert record["collection_finished"] is True
    assert record["uncollected_exposed"] == [f"{TARGET}::Helper::test_x"]


def test_class_alive_only_through_an_instance_or_closure_is_exposed(tmp_path):
    body = (
        "def test_x(): pass\nf = test_x\ndel test_x\n"
        "def make():\n    class H: test_x = f\n    return lambda: H\n"
        "keep = make()\nobj = type('I', (), {'test_x': f})()\n"
    )
    assert collect(tmp_path, body)["uncollected_exposed"] == [
        f"{TARGET}::I::test_x",
        f"{TARGET}::make::<locals>::H::test_x",
    ]


def test_test_false_and_uncollected_subclass_are_exposed(tmp_path):
    body = (
        "class TestA:\n    def test_k(self): pass\n"
        "class Helper(TestA): pass\n"
        "class TestOff:\n    __test__ = False\n    def test_x(self): pass\n"
    )
    assert collect(tmp_path, body)["uncollected_exposed"] == [
        f"{TARGET}::Helper::test_k",
        f"{TARGET}::TestOff::test_x",
    ]


def test_collected_bracket_ids_nested_unittest_and_static_tests_are_not_exposed(tmp_path):
    body = (
        "import pytest, unittest\n"
        "class TestA:\n"
        "    @pytest.mark.parametrize('v', [pytest.param(1, id='a[b]'), "
        "pytest.param(2, id='c::d')])\n"
        "    def test_k(self, v): pass\n"
        "    @staticmethod\n    def test_s(): pass\n"
        "    @classmethod\n    def test_c(cls): pass\n"
        "    class TestInner:\n        def test_i(self): pass\n"
        "class TestU(unittest.TestCase):\n    def test_u(self): pass\n"
    )
    record = collect(tmp_path, body)
    assert f"{TARGET}::TestA::TestInner::test_i" in record["collected"]
    assert record["uncollected_exposed"] == []


def test_class_from_an_imported_non_target_module_is_not_listed(tmp_path):
    helper = "def test_x(): pass\nclass H: test_x = test_x\n"
    body = "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\nimport helper_mod\n"
    record = collect(tmp_path, body, extra=[("helper_mod.py", helper)])
    assert record["uncollected_exposed"] == []


def test_glob_python_functions_option_is_honoured(tmp_path):
    body = "def check_x(): pass\nf = check_x\ndel check_x\nclass H: check_x = f\n"
    record = collect(
        tmp_path, body, extra=[("../pytest.ini", "[pytest]\npython_functions = *_x\n")]
    )
    assert record["uncollected_exposed"] == [f"{TARGET}::H::check_x"]


def test_fact_dropped_when_the_record_would_exceed_the_report_cap(tmp_path):
    body = "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\nclass H: test_orphan = f\n"
    normal = collect(tmp_path / "normal", body)
    capped = collect(tmp_path / "capped", body, cap=1)
    assert normal["uncollected_exposed"] == [f"{TARGET}::H::test_orphan"]
    assert "uncollected_exposed" not in capped
    assert {key: value for key, value in normal.items() if key != "uncollected_exposed"} == capped
