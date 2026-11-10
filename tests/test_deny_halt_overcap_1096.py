"""A stream that crosses its byte cap after the size guard stopped is never judged CLEAN (#1096)."""

from __future__ import annotations

import threading

import pytest

from cairnwork import deny_channel as deny


@pytest.mark.parametrize(
    "size, expected",
    [(64, "UNAVAILABLE"), (8, "CLEAN"), (16, "CLEAN"), (17, "UNAVAILABLE")],
)
def test_the_cap_is_checked_again_after_the_stream_stops(monkeypatch, size, expected):
    """The guard is off (as between _halt's join and the stream stopping): only the check after
    _halt can see that the file went over the cap."""

    class Stream:  # alive until stopped, like log stream under its watchdog
        pid, stdin, code = -1, None, None

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        kill = terminate

        def wait(self, timeout=None):
            return self.code

    def start(path, window):
        path.write_bytes(b"x" * size)  # what the stream wrote while nothing watched the size
        window.inode = path.stat().st_ino
        return Stream()

    class OffGuard:  # a size guard that has already stopped looking
        def __init__(self, *args):
            self.done = threading.Event()

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    def judge(path, window):  # the real judge_stream's first rule: any problem is UNAVAILABLE
        return (
            deny.Evidence("UNAVAILABLE", window.problem)
            if window.problem
            else deny.Evidence("CLEAN")
        )

    monkeypatch.setattr(deny, "MAX_STREAM_BYTES", 16)
    monkeypatch.setattr(deny, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(deny, "_start", start)
    monkeypatch.setattr(deny, "_SizeGuard", OffGuard)
    monkeypatch.setattr(deny, "_canary", lambda *args: None)
    monkeypatch.setattr(deny, "_judge_file", judge)

    with deny.recording("cwvtag-0123456789abcdef", lambda argv, log: "") as box:
        pass

    evidence = box.evidence
    assert evidence is not None
    assert evidence.state == expected, evidence
    if expected == "UNAVAILABLE":
        assert "byte cap" in evidence.detail
