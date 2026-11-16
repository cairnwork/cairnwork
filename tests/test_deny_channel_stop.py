"""Process cleanup regressions (#1363)."""

import subprocess
from typing import Any

from cairnwork import deny_channel


class BrokenControl:
    closed = False

    def close(self):
        raise BrokenPipeError


def _child(monkeypatch, *, broken_control=True) -> Any:
    proc: Any = subprocess.Popen(
        ["/bin/sleep", "30"], stdin=subprocess.PIPE, start_new_session=True
    )
    deny_channel._GROUPS.add(proc.pid)
    proc._original_stdin = proc.stdin
    if broken_control:
        monkeypatch.setattr(proc, "stdin", BrokenControl())
    return proc


def _cleanup(proc: Any) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    proc._original_stdin.close()
    deny_channel._GROUPS.discard(proc.pid)


def test_close_error_is_recorded_after_process_group_stops(monkeypatch):
    proc = _child(monkeypatch)
    window = deny_channel.Window("tag", [])
    try:
        deny_channel._guarded(window, "the stream did not stop", deny_channel._stop, proc)
        assert window.problem == "the stream did not stop: BrokenPipeError"
        assert proc.pid not in deny_channel._GROUPS
        proc.wait(timeout=5)
    finally:
        _cleanup(proc)


def test_wait_error_still_ends_process_group(monkeypatch):
    proc = _child(monkeypatch, broken_control=False)
    window = deny_channel.Window("tag", [])

    def fail_wait(*_args, **_kwargs):
        raise RuntimeError("wait failed")

    monkeypatch.setattr(proc, "wait", fail_wait)
    try:
        deny_channel._guarded(window, "the stream did not stop", deny_channel._stop, proc)
        assert window.problem == "the stream did not stop: RuntimeError"
        assert proc.pid not in deny_channel._GROUPS
        monkeypatch.undo()
        proc.wait(timeout=5)
    finally:
        monkeypatch.undo()
        _cleanup(proc)
