"""#796 step 1: the guard records a test module whose own import statement failed with "cannot
import name X from <module>" (``collect_import_errors``), the parent re-validates it, and no
verdict changes: ``collect_errors`` still names the collector, so the run still fails closed."""

import json
import subprocess
import sys

import pytest

from cairnwork import report_guard
from cairnwork.collect_import import import_errors
from cairnwork.verifiers import VerifierRun

NONCE = "n0nce"
NODE = "tests/test_x.py"


def _run(tmp_path, files, targets=(NODE,)):
    for name, body in {"pytest.ini": "[pytest]\n", **files}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    guard, verdict = tmp_path / "guard", tmp_path / "verdict.json"
    guard.mkdir()
    report_guard.install_plugin(guard)
    env = {"PATH": "/usr/bin:/bin", report_guard.PATH_ENV: str(verdict),
           report_guard.NONCE_ENV: NONCE, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": str(guard)}  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
         "-p", report_guard.MODULE, *targets],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )  # fmt: skip
    return done, report_guard.read_verdict(verdict, NONCE)


def _unchanged(done, verdict):
    """What the run meant before this step: a failed collector, exit 2, nothing passed."""
    assert done.returncode == 2 and not verdict.ran and verdict.recorded
    assert verdict.collect_errors == (NODE,)
    assert "pytest_exception_interact" not in done.stderr + done.stdout


PKG = {"pkg/__init__.py": "def old(): pass\n"}


def test_a_missing_name_of_an_in_repo_module_is_recorded(tmp_path):
    done, verdict = _run(tmp_path, {**PKG, NODE: "from pkg import new_fn\ndef test_a(): pass\n"})
    _unchanged(done, verdict)  # AC13: collect_errors is as before
    assert verdict.import_errors == ((NODE, "pkg", "pkg/__init__.py"),)


def test_a_record_from_an_older_guard_has_no_field(tmp_path):  # AC13
    record = {"nonce": NONCE, "collected": [], "outcomes": {}, "exitstatus": 2, "testsfailed": 0,
              "collect_errors": [NODE]}  # fmt: skip
    path = tmp_path / "v.json"
    path.write_text(json.dumps(record))
    verdict = report_guard.read_verdict(path, NONCE)
    assert verdict.import_errors == () and verdict.collect_errors == (NODE,)


def test_a_missing_third_party_package_is_not_recorded(tmp_path):  # AC3
    done, verdict = _run(tmp_path, {NODE: "import not_installed_pkg_796\ndef test_a(): pass\n"})
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


def test_a_missing_name_of_a_module_outside_the_checkout_is_not_recorded(tmp_path):  # AC4
    done, verdict = _run(tmp_path, {NODE: "from json import nope\ndef test_a(): pass\n"})
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


def test_a_syntax_error_is_not_recorded(tmp_path):  # AC5
    done, verdict = _run(tmp_path, {NODE: "def test_a(:\n"})
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


def test_an_import_failing_inside_a_repo_module_is_not_recorded(tmp_path):  # AC6
    files = {**PKG, "pkg/a.py": "from pkg import new_fn\n",
             NODE: "import pkg.a\ndef test_a(): pass\n"}  # fmt: skip
    done, verdict = _run(tmp_path, files)
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


def test_a_conftest_import_error_is_not_recorded(tmp_path):  # AC9
    files = {**PKG, "conftest.py": "from pkg import new_fn\n",
             NODE: "def test_a(): pass\n"}  # fmt: skip
    done, verdict = _run(tmp_path, files)
    assert not verdict.ran and verdict.import_errors == ()
    assert "pytest_exception_interact" not in done.stderr + done.stdout


def _forged(message, name="pkg", path="pkg/__init__.py"):
    return (f"import os\nraise ImportError({message!r}, name={name!r},"
            f" path=os.path.abspath({path!r}))\ndef test_a(): pass\n")  # fmt: skip


def test_a_circular_import_is_not_recorded(tmp_path):  # AC12
    text = "cannot import name 'x' from partially initialized module 'pkg' (circular)"
    done, verdict = _run(tmp_path, {**PKG, NODE: _forged(text)})
    _unchanged(done, verdict)
    assert verdict.import_errors == ()
    # the same raise without the circular wording is recorded: the check above is what held
    plain = "cannot import name 'x' from 'pkg'"
    done, verdict = _run(tmp_path / "again", {**PKG, NODE: _forged(plain)})
    assert verdict.import_errors == ((NODE, "pkg", "pkg/__init__.py"),)


def test_a_namespace_package_has_no_path_and_the_hook_does_not_raise(tmp_path):  # AC15
    files = {"ns/data.txt": "x", NODE: "from ns import nope\ndef test_a(): pass\n"}
    done, verdict = _run(tmp_path, files)
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


ODD = {
    "name is None": "raise ImportError('cannot import name x', name=None, path='/x/__init__.py')",
    "path is not a string": "raise ImportError('cannot import name x', name='pkg', path=7)",
    "path is None": "raise ImportError('cannot import name x', name='pkg', path=None)",
    "name is not a string": "raise ImportError('cannot import name x', name=3, path='/x/y.py')",
    "a subclass": "raise ModuleNotFoundError('cannot import name x', name='pkg', path='/x/y.py')",
    "not an import error": "raise ValueError('cannot import name x')",
    "str raising SystemExit": ("class E(ImportError):\n    pass\n"
        "class S(str):\n    def __str__(self):\n        raise SystemExit(5)\n"
        "raise ImportError(S('cannot import name x'), name='pkg', path='/x/y.py')"),
    "fspath raising KeyboardInterrupt": ("class P:\n    def __fspath__(self):\n"
        "        raise KeyboardInterrupt\n    def __str__(self):\n        raise KeyboardInterrupt\n"
        "raise ImportError('cannot import name x', name='pkg', path=P())"),
    "an odd sys.modules entry": ("import sys, types\nsys.modules['pkg'] = types.ModuleType('pkg')\n"
                                 "raise ImportError('cannot import name x', name='pkg', path='')"),
}  # fmt: skip


@pytest.mark.parametrize("why", ODD)
def test_odd_exception_attributes_record_nothing_and_change_nothing(tmp_path, why):  # AC16
    done, verdict = _run(tmp_path, {**PKG, NODE: ODD[why] + "\ndef test_a(): pass\n"})
    _unchanged(done, verdict)
    assert verdict.import_errors == ()


def test_a_test_module_cannot_rebind_what_the_recorder_uses(tmp_path):
    """The os functions are bound when the plugin is imported: rebinding them in the test module
    (which runs later) leaves the record what an untouched run records."""
    raise_ = "import os\ndef _b(*a, **k):\n    raise RuntimeError('rebound')\n"
    rebind = raise_ + "os.path.realpath = os.path.relpath = os.path.isabs = _b\n"
    shape = "raise ImportError(\"cannot import name 'x' from 'pkg'\", name='pkg', path=%s)\n"
    plain = "import os\n" + shape % "os.path.abspath('pkg/__init__.py')"
    forged = (
        rebind.replace("def _b", "_p = os.path.abspath('pkg/__init__.py')\ndef _b") + shape % "_p"
    )
    done, base = _run(tmp_path / "a", {**PKG, NODE: plain + "def test_a(): pass\n"})
    _unchanged(done, base)
    done, got = _run(tmp_path / "b", {**PKG, NODE: forged + "def test_a(): pass\n"})
    _unchanged(done, got)
    assert base.import_errors == ((NODE, "pkg", "pkg/__init__.py"),)
    assert got.import_errors == base.import_errors


def _record_with(entries):
    return {"nonce": NONCE, "collected": [], "outcomes": {}, "exitstatus": 2, "testsfailed": 0,
            "collect_errors": [NODE], "collect_import_errors": entries}  # fmt: skip


GOOD = {"node": NODE, "module": "pkg.sub", "origin": "pkg/sub.py"}


def test_the_parent_drops_any_malformed_entry_and_keeps_the_good_one(tmp_path):  # AC8
    bad = [
        {**GOOD, "origin": "../x.py"}, {**GOOD, "origin": "a/../b.py"},
        {**GOOD, "origin": "/etc/passwd"}, {**GOOD, "origin": ""}, {**GOOD, "origin": "a\\b.py"},
        {**GOOD, "origin": "x" * 400}, {**GOOD, "origin": "p/\n::error::x.py"},
        {**GOOD, "node": "/abs/test_x.py"}, {**GOOD, "node": "../test_x.py"},
        {**GOOD, "node": "tests/test_x.py::t\n"}, {**GOOD, "module": "a" * 101},
        {**GOOD, "module": "a b"}, {**GOOD, "module": ""}, {**GOOD, "module": "x'\n::error::b"},
        {**GOOD, "module": 7}, {**GOOD, "origin": None}, {**GOOD, "node": ["t"]},
        {"node": NODE}, "tests/test_x.py", 3, None,
    ]  # fmt: skip
    got = import_errors(_record_with([*bad, GOOD, GOOD]))
    assert got == ((NODE, "pkg.sub", "pkg/sub.py"),)  # the good one, listed once
    path = tmp_path / "v.json"
    path.write_text(json.dumps(_record_with([*bad, GOOD])))
    assert report_guard.read_verdict(path, NONCE).import_errors == got


def test_a_field_that_is_not_a_list_gives_nothing():
    for value in ({"node": NODE}, "x", 5, None, True):
        assert import_errors({"collect_import_errors": value}) == ()
    assert import_errors({}) == ()


def test_the_parent_reads_at_most_a_bounded_number_of_entries():
    many = [{**GOOD, "module": f"m{n}"} for n in range(5000)]
    assert 0 < len(import_errors(_record_with(many))) <= 100


def test_the_record_does_not_change_whether_the_run_ran(tmp_path):
    entries = [GOOD]
    path = tmp_path / "v.json"
    path.write_text(json.dumps(_record_with(entries)))
    verdict = report_guard.read_verdict(path, NONCE)
    assert not verdict.ran and verdict.collect_errors == (NODE,)


def test_a_verifier_run_carries_the_field_with_an_empty_default():
    assert VerifierRun.__dataclass_fields__["import_errors"].default == ()
