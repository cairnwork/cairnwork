"""#1849 audit gaps: one guarded killpg for every child-group kill, hook_watch's own-group kill,
and the rollback script's uid guard. Every signal here goes to a recorder: nothing is ever sent,
no process is touched, no sudo or root is needed."""

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from local_worker_fakes import WRAPPER, make_runs

from cairnwork import confined, deny_channel, post_verified, safe_kill
from cairnwork.workers import agy, agy_dispatch

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts" / "setup_worker_user_rollback.sh"
BAD = {"zero": 0, "one": 1, "minus_one": -1, "minus_two": -2, "own_group": "own"}
CHILD = 4242


@pytest.fixture
def sent(monkeypatch):
    recorded = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: recorded.append((pgid, sig)))
    return recorded


def _bad(value):
    return os.getpgrp() if value == "own" else value


@pytest.mark.parametrize("name", BAD)
def test_safe_killpg_refuses_what_is_never_a_childs_group(sent, name):
    assert safe_kill.safe_killpg(_bad(BAD[name]), signal.SIGKILL) is False
    assert sent == []


def test_safe_killpg_signals_a_child_group(sent):
    assert safe_kill.safe_killpg(CHILD, signal.SIGTERM) is True
    assert sent == [(CHILD, signal.SIGTERM)]


def _confined(pgid):
    confined._kill_group(pgid)


def _post_verified(pgid):
    post_verified._kill_group(
        cast("subprocess.Popen[bytes]", SimpleNamespace(pid=pgid, kill=lambda: None))
    )


def _deny_channel(pgid):
    deny_channel._GROUPS.add(pgid)
    deny_channel._end_group(SimpleNamespace(pid=pgid))


def _agy_dispatch(pgid):
    agy_dispatch.ask_to_stop(SimpleNamespace(pid=pgid, wait=lambda timeout=None: 0))


def _agy_stop(pgid, monkeypatch):
    monkeypatch.setattr(agy, "sweep_marked", lambda *a, **k: SimpleNamespace(held_by=[]))
    monkeypatch.setattr(agy, "confirm_gone", lambda first, look: first)
    agy._stop(SimpleNamespace(pid=pgid, wait=lambda timeout=None: 0), None, "a")


SITES = {
    "confined": lambda p, m: _confined(p),
    "post_verified": lambda p, m: _post_verified(p),
    "deny_channel": lambda p, m: _deny_channel(p),
    "agy_dispatch": lambda p, m: _agy_dispatch(p),
    "agy": _agy_stop,
}


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name", BAD)
def test_every_group_kill_refuses_a_bad_group(sent, monkeypatch, site, name):
    SITES[site](_bad(BAD[name]), monkeypatch)
    assert sent == []


@pytest.mark.parametrize("site", SITES)
def test_every_group_kill_still_ends_a_childs_group(sent, monkeypatch, site):
    SITES[site](CHILD, monkeypatch)
    assert [pgid for pgid, _ in sent] == [CHILD]


def _hook_watch(monkeypatch, *, pid, pgrp, sent):
    spec = importlib.util.spec_from_file_location(
        "hook_watch", Path(post_verified.__file__).with_name("hook_watch.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = SimpleNamespace(
        getpid=lambda: pid,
        getpgrp=lambda: pgrp,
        getsid=os.getsid,
        killpg=lambda group, sig: sent.append((group, sig)),
    )
    monkeypatch.setattr(module, "os", fake)
    module._kill_session(kill_group=True, budget=0.0)  # no session scan: only the group kill


def test_hook_watch_does_not_kill_a_group_it_does_not_lead(monkeypatch):
    sent = []
    _hook_watch(monkeypatch, pid=5000, pgrp=4000, sent=sent)  # started from a shell: pgrp != pid
    assert sent == []


def test_hook_watch_kills_its_own_group_when_it_leads_it(monkeypatch):
    sent = []
    _hook_watch(monkeypatch, pid=5000, pgrp=5000, sent=sent)  # start_new_session, as post_verified
    assert sent == [(5000, signal.SIGKILL)]


def _guard_text():
    text = ROLLBACK.read_text()
    found = re.search(r"^owner_uid_guard\(\) \{.*?^\}\n", text, re.S | re.M)
    assert found, "owner_uid_guard is missing from the rollback script"
    return found.group(0)


def _guard(wuid, *, owner="", sudo_user="", runs_owner="", users=None):
    users = users or {"james": 501, "_cairnwork_worker": 399}
    script = (
        'die() { print -r -- "DIED: $*"; exit 1 }\n'
        f"id() {{ case $2 in {' '.join(f'{n}) print {u};;' for n, u in users.items())} "
        "*) return 1;; esac }\n"
        f"stat() {{ {f'print {runs_owner}' if runs_owner else 'return 1'} }}\n"
        f"OWNER={owner!r}; SUDO_USER={sudo_user!r}; RUNS=/nonexistent\n"
        f"{_guard_text()}owner_uid_guard {wuid}; print OK\n"
    )
    return subprocess.run(
        ["/bin/zsh", "-c", script], capture_output=True, text=True, timeout=20, check=False
    )


needs_zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")


@needs_zsh
@pytest.mark.parametrize(
    "kwargs", [{"owner": "james"}, {"sudo_user": "james"}, {"runs_owner": "james"}]
)
def test_rollback_refuses_a_worker_uid_that_is_the_owners(kwargs):
    out = _guard(501, **kwargs)
    assert out.returncode == 1 and "DIED: worker uid 501 is the owner's" in out.stdout, out


@needs_zsh
def test_rollback_goes_on_for_a_separate_worker_account():
    out = _guard(399, owner="james", sudo_user="james")
    assert out.returncode == 0 and out.stdout.strip().endswith("OK"), out


@needs_zsh
def test_rollback_fails_closed_when_no_owner_can_be_named():
    out = _guard(399)
    assert out.returncode == 1 and "cannot tell who the owner is" in out.stdout, out


def test_local_worker_wrapper_already_refuses_slash_as_run_root(tmp_path):
    """Audit item 1 did not hold: "/" is not a run root (no tag name, not directly in RUNS).
    Pinned with `status`, which never signals anything: require_run_root runs before every action
    (sweep included), so a regressed guard cannot make this test kill a process. cwd is tmp_path."""
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(make_runs(tmp_path)),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    args: list[Any] = ["/bin/sh", str(WRAPPER), "status", "/"]
    proc = subprocess.run(args, capture_output=True, env=env, cwd=tmp_path, check=False)
    assert proc.returncode == 64 and b"refusing" in proc.stderr, proc.stderr
