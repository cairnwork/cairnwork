"""#850 PR2 (data_exec_hook.py) without a sandbox: the normal form, the default plugin unchanged
(AC-H1), the record field and its parsing, the install-time self-probe (AC-H7) and the hook's
rules in a plain guard run. Real verifier runs: test_data_exec_hook_runs.py."""

import json
import subprocess
import sys

import pytest

from cairnwork import report_guard
from cairnwork.bytecode_hook import HOOK_SOURCE
from cairnwork.data_exec_hook import FIELD, normalise, plugin_source

NONCE = "n0nce"
DATA = "tests/data/x.py"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("tests/data/x.py", "tests/data/x.py"),
        ("./tests/data/x.py", "tests/data/x.py"),
        ("tests//data/./x.py", "tests/data/x.py"),
        ("Tests\\Data\\X.PY", "tests/data/x.py"),
        ("/abs/root/tests/other/../data/x.py", "abs/root/tests/data/x.py"),
        ("../../tests/data/x.py", "../../tests/data/x.py"),
        ("x.zip/tests/data/x.py", "x.zip/tests/data/x.py"),
    ],
)
def test_the_normal_form_is_pure_text(raw, want):
    assert normalise(raw) == want


def test_ac_h1_no_declared_data_leaves_the_plugin_byte_identical(tmp_path):
    for name, args in (("a", ()), ("b", ((),)), ("c", ([""],))):
        (tmp_path / name).mkdir()
        report_guard.install_plugin(tmp_path / name, *args)
    texts = {(tmp_path / n / f"{report_guard.MODULE}.py").read_bytes() for n in "abc"}
    assert texts == {(HOOK_SOURCE + report_guard._PLUGIN_SOURCE).encode()}
    assert plugin_source(()) == "" and FIELD not in (HOOK_SOURCE + report_guard._PLUGIN_SOURCE)


def test_declared_data_is_written_only_into_the_per_run_plugin(tmp_path):
    report_guard.install_plugin(tmp_path, ["./Tests//data/x.py"])
    text = (tmp_path / f"{report_guard.MODULE}.py").read_text()
    assert text.startswith(HOOK_SOURCE + report_guard._PLUGIN_SOURCE)
    assert "_CW_DATA_PATHS = ('tests/data/x.py',)" in text
    assert "tests/data" not in HOOK_SOURCE + report_guard._PLUGIN_SOURCE


def _record(**extra):
    payload = {"nonce": NONCE, "collected": ["t.py::a"], "outcomes": {"t.py::a": "passed"},
               "exitstatus": 0, "testsfailed": 0, **extra}  # fmt: skip
    return report_guard.parse_verdict(json.dumps(payload).encode(), NONCE)


def test_a_record_with_a_refusal_is_never_a_pass():
    assert _record().ran and _record(**{FIELD: []}).ran
    v = _record(**{FIELD: [DATA]})
    assert v.recorded and not v.ran and v.data_refused == (DATA,)


@pytest.mark.parametrize("bad", ["x", [1], {"a": 1}, None])
def test_a_malformed_refusal_field_is_no_record(bad):
    v = _record(**{FIELD: bad})
    assert not v.recorded and not v.ran


def guard_run(tmp_path, test_text, data_paths=(DATA,), change=None):
    """A plain pytest run (no sandbox) of tests/test_d.py with the plugin and ``data_paths``."""
    files = {"tests/test_d.py": test_text, DATA: "X = 1\n", "pytest.ini": "[pytest]\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    guard, verdict = tmp_path / "guard", tmp_path / "verdict.json"
    guard.mkdir()
    report_guard.install_plugin(guard, data_paths)
    plugin = guard / f"{report_guard.MODULE}.py"
    if change:
        text = plugin.read_text()
        assert change(text) != text
        plugin.write_text(change(text))
    env = {"PATH": "/usr/bin:/bin", report_guard.PATH_ENV: str(verdict),
           report_guard.NONCE_ENV: NONCE, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": str(guard), "PYTHONDONTWRITEBYTECODE": "1"}  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
         "-p", report_guard.MODULE, "tests/test_d.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return done, report_guard.read_verdict(verdict, NONCE)


READS = (
    "import ast, subprocess, sys\n"
    "def test_reads():\n"
    f"    assert ast.parse(open({DATA!r}).read())\n"
    "    subprocess.run([sys.executable, '-c', 'print(1)'], check=True)\n"
)
ZIP = (
    "import pytest\n"
    "def test_zip():\n"
    "    with pytest.raises(BaseException) as e:\n"
    "        exec(compile('X = 1', '/w/x.zip/tests/data/x.py', 'exec'), {})\n"
    "    assert type(e.value).__name__ == 'DataExecRefused'\n"
)


def test_the_live_hook_lets_reading_pass_and_records_an_empty_latch(tmp_path):
    done, v = guard_run(tmp_path, READS)
    assert done.returncode == 0 and v.ran and v.data_refused == (), done.stdout + done.stderr
    assert json.loads((tmp_path / "verdict.json").read_text())[FIELD] == []


def test_a_zip_form_name_is_refused_and_latched(tmp_path):
    done, v = guard_run(tmp_path, ZIP)
    assert v.outcomes == {"tests/test_d.py::test_zip": "passed"}, done.stdout
    assert not v.ran and v.data_refused == ("/w/x.zip/tests/data/x.py",)


def test_a_conftest_that_restores_the_reporter_cannot_hide_the_latch_from_the_hook(tmp_path):
    """The latch lives in the hook, not the reporter: clearing what the reporter can reach
    (its defaults hold no list) leaves the record's refusal in place."""
    body = ZIP + (
        "def test_clear():\n"
        "    import _cairnwork_report_guard as g\n"
        "    for value in g._Reporter.pytest_sessionfinish.__defaults__:\n"
        "        if type(value) is list:\n"
        "            value.clear()\n"
    )
    _, v = guard_run(tmp_path, body)
    assert not v.ran and v.data_refused


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda t: t.replace(
                "                    _latch.append(word)\n                    raise _refuse(",
                "                    (",
                1,
            ),
            id="exec_rule_off",
        ),
        pytest.param(
            lambda t: t.replace("sys.addaudithook(hook)\n    probe", "probe", 1),
            id="hook_never_installed",
        ),
        pytest.param(lambda t: t.replace('event == "exec" or ', "", 1), id="exec_event_off"),
    ],
)
def test_ac_h7_a_data_hook_that_is_not_live_records_nothing(tmp_path, change):
    done, v = guard_run(tmp_path, READS, change=change)
    assert done.returncode != 0 and "data-exec hook is not active" in done.stderr + done.stdout
    assert not v.recorded and not (tmp_path / "verdict.json").exists()
