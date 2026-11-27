"""#1807: a run on the second verifier account declares ``/Users/Shared/cairnwork-v2/runs`` as a
sweep root (cleanup_sweep.look_for), next to the intake's own run root. The allow-list must accept
every account in ``VERIFIER_ACCOUNTS`` and still refuse any other ``/Users/Shared`` directory.
Nothing is signalled or started: ``scope`` only judges paths."""

import sys

import pytest

from cairnwork import proc_cwd
from cairnwork.verifier_select import VERIFIER_ACCOUNTS

HOME = "/Users/tester"
STATE_RUNS = f"{HOME}/.local/share/cairnwork/state-x/runs"
ATTEMPT = "a0123456789abcdef"


@pytest.fixture(autouse=True)
def _macos(monkeypatch):
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", True)
    monkeypatch.setattr(proc_cwd, "_home", lambda: HOME)


@pytest.mark.parametrize("base", ["/Users/Shared/cairnwork", "/Users/Shared/cairnwork-v2"])
def test_each_verifier_account_run_root_is_a_narrow_root_beside_the_intake_root(base):
    roots = [STATE_RUNS, f"{base}/runs"]  # cleanup_sweep.look_for: [intake.run_root, *extra]
    for where in (f"{base}/runs/{ATTEMPT}", f"{STATE_RUNS}/{ATTEMPT}"):
        assert proc_cwd.scope([where], roots) == (where,)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS account roots")
def test_every_registered_verifier_account_passes_the_root_check():
    for account in VERIFIER_ACCOUNTS.values():
        where = f"{account.run_root}/{ATTEMPT}"
        assert proc_cwd.scope([where], [str(account.run_root)]) == (where,)


@pytest.mark.parametrize(
    "base",
    [
        "/Users/Shared/cairnwork-worker",
        "/Users/Shared/cairnwork-xa2",
        "/Users/Shared/cairnwork-v22",
        "/Users/Shared/cairnwork-v3",
        "/Users/Shared/cairnworkx",
        "/Users/Shared/cairnwork-v2x",
        "/Users/Shared/other",
        "/Users/Shared",
    ],
)
def test_other_shared_directories_stay_refused(base):
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{base}/runs/{ATTEMPT}"], [f"{base}/runs"])
