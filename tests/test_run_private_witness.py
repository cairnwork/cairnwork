"""Witness for the 0700 seal on ``private/guard`` and ``verifier.log`` (#437 F, #392).

After an account-mode run, ``run/`` is gone and ``private`` is ``0700``; the guard directory
(the trusted report plugin) and ``verifier.log`` stay for the owner. test_run_isolation_witness
plants markers in ``run/``, home, tmp and the work directory only. This file proves the other
half: a *later* account run cannot list or read the previous run's ``private/guard``, its files,
or ``verifier.log``. The owner side checks the seal itself (mode 0700, owned by this process,
the guard and the log really exist, so the paths are not a typo). The second run, as the account
inside the real sandbox, tries each path and records the errno: EACCES or EPERM is the witness,
ENOENT is not (it would mean the path formula is wrong). It also reads a file of its own in TMPDIR,
so a probe that cannot open anything does not pass.

Needs the verifier account (pytest.mark.verifier_account). Another account can be named with
CAIRNWORK_WITNESS_VERIFIER_USER and CAIRNWORK_WITNESS_VERIFIER_ROOT (both or neither); without a
usable account the tests are skipped, which is "not run", not "passed".
"""

import os
import stat
import sys
from pathlib import Path

import pytest
from conftest import commit_change
from run_root_scope import own_run_id, own_run_names

from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]


def _account() -> VerifierAccount:
    user = os.environ.get("CAIRNWORK_WITNESS_VERIFIER_USER")
    root = os.environ.get("CAIRNWORK_WITNESS_VERIFIER_ROOT")
    if bool(user) != bool(root):
        raise pytest.UsageError("set both CAIRNWORK_WITNESS_VERIFIER_USER and ..._ROOT, or neither")
    return VerifierAccount(user=user, root=Path(root)) if user and root else VerifierAccount()


ACCOUNT = _account()

LEAVE = "def test_leave():\n    assert True\n"
PROBE = (
    "import errno, os\n\n"
    "def test_cannot_reach_the_last_runs_private_files():\n"
    "    control = os.path.join(os.environ['TMPDIR'], 'cw-private-control')\n"
    "    with open(control, 'w') as handle:\n"
    "        handle.write('ok')\n"
    "    assert open(control).read() == 'ok', 'control file not readable'\n"
    "    denied, other = [], []\n"
    "    for how, path in {tries!r}:\n"
    "        try:\n"
    "            if how == 'list':\n"
    "                os.listdir(path)\n"
    "            else:\n"
    "                open(path, 'rb').close()\n"
    "        except OSError as err:\n"
    "            (denied if err.errno in (errno.EACCES, errno.EPERM) else other).append(\n"
    "                (how, path, errno.errorcode.get(err.errno, err.errno)))\n"
    "            continue\n"
    "        other.append((how, path, 'OPENED'))\n"
    "    print(f'{{len(denied)}} refused, {{len(other)}} not refused')\n"
    "    for how, path, why in other:\n"
    "        print('NOT REFUSED ' + how + ' ' + repr(path) + ' ' + str(why))\n"
    "    assert not other, 'not refused with EACCES/EPERM, listed on stdout'\n"
    "    assert len(denied) == {expect!r}\n"
)


@pytest.fixture
def own_names():
    with own_run_names(ACCOUNT.run_root, ACCOUNT) as prefixes:
        yield prefixes


def _run(repo, tmp_path, name, code, prefixes):
    head = commit_change(repo, {f"tests/{name}": code})
    spec = parse_verifier("v", ["python3", "-m", "pytest", f"tests/{name}"])
    run_id = own_run_id(prefixes, "r")
    (run,) = run_verifiers(
        VerifyContext(repo, ACCOUNT.run_root, python=None, account=ACCOUNT), head, (spec,), run_id
    )
    return run


def _fail(run):
    pytest.fail(run.why + "\n" + run.log_path.read_text())


def test_next_account_run_cannot_read_last_runs_guard_or_log(repo, tmp_path, own_names):
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        pytest.skip("account mode is macOS or Linux")
    first = _run(repo, tmp_path, "test_leave.py", LEAVE, own_names)
    if not first.observation.passed:
        _fail(first)
    private, guard, log = first.log_path.parent, first.log_path.parent / "guard", first.log_path
    # owner side: the seal is there and the paths are real
    info = private.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        pytest.fail(f"{private}: mode {stat.S_IMODE(info.st_mode):04o}, uid {info.st_uid}")
    if os.path.lexists(private / "run"):
        pytest.fail(f"{private / 'run'} still exists after the run")
    if not log.is_file() or log.name != "verifier.log":
        pytest.fail(f"{log} is not the run's verifier.log")
    if not guard.is_dir() or not (names := sorted(os.listdir(guard))):
        pytest.fail(f"{guard} is missing or empty: the witness would be vacuous")
    tries = [("list", str(private)), ("list", str(guard)), ("open", str(log))]
    tries += [("open", str(guard / n)) for n in names if (guard / n).is_file()]
    code = PROBE.format(tries=tries, expect=len(tries))
    second = _run(repo, tmp_path, "test_probe.py", code, own_names)
    if not second.observation.passed:
        _fail(second)
