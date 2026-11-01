"""pytest's own session status in the report guard's record (#276): the record carries the exit
status and failure count pytest computed, a run passes only when they and the process agree, and
what the plugin calls on the record path is bound when it is imported."""

import json
import subprocess
import sys

import pytest

from cairnwork import report_channel, report_guard
from cairnwork.reasons import why_not_passed

NONCE = "n0nce"
PASSING = {
    "nonce": NONCE, "collected": ["t.py::a"], "outcomes": {"t.py::a": "passed"},
    "exitstatus": 0, "testsfailed": 0,
}  # fmt: skip


def _parse(payload):
    return report_guard.parse_verdict(json.dumps(payload).encode(), NONCE)


def test_a_record_where_pytest_agrees_every_test_passed_is_a_pass():
    v = _parse(PASSING)
    assert v.ran and v.recorded and (v.exitstatus, v.testsfailed) == (0, 0)


@pytest.mark.parametrize("missing", ["exitstatus", "testsfailed"])
def test_a_record_without_pytests_own_status_is_not_a_record(missing):
    v = _parse({k: v for k, v in PASSING.items() if k != missing})
    assert not v.ran and not v.recorded and v.outcomes == {}


@pytest.mark.parametrize("field", ["exitstatus", "testsfailed"])
@pytest.mark.parametrize("bad", [False, True, None, "0", 0.0, [0]])
def test_pytests_own_status_must_be_an_exact_int(field, bad):
    v = _parse(PASSING | {field: bad})
    assert not v.ran and not v.recorded and v.outcomes == {}


@pytest.mark.parametrize(("exitstatus", "testsfailed"), [(1, 0), (0, 1), (1, 1), (2, 0)])
def test_every_outcome_passed_is_not_a_pass_when_pytest_says_otherwise(exitstatus, testsfailed):
    v = _parse(PASSING | {"exitstatus": exitstatus, "testsfailed": testsfailed})
    assert v.recorded and not v.ran and v.outcomes == {"t.py::a": "passed"}
    assert (v.exitstatus, v.testsfailed) == (exitstatus, testsfailed)


BASE = {
    "ran_sandboxed": True, "timed_out": False, "mutated": (), "survivors": 0,
    "log_too_big": False, "tests_ran": False,
}  # fmt: skip
DISAGREES = "disagrees with the recorded session status"


@pytest.mark.parametrize(
    ("returncode", "exitstatus", "testsfailed", "why"),
    [
        (0, 1, 1, f"process exit status 0 {DISAGREES} (exit status 1, 1 failed)"),
        (0, 0, 1, f"process exit status 0 {DISAGREES} (exit status 0, 1 failed)"),
        (
            1,
            0,
            0,
            f"process exit status 1 {DISAGREES} (exit status 0, 0 failed); pytest exit code 1",
        ),
        (1, 1, 1, "pytest exit code 1"),
        (0, None, None, "pytest passed but the guard saw no test run"),
    ],
)
def test_the_reason_names_an_exit_status_that_disagrees_with_the_record(
    returncode, exitstatus, testsfailed, why
):
    status = {"exitstatus": exitstatus, "testsfailed": testsfailed}
    assert why_not_passed(**BASE, returncode=returncode, **status) == why


# ---- the plugin, end to end (descriptor hand-over, no sandbox) ----------------------------


def _run(tmp_path, test_body):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(test_body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    channel = report_channel.open_channel(tmp_path)
    try:
        env = {
            "PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(guard), **channel.env(),
        }  # fmt: skip
        argv = [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
            "-p", report_guard.MODULE, "tests/test_x.py",
        ]  # fmt: skip
        done = subprocess.run(
            argv, cwd=tmp_path, env=env, capture_output=True, pass_fds=channel.pass_fds
        )
        return done, channel.read()
    finally:
        channel.close()


def test_the_plugin_records_pytests_own_status_for_a_failing_run(tmp_path):
    done, verdict = _run(tmp_path, "def test_a():\n    assert False\ndef test_b():\n    pass\n")
    assert done.returncode == 1 and verdict.recorded and not verdict.ran
    assert (verdict.exitstatus, verdict.testsfailed) == (1, 1)


# rebinds json.dumps so that any record it encodes says every test passed and pytest agreed
REBIND_DUMPS = (
    "import json\n"
    "_dumps = json.dumps\n"
    "def _all_passed(obj, *args, **kwargs):\n"
    "    if isinstance(obj, dict) and 'outcomes' in obj:\n"
    "        obj = dict(obj, outcomes=dict.fromkeys(obj['outcomes'], 'passed'),\n"
    "                   exitstatus=0, testsfailed=0)\n"
    "    return _dumps(obj, *args, **kwargs)\n"
    "json.dumps = _all_passed\n"
)
# rebinds a helper of the reporter class so that every outcome it keeps is passed
REBIND_HELPER = (
    "import sys\n"
    f"_reporter = sys.modules[{report_guard.MODULE!r}]._Reporter\n"
    "def _keep_passed(self, nodeid, outcome):\n"
    "    self._outcomes[nodeid] = 'passed'\n"
    "_reporter._keep_worst = _keep_passed\n"
)


@pytest.mark.parametrize("rebind", [REBIND_DUMPS, REBIND_HELPER], ids=["json.dumps", "helper"])
def test_rebinding_what_the_plugin_calls_after_import_does_not_change_the_record(tmp_path, rebind):
    """The plugin binds json.dumps, the os writes and its own helpers when it is imported, so a
    test module that rebinds them later leaves the record as pytest reported the run."""
    done, verdict = _run(tmp_path, rebind + "def test_x():\n    assert False\n")
    assert done.returncode == 1, done.stdout
    assert verdict.recorded and not verdict.ran
    assert verdict.outcomes == {"tests/test_x.py::test_x": "failed"}
    assert (verdict.exitstatus, verdict.testsfailed) == (1, 1)
