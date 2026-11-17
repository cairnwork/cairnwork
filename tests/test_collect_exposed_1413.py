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


def collect(tmp_path: Path, body: str, *, fail_hook: bool = False):
    test_file = tmp_path / TARGET
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard, record_path = tmp_path / "guard", tmp_path / "record.json"
    guard.mkdir()
    report_guard.install_plugin(guard)
    plugin_file = guard / f"{report_guard.MODULE}.py"
    if fail_hook:
        source = plugin_file.read_text()
        source = source.replace(
            "return _exposed_uncollected(session)",
            "return (_ for _ in ()).throw(RuntimeError('simulated hook error'))",
            1,
        )
        plugin_file.write_text(source)
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


def test_hook_exception_omits_only_the_optional_record_key(tmp_path):
    body = (
        "def test_orphan(): pass\nf = test_orphan\ndel test_orphan\n"
        "class H: pass\nsetattr(H, 'test_orphan', f)\n"
    )
    normal = collect(tmp_path / "normal", body)
    failed = collect(tmp_path / "failed", body, fail_hook=True)
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
