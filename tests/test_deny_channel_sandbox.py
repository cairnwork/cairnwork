"""#797 step 1, real runs: a base run on macOS records its sandbox denials (deny_channel.py) in the
result's logs, and no verdict changes. A denial caught inside the test, or made by a child process,
is seen through the run's tag."""

import os
import re
import shutil
import sys
import warnings

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
HIT = ("file-read-data", DENIED)
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


EMPTY = "UNAVAILABLE: a canary line is missing (lines 0, peak 0/s)"  # #879: the host log, idle


def _evidence(out) -> list[str]:
    """The run's one evidence line. Checked after the verdict: a flood from any process on the host
    makes it UNAVAILABLE by design (whole-stream volume), and then the denial cannot be judged."""
    (line,) = [line for line in out["logs"] if line.startswith("base deny evidence ")]
    if "UNAVAILABLE: stream volume over the cap" in line:
        pytest.skip(f"the host flooded the deny log during the run: {line}")
    return [line]


def _verified(project, state, tests):
    """``run_cli`` with the fix; once more when the host's log delivered no line at all (#879). A
    second empty window fails: a channel that never sees its canaries must not pass as a skip."""
    for again in ("", "# again\n"):
        code, out = run_cli(project, state, tests + again, {"src/calc.py": FIX_DOUBLE})
        assert code == 0 and out["status"] == "VERIFIED", out  # unchanged: step 1 only records
        if not again and any(EMPTY in line for line in out["logs"]):
            continue
        assert any(
            line.startswith("second base deny evidence ") and "-s/" in line for line in out["logs"]
        ), out["logs"]
        return _evidence(out)
    raise AssertionError("unreachable")


def test_a_plain_base_failure_records_evidence_without_that_path(project, state, repo, tmp_path):
    (line,) = _verified(project, state, PLAIN)
    # CLEAN, or DENIED by the interpreter alone (an editable venv's src dir, step 1 data)
    assert line.split(": ")[1] in ("CLEAN", "DENIED"), line
    run = _run_evidence(repo, tmp_path, PLAIN.replace("test_regression", "test_d"))
    assert run.deny_evidence.startswith(("CLEAN", "DENIED")), run.deny_evidence  # judged
    assert HIT not in run.deny_denials, run.deny_evidence


def test_a_denial_the_test_catches_is_recorded_and_changes_no_verdict(
    project, state, repo, tmp_path
):
    (line,) = _verified(project, state, CAUGHT)
    assert ": DENIED: " in line, line
    run = _run_evidence(repo, tmp_path, CAUGHT.replace("test_regression", "test_d"))
    assert HIT in run.deny_denials, run.deny_evidence


def test_a_child_process_denial_carries_the_run_tag(project, state, repo, tmp_path):
    (line,) = _verified(project, state, CHILD)
    assert ": DENIED: " in line, line
    run = _run_evidence(repo, tmp_path, CHILD.replace("test_regression", "test_d"))
    assert HIT in run.deny_denials, run.deny_evidence


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


def _run_evidence(repo, tmp_path, test):
    """Retry an empty canary window once, and skip when host log volume prevents judgment."""
    for again in ("", "# again\n"):
        run = _run(repo, tmp_path, test + again)
        if not again and EMPTY in run.deny_evidence:
            continue
        if "UNAVAILABLE: stream volume over the cap" in run.deny_evidence:
            pytest.skip(f"the host flooded the deny log during the run: {run.deny_evidence}")
        if EMPTY in run.deny_evidence:
            raise AssertionError(f"canary line still missing after retry: {run.deny_evidence}")
        return run
    raise AssertionError("unreachable")


def test_a_run_that_is_not_a_base_run_records_nothing(repo, tmp_path):
    assert (
        _run(repo, tmp_path, CAUGHT.replace("test_regression", "test_d"), deny=False).deny_evidence
        == ""
    )


BURST_PEAK = 100  # lines/s: #991 saw the run's own denial missing beside another process's burst


def _peak(evidence: str) -> int:
    match = re.search(r"peak (\d+)/s\)$", evidence)
    return int(match.group(1)) if match else 0


@pytest.mark.verifier_account
def test_the_verifier_accounts_denial_is_recorded(repo, tmp_path):
    test = CAUGHT.replace("test_regression", "test_d")  # fails on ImportError: no calc here
    run = _run(repo, tmp_path, test, account=VerifierAccount())
    over = "volume over the cap" in run.deny_evidence  # another process's burst
    if over or (HIT not in run.deny_denials and _peak(run.deny_evidence) > BURST_PEAK):
        # #991: below the cap, a burst has also lost the run's own denial: once more, said aloud
        warnings.warn(f"#991 rerun, first run: {run.deny_evidence}", stacklevel=1)
        run = _run(repo, tmp_path, test + "# again\n", account=VerifierAccount())
    # #991: the summary names only the first MAX_LISTED denials, and the account's own start-up
    # denials can fill it first; the exact set holds every one the run made
    assert run.deny_evidence.startswith("DENIED: "), run.deny_evidence
    assert HIT in run.deny_denials, run.deny_evidence


def test_a_base_that_floods_the_log_itself_is_unavailable(repo, tmp_path):  # AC-burst-own
    # #1102: under a heavily loaded host the burst can be slowed or its lines delayed, so the same
    # flood reads "stalled" or "canary missing" once (still UNAVAILABLE, never judged). One more run
    # for that; the volume reason is still required, and a second miss fails.
    run = _run(repo, tmp_path, BURST)
    if not run.deny_evidence.startswith("UNAVAILABLE: stream volume over the cap"):
        assert run.deny_evidence.startswith("UNAVAILABLE: "), run.deny_evidence  # never judged
        run = _run(repo, tmp_path, BURST + "# again\n")
    assert run.deny_evidence.startswith("UNAVAILABLE: stream volume over the cap"), (
        run.deny_evidence
    )


def test_a_base_making_many_distinct_denials_stays_bounded(repo, tmp_path):
    run = _run(repo, tmp_path, SPREAD)
    assert run.deny_evidence.startswith("UNAVAILABLE: "), run.deny_evidence
    assert "byte cap" in run.deny_evidence or "volume" in run.deny_evidence, run.deny_evidence
