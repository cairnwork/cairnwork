"""#1862 round 4: the rollback's worker-uid gate must tell "account does not exist" (go on) from
"account exists but its UniqueID cannot be read" (stop before any later step, incl. the account
delete). Only the gate block is cut out of the script and run in zsh with fake id/stat/dscl/pgrep/
kill/sleep: nothing real is read, signalled or deleted."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROLLBACK = Path(__file__).resolve().parents[1] / "scripts" / "setup_worker_user_rollback.sh"
needs_zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")


def _cut(pattern):
    found = re.search(pattern, ROLLBACK.read_text(), re.S | re.M)
    assert found, f"missing in rollback script: {pattern}"
    return found.group(0)


def _run(*, exists=True, uid="399", owner="james", sudo_user="james"):
    guard = _cut(r"^owner_uid_guard\(\) \{.*?^\}\n")
    gate = _cut(r'^if dscl \. -read "/Users/\$WORKER".*?^fi\n')
    uid_cmd = "return 1" if uid == "FAIL" else f"print UniqueID: {uid}"
    users = {"james": 501, "_cairnwork_worker": 399}
    script = (
        'die() { print -r -- "DIED: $*"; exit 1 }\n'
        f"id() {{ case $2 in {' '.join(f'{n}) print {u};;' for n, u in users.items())} "
        "*) return 1;; esac }\n"
        "stat() { return 1 }\n"
        "pgrep() { return 1 }\nkill() { print KILLED; return 0 }\nsleep() { :; }\n"
        "worker_pids() { print -r -- ''; }\n"
        "dscl() {\n"
        f"  {'' if exists else 'return 1;'}\n"
        f"  if (( $# >= 4 )); then {uid_cmd}; fi\n"
        "  return 0\n}\n"
        f"WORKER=_cairnwork_worker; RUNS=/nonexistent; OWNER={owner!r}; SUDO_USER={sudo_user!r}\n"
        f"{guard}{gate}print PASSED_GATE\n"
    )
    return subprocess.run(
        ["/bin/zsh", "-c", script], capture_output=True, text=True, timeout=20, check=False
    )


@needs_zsh
def test_account_exists_but_uid_unreadable_stops():
    out = _run(uid="FAIL")
    assert out.returncode == 1 and "DIED:" in out.stdout and "PASSED_GATE" not in out.stdout, out


@needs_zsh
@pytest.mark.parametrize(
    "kwargs",
    [
        {"uid": "0"},
        {"uid": "501"},
        {"uid": "abc"},
        {"uid": ""},
        {"owner": "ghost"},
        {"owner": "", "sudo_user": "ghost"},
    ],
)
def test_bad_uid_or_unresolvable_owner_stops(kwargs):
    out = _run(**kwargs)
    assert out.returncode == 1 and "PASSED_GATE" not in out.stdout, out


@needs_zsh
def test_missing_account_goes_on():
    out = _run(exists=False, uid="FAIL")
    assert out.returncode == 0 and out.stdout.strip().endswith("PASSED_GATE"), out


@needs_zsh
def test_valid_separate_uid_goes_on():
    out = _run(uid="399")
    assert out.returncode == 0 and out.stdout.strip().endswith("PASSED_GATE"), out
