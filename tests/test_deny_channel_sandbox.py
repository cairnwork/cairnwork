"""#797 step 1, real runs: a base run on macOS records its sandbox denials (deny_channel.py) in the
result's logs, and no verdict changes. A denial caught inside the test, or made by a child process,
is seen through the run's tag."""

import os
import shutil
import sys

import pytest
from conftest import commit_change
from test_uncomparable_paths import FIX_DOUBLE, run_cli

from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(sys.platform != "darwin", reason="the deny channel is macOS only"),
]

DENIED = "/private/etc/protocols"  # exists, and the base policy denies reading it
PLAIN = "def test_regression():\n    from calc import double\n    assert double(2) == 4\n"
CAUGHT = (
    "def test_regression():\n"
    "    try:\n"
    f"        open({DENIED!r}).close()\n"
    "    except OSError:\n"
    "        pass\n"
    "    from calc import double\n"
    "    assert double(2) == 4\n"
)
CHILD = (
    "import subprocess\n"
    "def test_regression():\n"
    f"    subprocess.run(['/bin/cat', {DENIED!r}], capture_output=True, check=False)\n"
    "    from calc import double\n"
    "    assert double(2) == 4\n"
)


def _evidence(out) -> list[str]:
    """The run's one evidence line. Checked after the verdict: a flood from any process on the host
    makes it UNAVAILABLE by design (whole-stream volume), and then the denial cannot be judged."""
    (line,) = [line for line in out["logs"] if line.startswith("base deny evidence ")]
    if "UNAVAILABLE: stream volume over the cap" in line:
        pytest.skip(f"the host flooded the deny log during the run: {line}")
    return [line]


def test_a_plain_base_failure_records_evidence_without_that_path(project, state):
    code, out = run_cli(project, state, PLAIN, {"src/calc.py": FIX_DOUBLE})
    assert code == 0 and out["status"] == "VERIFIED", out  # unchanged
    (line,) = _evidence(out)
    # CLEAN, or DENIED by the interpreter alone (an editable venv's src dir, step 1 data)
    assert line.split(": ")[1] in ("CLEAN", "DENIED") and DENIED not in line, line


def test_a_denial_the_test_catches_is_recorded_and_changes_no_verdict(project, state):
    code, out = run_cli(project, state, CAUGHT, {"src/calc.py": FIX_DOUBLE})
    assert code == 0 and out["status"] == "VERIFIED", out  # unchanged: step 1 only records
    (line,) = _evidence(out)
    assert ": DENIED: " in line and f"file-read-data {DENIED}" in line, line


def test_a_child_process_denial_carries_the_run_tag(project, state):
    code, out = run_cli(project, state, CHILD, {"src/calc.py": FIX_DOUBLE})
    assert code == 0 and out["status"] == "VERIFIED", out
    (line,) = _evidence(out)
    assert ": DENIED: " in line and f"file-read-data {DENIED}" in line, line


# run_verifiers directly: the owner and the verifier account, a base run and any other run. The
# flood test runs last: its lines can still arrive during the next test's window.
BURST = (
    "import socket\n"
    "def test_d():\n"
    "    for _ in range(2500):\n"
    "        socket.socket().connect_ex(('127.0.0.1', 9))\n"
)
SPREAD = (  # distinct denials, which the kernel cannot fold: the stream file grows fast
    "import socket, time\n"
    "def test_d():\n"
    "    end, n = time.monotonic() + 8, 0\n"
    "    while time.monotonic() < end:\n"
    "        n += 1\n"
    "        socket.socket().connect_ex(('127.0.0.1', 1 + n % 60000))\n"
)


def _run(repo, tmp_path, test, deny=True, account=None):
    head = commit_change(repo, {"tests/test_d.py": test})
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_d.py"])
    root, python = (
        (tmp_path / "runs", sys.executable) if account is None else (account.run_root, None)
    )
    ctx = VerifyContext(repo, root, python=python, account=account)
    (run,) = run_verifiers(ctx, head, (spec,), "r" + os.urandom(6).hex(), deny=deny)
    if account is not None:  # the account's run root is shared: leave nothing behind
        shutil.rmtree(run.log_path.parent, ignore_errors=True)
    return run


def test_a_run_that_is_not_a_base_run_records_nothing(repo, tmp_path):
    assert (
        _run(repo, tmp_path, CAUGHT.replace("test_regression", "test_d"), deny=False).deny_evidence
        == ""
    )


@pytest.mark.verifier_account
def test_the_verifier_accounts_denial_is_recorded(repo, tmp_path):
    test = CAUGHT.replace("test_regression", "test_d")  # fails on ImportError: no calc here
    run = _run(repo, tmp_path, test, account=VerifierAccount())
    if "volume over the cap" in run.deny_evidence:  # another process's burst: once more
        run = _run(repo, tmp_path, test + "# again\n", account=VerifierAccount())
    assert (
        run.deny_evidence.startswith("DENIED: ") and f"file-read-data {DENIED}" in run.deny_evidence
    )


def test_a_base_that_floods_the_log_itself_is_unavailable(repo, tmp_path):  # AC-burst-own
    run = _run(repo, tmp_path, BURST)
    assert run.deny_evidence.startswith("UNAVAILABLE: stream volume over the cap"), (
        run.deny_evidence
    )


def test_a_base_making_many_distinct_denials_stays_bounded(repo, tmp_path):
    run = _run(repo, tmp_path, SPREAD)
    assert run.deny_evidence.startswith("UNAVAILABLE: "), run.deny_evidence
    assert "byte cap" in run.deny_evidence or "volume" in run.deny_evidence, run.deny_evidence
