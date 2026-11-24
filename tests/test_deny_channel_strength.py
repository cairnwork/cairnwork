"""Test-strength sweep of deny_channel.py: pins five moves no earlier test noticed (an escaped
quote inside a deny form, SIGKILL on the leftover group, the per-try canary wait, the settle pause
after the closing canary, and the random canary port). No sandbox, no log process."""

import signal
from types import SimpleNamespace

from cairnwork import deny_channel
from cairnwork.deny_channel import Window, tag_policy

TAG = "cwvtag-0123456789abcdef"


def test_an_escaped_quote_does_not_end_the_string_so_a_paren_after_it_stays_inside():
    text = '(deny file-read* (subpath "/a\\"b)c"))\n'
    assert (
        tag_policy(text, TAG) == f'(deny file-read* (subpath "/a\\"b)c") (with message "{TAG}"))\n'
    )


def test_the_leftover_process_group_is_killed_once_with_sigkill(monkeypatch):
    sent = []
    monkeypatch.setattr(deny_channel.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(deny_channel, "_GROUPS", {4242})
    proc = SimpleNamespace(pid=4242)
    deny_channel._end_group(proc)
    deny_channel._end_group(proc)  # a later call must not signal a reused group id
    assert sent == [(4242, signal.SIGKILL)]


def test_each_canary_try_waits_its_share_of_the_wait_budget(monkeypatch, tmp_path):
    waits = []

    def fake_wait(ready, proc, seconds=None):
        waits.append(seconds)
        return False

    monkeypatch.setattr(deny_channel, "_wait", fake_wait)
    monkeypatch.setattr(deny_channel, "WAIT_SECONDS", 10.0)
    for tries in (1, 2):
        waits.clear()
        deny_channel._canary(lambda argv, log: "7\n", tmp_path, Window(TAG), tmp_path / "s", tries)
        assert waits == [10.0 / tries] * tries


def test_the_run_pauses_for_the_settle_time_after_the_closing_canary(monkeypatch):
    events = []
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    monkeypatch.setattr(deny_channel, "SETTLE_SECONDS", 0.7)
    monkeypatch.setattr(
        deny_channel.time, "sleep", lambda seconds: events.append(("sleep", seconds))
    )
    monkeypatch.setattr(
        deny_channel, "_canary", lambda run, d, w, p, n=1: events.append(("canary", n))
    )
    with deny_channel.recording(TAG, lambda argv, log: ""):
        pass
    assert events == [("canary", 2), ("canary", 1), ("sleep", 0.7)]


def test_the_canary_port_is_random_inside_the_ephemeral_range(monkeypatch, tmp_path):
    ports, bounds = [], []
    for pick in (0, 15999):

        def randbelow(bound, pick=pick):
            bounds.append(bound)
            return pick

        monkeypatch.setattr(deny_channel.secrets, "randbelow", randbelow)
        deny_channel._canary(
            lambda argv, log: ports.append(argv[1]) or "", tmp_path, Window(TAG), tmp_path / "s"
        )
    assert ports == ["49152", "65151"] and set(bounds) == {16000}
