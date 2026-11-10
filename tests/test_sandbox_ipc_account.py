"""#311 under the verifier account, the only mode where a blackbox check can pass.

Cross-run carry for real: verify #1's pytest phase, running as the account, leaves the answer it
read in the tests as a semaphore's count; verify #2 of another candidate reads it in its blackbox
phase, also as the account. Had it carried, the check would PASS and the wrong candidate would be
VERIFIED. On macOS the blackbox profile refuses the open; under bwrap (Linux CI's
test-linux-verifier-account) the semaphore died with verify #1's own /dev/shm.

That job is also the one CI run on real bwrap that selects this file, so the bwrap side of the
policy witness (objects left outside are absent inside: ENOENT) runs here too.
"""

import json
import subprocess

import pytest
from test_sandbox_ipc import (
    READ,
    WRITE,
    _declaration,
    _seatbelt,
    _source,
    _verify,
    leftovers,  # noqa: F401 - the fixture, shared
)
from test_sandbox_ipc import (
    test_the_blackbox_policy_reaches_no_leftover as _no_leftover,
)

from cairnwork.verifier_account import VerifierAccount

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]

WRONG = "def add(a, b):\n    return a + b + 1\n"


@pytest.fixture
def account_state(state):
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    return state


@pytest.fixture
def token():
    import secrets

    value = secrets.token_hex(4)
    yield value
    account = VerifierAccount()  # the account owns what its run left: it removes it
    unlink = (
        "import _multiprocessing as m, sys\n"
        "for n in sys.argv[1:]:\n"
        "    try:\n        m.sem_unlink(n)\n    except OSError:\n        pass\n"
    )
    with account.exclusive():
        argv = account.wrap([account.python, "-c", unlink, f"/mp-cw{value}", f"/cw{value}"], None)
        subprocess.run(argv, capture_output=True, timeout=60, check=False)


def test_a_value_left_by_the_accounts_pytest_phase_does_not_reach_its_blackbox(
    account_state, project, tmp_path, token
):
    declared = _declaration(tmp_path)
    first = _verify(account_state, project, _source(token, WRITE, WRONG), declared)
    assert first["core_status"] == "FAILED_VERIFICATION", first
    out = _verify(account_state, project, _source(token, READ), declared)
    assert out["status"] != "VERIFIED" and out["core_status"] == "FAILED_VERIFICATION", out
    assert "blackbox:add: MISMATCH [matched 0/1]" in out["reason"], out["reason"]


def test_on_bwrap_nothing_left_outside_is_visible_inside(tmp_path, leftovers):  # noqa: F811
    if _seatbelt():
        pytest.skip("the Seatbelt side runs in test_sandbox_ipc.py on every macOS run")
    _no_leftover(tmp_path, leftovers)
