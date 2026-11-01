"""The report-guard descriptor hand-over through the real sandbox (report_channel.py)."""

import sys
from dataclasses import replace

import pytest
from conftest import commit_change

from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox


def run(repo, tmp_path, commit, target):
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", target])
    return run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), commit, (spec,), "r1"
    )[0]


FORGE_FROM_ENV = (
    "import os, json, atexit\n"
    "def test_x():\n    assert False\n"
    "def _forge():\n"
    "    p = os.environ.get('CAIRNWORK_VERDICT_PATH')\n"
    "    n = os.environ.get('CAIRNWORK_VERDICT_NONCE')\n"
    "    if p and n:\n"
    "        json.dump({'nonce': n, 'collected': ['tests/test_f.py::test_x'],\n"
    "                   'outcomes': {'tests/test_f.py::test_x': 'passed'}}, open(p, 'w'))\n"
    "atexit.register(_forge)\n"
)


def test_the_hand_over_is_not_in_the_environment_of_the_code_under_test(repo, tmp_path):
    """The owner path hands the nonce and report over by descriptor and the plugin scrubs the
    guard variables at import, so a test sees none of them (this also proves the descriptors
    reach the child through sandbox-exec and that the run is judged from them)."""
    head = commit_change(
        repo,
        {
            "tests/test_env_clean.py": (
                "import os\n"
                "def test_clean():\n"
                "    bad = [k for k in os.environ if 'VERDICT' in k or 'GUARD' in k]\n"
                "    assert not bad, bad\n"
            )
        },
    )
    result = run(repo, tmp_path, head, "tests/test_env_clean.py")
    assert result.returncode == 0 and result.observation.passed and result.ran
    assert result.outcomes == {"tests/test_env_clean.py::test_clean": "passed"}


def test_forging_a_pass_from_the_environment_no_longer_works(repo, tmp_path):
    head = commit_change(repo, {"tests/test_f.py": FORGE_FROM_ENV})
    result = run(repo, tmp_path, head, "tests/test_f.py")
    assert not result.observation.passed
    assert result.outcomes == {"tests/test_f.py::test_x": "failed"}


def test_an_exit_status_that_disagrees_with_the_recorded_session_status_fails(repo, tmp_path):
    """Library code imported by the test ends the process with status 0 after pytest reported a
    failure: the record keeps pytest's own status, and the run fails naming the disagreement."""
    head = commit_change(
        repo,
        {
            "calc.py": (
                "import atexit, os\n"
                "atexit.register(os._exit, 0)\n"
                "def add(a, b):\n    return a - b\n"
            ),
            "tests/test_add.py": (
                "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
            ),
        },
    )
    result = run(repo, tmp_path, head, "tests/test_add.py")
    assert result.returncode == 0 and not result.observation.passed
    assert result.outcomes == {"tests/test_add.py::test_add": "failed"}
    assert result.why == (
        "process exit status 0 disagrees with the recorded session status (exit status 1, 1 failed)"
    )


class _AllPassedChannel:
    """The real channel, but its record says every test passed and pytest agreed."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def read(self):
        verdict = self._real.read()
        outcomes = dict.fromkeys(verdict.outcomes, "passed")
        return replace(verdict, ran=True, outcomes=outcomes, exitstatus=0, testsfailed=0)


def test_a_record_that_says_passed_does_not_pass_a_run_whose_pytest_exited_nonzero(
    repo, tmp_path, monkeypatch
):
    from cairnwork import report_channel

    real_open = report_channel.open_channel
    monkeypatch.setattr(report_channel, "open_channel", lambda d: _AllPassedChannel(real_open(d)))
    head = commit_change(repo, {"tests/test_f.py": "def test_x():\n    assert False\n"})
    result = run(repo, tmp_path, head, "tests/test_f.py")
    assert result.ran and result.outcomes == {"tests/test_f.py::test_x": "passed"}
    assert result.returncode == 1 and not result.observation.passed
    assert "pytest exit code 1" in result.why
