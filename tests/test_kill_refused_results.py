"""#1849 round 3: a refused killpg (safe_killpg returned False) must never read as a clean end.
Every signal goes to a fake: nothing is sent and no real process is touched."""

import io
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from cairnwork import confined, deny_channel, post_verified, survivor_argv
from cairnwork.workers import agy, agy_dispatch

CHILD = 4242
CLEAN = survivor_argv.Survivors(0)


def _refuse(module, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "safe_killpg", lambda pgid, sig: calls.append(pgid) or False)
    return calls


def test_confined_kill_group_reports_a_refusal(monkeypatch):
    _refuse(confined, monkeypatch)
    assert confined._kill_group(CHILD) is False


def test_confined_kill_group_reports_a_sent_signal(monkeypatch):
    monkeypatch.setattr(confined, "safe_killpg", lambda pgid, sig: True)
    assert confined._kill_group(CHILD) is True


def test_confined_execute_is_not_clean_after_a_refused_kill(monkeypatch, tmp_path):
    proc = SimpleNamespace(
        pid=CHILD, stdout=io.BytesIO(), returncode=0, wait=lambda timeout=None: 0
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(confined, "copy_capped", lambda *a, **k: None)
    monkeypatch.setattr(confined, "_wait_unreaped", lambda *a, **k: True)
    monkeypatch.setattr(confined.proc_start, "boot_mark", lambda: "")
    monkeypatch.setattr(confined, "_sweep_survivors", lambda *a, **k: CLEAN)
    _refuse(confined, monkeypatch)
    env = {confined.RUN_TOKEN_VAR: "t"}
    _, _, survivors = confined.execute(["x"], None, env, 5, tmp_path / "log")
    assert survivors >= 1 and "group kill refused" in survivors.reasons


def test_deny_channel_end_group_raises_on_a_refusal(monkeypatch):
    _refuse(deny_channel, monkeypatch)
    deny_channel._GROUPS.add(CHILD)
    with pytest.raises(OSError, match="group kill refused"):
        deny_channel._end_group(SimpleNamespace(pid=CHILD))


def test_deny_channel_stop_becomes_the_windows_problem(monkeypatch):
    _refuse(deny_channel, monkeypatch)
    deny_channel._GROUPS.add(CHILD)
    window = deny_channel.Window("t")
    proc = SimpleNamespace(pid=CHILD, poll=lambda: 0)
    deny_channel._guarded(window, "the stream did not stop", deny_channel._stop, proc)
    assert window.problem


def test_post_verified_kill_group_raises_on_a_refusal(monkeypatch):
    _refuse(post_verified, monkeypatch)
    killed = []
    fake = SimpleNamespace(pid=CHILD, kill=lambda: killed.append(1))
    with pytest.raises(OSError, match="group kill refused"):
        post_verified._kill_group(cast("subprocess.Popen[bytes]", fake))
    assert killed  # the leader is still ended


def _agy_stop(monkeypatch, sent):
    monkeypatch.setattr(agy, "safe_killpg", lambda pgid, sig: sent)
    monkeypatch.setattr(agy, "sweep_marked", lambda *a, **k: confined.Sweep(listed=True))
    monkeypatch.setattr(agy, "confirm_gone", lambda first, look: first)
    process = SimpleNamespace(pid=CHILD, wait=lambda timeout=None: 0)
    return agy._stop(cast("Any", process), None, "a")[1]


def test_agy_stop_is_never_gone_after_a_refused_kill(monkeypatch):
    swept = _agy_stop(monkeypatch, sent=False)
    assert swept.gone is False and swept.denied == 1


def test_agy_stop_stays_gone_when_the_kill_was_sent(monkeypatch):
    assert _agy_stop(monkeypatch, sent=True).gone is True


def test_agy_dispatch_ask_to_stop_reports_a_refusal(monkeypatch):
    _refuse(agy_dispatch, monkeypatch)
    waited = []
    process = SimpleNamespace(pid=CHILD, wait=lambda timeout=None: waited.append(1))
    assert agy_dispatch.ask_to_stop(process) is False
    assert not waited  # nothing was sent: no grace to wait for
