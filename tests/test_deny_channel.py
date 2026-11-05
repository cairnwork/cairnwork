"""#797 step 1: the deny channel's policy tagging and stream judging, replayed from ndjson fixtures
shaped like ``log stream --style ndjson`` (step 0 raw lines). No sandbox, no log process."""

import json

import pytest

from cairnwork import deny_channel
from cairnwork.deny_channel import Evidence, Window, judge, tag_policy, tagged, watch
from cairnwork_sandbox.backend import Policy

TAG = "cwvtag-0123456789abcdef"
OTHER = "cwvtag-fedcba9876543210"
HEADER = 'Filtering the log data using "sender == "Sandbox""\n'
C1, C2 = (101, "remote:*:50001"), (102, "remote:*:50002")


def line(message, second=0, tag=TAG):
    stamp = f"2026-11-05 08:00:{second:02d}.000001+0800"
    text = f"{message}\n{tag}" if tag else message
    return json.dumps({"timestamp": stamp, "eventMessage": text}) + "\n"


def canary(pid_target, second=0):
    pid, target = pid_target
    return line(f"Sandbox: python3.13({pid}) deny(1) network-outbound {target}", second)


def stream(*body, opening=0, closing=1):
    return (HEADER + canary(C1, opening) + "".join(body) + canary(C2, closing)).encode()


def window():
    return Window(TAG, [C1, C2])


def test_tag_policy_tags_every_deny_form_and_skips_parens_in_strings():
    text = '(version 1)\n(deny default)\n(allow file-read* (regex #"^/a(b)c$"))\n(deny network*)\n'
    text += '(deny file-read* file-write*\n  (subpath "/x(y)"))\n'
    tagged_text = tag_policy(text, TAG)
    assert tagged_text.count(f'(with message "{TAG}")') == 3
    assert '(deny default (with message "' in tagged_text
    assert '(subpath "/x(y)") (with message "' in tagged_text
    assert '(allow file-read* (regex #"^/a(b)c$"))' in tagged_text  # allow forms untouched


@pytest.mark.parametrize(
    "text, tag",
    [("(version 1)(allow default)", TAG), ("(deny default", TAG), ("(deny default)", "x")],
)
def test_tag_policy_refuses_no_deny_form_an_open_form_or_a_bad_tag(text, tag):
    with pytest.raises(ValueError):
        tag_policy(text, tag)


def test_tagged_gives_a_new_tag_per_run_and_only_on_seatbelt():
    policy = Policy("(version 1)(deny default)")
    (one, tag1), (two, tag2) = tagged(policy, "seatbelt", True), tagged(policy, "seatbelt", True)
    assert tag1 != tag2 and tag1 in one.text and tag2 in two.text
    assert tagged(policy, "seatbelt", False) == (policy, "")
    assert tagged(policy, "bwrap", True) == (policy, "")
    assert tagged(Policy("(version 1)(allow default)"), "seatbelt", True)[1] == ""


def test_watch_off_records_nothing_and_elsewhere_records_unavailable():
    with watch(False, "seatbelt", TAG, None) as box:
        pass
    assert box.evidence is None
    with watch(True, "bwrap", "", None) as box:
        pass
    assert box.evidence == deny_channel.UNSUPPORTED  # AC8
    with watch(True, "seatbelt", "", None) as box:
        pass
    assert box.evidence is not None and box.evidence.state == "UNAVAILABLE"


def test_canaries_and_benign_lines_alone_are_clean():
    benign = line("Sandbox: python3.13(200) deny(1) system-info vfs.disk-space")
    assert judge(stream(benign), window()).state == "CLEAN"


def test_a_tagged_line_is_ours_and_another_tag_is_ignored():
    theirs = line(
        "Sandbox: python3.14(300) deny(1) file-read-data /private/etc/services", tag=OTHER
    )
    untagged = line("Sandbox: sharingd(9) deny(1) file-read-data /private/etc/services", tag="")
    assert judge(stream(theirs, untagged), window()).state == "CLEAN"
    ours = line("Sandbox: cat(301) deny(1) file-read-data /private/etc/services")  # a child
    evidence = judge(stream(ours), window())
    assert evidence == Evidence("DENIED", "file-read-data /private/etc/services", 3, 2)


def test_denied_names_the_first_distinct_denials_in_order():
    paths = ["/a", "/b", "/a", *(f"/p{n}" for n in range(deny_channel.MAX_LISTED))]
    body = [line(f"Sandbox: python3.13(5) deny(1) file-read-data {p}") for p in paths]
    listed = judge(stream(*body), window()).detail.split(" | ")
    assert len(listed) == deny_channel.MAX_LISTED
    assert listed[:3] == ["file-read-data /a", "file-read-data /b", "file-read-data /p0"]


def test_a_canary_match_needs_pid_tag_and_target():
    # the canary's target under another pid is an ordinary denial of the run (AC6)
    copy = line(f"Sandbox: python3.13(999) deny(1) network-outbound {C1[1]}")
    assert judge(stream(copy), window()).detail == f"network-outbound {C1[1]}"
    # the canary's pid and target without our tag: the canary is missing (AC-canary negative)
    raw = HEADER + line(f"Sandbox: x({C1[0]}) deny(1) network-outbound {C1[1]}", tag=OTHER)
    evidence = judge((raw + canary(C2, 1)).encode(), window())
    assert evidence.state == "UNAVAILABLE" and evidence.detail == "a canary line is missing"


def test_duplicate_reports_count_as_n_toward_volume():
    many = line(
        "4000 duplicate reports for Sandbox: x(7) deny(1) system-info vfs.disk-space", tag=""
    )
    evidence = judge(stream(many), window())
    assert evidence.state == "UNAVAILABLE" and "volume" in evidence.detail
    assert evidence.window_lines == 4002


def test_a_foreign_burst_makes_it_unavailable_and_a_small_one_does_not():
    # AC-burst-foreign: every one of our lines arrived, the stream still went over the cap
    foreign = "Sandbox: flood(8) deny(1) network-outbound remote:*:9"
    burst = [line(foreign, tag=OTHER)] * (deny_channel.MAX_LINES_PER_SECOND + 1)
    assert judge(stream(*burst), window()).state == "UNAVAILABLE"
    assert judge(stream(*[line(foreign, tag=OTHER)] * 100), window()).state == "CLEAN"


def test_the_window_total_cap_applies_across_seconds_and_grows_with_the_window():
    foreign = "Sandbox: flood(8) deny(1) network-outbound remote:*:9"
    cap = deny_channel.MAX_WINDOW_LINES + 10 * deny_channel.BACKGROUND_PER_SECOND  # 10 seconds

    def over(count):
        body = [line(foreign, second=s % 10, tag=OTHER) for s in range(count)]
        return judge(stream(*body, closing=9), window())

    assert over(cap - 2).state == "CLEAN"  # two canary lines make it exactly the cap
    evidence = over(cap - 1)
    assert evidence.state == "UNAVAILABLE" and evidence.peak_per_second <= 1000


@pytest.mark.parametrize(
    "raw, reason",
    [
        (stream()[:-5], "half-written"),
        (stream().replace(HEADER.encode(), b""), "no stream header"),
        (stream(b"{not json\n".decode()), "unreadable"),
        (stream(line("Sandbox: something odd")), "unparseable"),
        (stream(line("Sandbox: x(1) deny(1) file-read-data /a", tag="cwvtag-zz")), "malformed"),
        (stream(closing=40), "stalled"),
        ((HEADER + canary(C1)).encode(), "canary"),
    ],
)
def test_anything_unreadable_is_unavailable_never_clean(raw, reason):
    evidence = judge(raw, window())
    assert evidence.state == "UNAVAILABLE" and reason in evidence.detail, evidence


def test_a_recorder_problem_wins_over_the_stream():
    evidence = judge(stream(), Window(TAG, [C1, C2], problem="log stream exited during the run"))
    assert evidence == Evidence("UNAVAILABLE", "log stream exited during the run")


def test_a_non_utf8_path_and_a_newline_inside_the_path_are_parsed():
    odd = line("Sandbox: x(4) deny(1) file-read-data /tmp/Q\nname")
    evidence = judge(stream(odd).replace(b"/tmp/Q", b"/tmp/\xff"), window())
    assert evidence.state == "DENIED" and "file-read-data /tmp/\ufffd?name" in evidence.detail


def test_system_policy_lines_are_ignored():
    kill = line("System Policy: kill(1) deny(1) signal", tag="")
    assert judge(stream(kill), window()).state == "CLEAN"
    # CI test-macos 11/05 (#884): the same line behind a duplicate-reports prefix
    daemon = line(
        "2 duplicate reports for System Policy: d(178) allow file-read-metadata /S", tag=""
    )
    assert judge(stream(daemon), window()).state == "CLEAN"


def test_another_process_s_allow_lines_count_toward_volume_and_are_never_a_denial():
    # CI test-macos 11/05: a system daemon's profile reported an allow; it made the run UNAVAILABLE
    allow = "Sandbox: diskimaged(86) allow process-exec* /System/Library/Filesystems/x"
    counted = line("Sandbox: y(87) allow(1) file-read-data /b", tag=OTHER)
    assert judge(stream(line(allow, tag=""), counted), window()) == Evidence("CLEAN", "", 4, 3)
    burst = [line(allow, tag="")] * (deny_channel.MAX_LINES_PER_SECOND + 1)
    assert "volume" in judge(stream(*burst), window()).detail
    evidence = judge(stream(line("Sandbox: x(5) allow(1) file-read-data /a")), window())
    assert evidence == Evidence("UNAVAILABLE", "a tagged allow line")  # our tag is on denies only
    # Codex #881 r1: a deny line whose process name looks like "x(1) allow(9)" stays a denial
    named = judge(stream(line("Sandbox: x(1) allow(9) deny(1) file-read-data /secret")), window())
    assert named == Evidence("DENIED", "file-read-data /secret", 3, 2)
    # Codex #881 r2: a broken deny line (no count) is still unreadable, as on main, not an allow
    broken = line("Sandbox: x(1) allow(9) deny /secret", tag="")
    assert "unparseable" in judge(stream(broken), window()).detail


def test_a_canary_that_raises_never_stops_the_run(monkeypatch):
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)

    def broken(argv, log):
        raise RuntimeError("account gone")

    ran = []
    with deny_channel.recording(TAG, broken) as box:
        ran.append(True)
    assert ran == [True] and box.evidence is not None
    assert box.evidence.state == "UNAVAILABLE" and "a canary failed" in box.evidence.detail


def test_a_log_that_cannot_start_is_unavailable(monkeypatch):
    monkeypatch.setattr(deny_channel, "LOG", "/nonexistent/log")
    with deny_channel.recording(TAG, lambda argv, log: "") as box:
        pass
    assert box.evidence == Evidence("UNAVAILABLE", "log is missing")


def test_an_unreadable_timestamp_is_unavailable_not_an_error():
    odd = json.dumps({"timestamp": "yesterday-ish", "eventMessage": "Sandbox: x(1) deny(1) a b"})
    evidence = judge(stream(odd + "\n"), window())
    assert evidence.state == "UNAVAILABLE", evidence


def test_a_stream_that_raises_on_start_never_stops_the_run(monkeypatch):
    def broken(path, window):
        raise OSError("no log")

    monkeypatch.setattr(deny_channel, "_start", broken)
    ran = []
    with deny_channel.recording(TAG, lambda argv, log: "") as box:
        ran.append(True)
    assert ran == [True] and box.evidence is not None
    assert box.evidence.detail == "log stream did not start: OSError"


def test_a_private_directory_that_cannot_be_made_never_stops_the_run(monkeypatch):
    monkeypatch.setattr(deny_channel.tempfile, "tempdir", "/nonexistent/cairnwork-797")
    ran = []
    with deny_channel.recording(TAG, lambda argv, log: "") as box:
        ran.append(True)
    assert ran == [True] and box.evidence is not None
    assert box.evidence.detail.startswith("no private directory: ")


class _Proc:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


def test_the_size_guard_stops_a_stream_over_its_byte_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(deny_channel, "MAX_STREAM_BYTES", 1000)
    monkeypatch.setattr(deny_channel, "POLL_SECONDS", 0.01)
    path, proc, window = tmp_path / "s.ndjson", _Proc(), Window(TAG)
    path.write_bytes(b"x" * 999)
    guard = deny_channel._SizeGuard(path, proc, window)
    deny_channel.time.sleep(0.1)
    assert not proc.stopped and window.problem == ""  # under the cap: left alone
    path.write_bytes(b"x" * 1001)
    guard.join(timeout=5)
    assert proc.stopped and "byte cap" in window.problem


def test_the_canary_search_reads_only_what_was_added(tmp_path):
    path, window = tmp_path / "s.ndjson", Window(TAG)
    path.write_bytes(b"header\nab")
    assert not deny_channel._found(path, b"abcd", window)
    with path.open("ab") as handle:
        handle.write(b"cd\n")  # the needle spans the two reads
    assert deny_channel._found(path, b"abcd", window)
    assert window.offset == len(b"header\nabcd\n")
    assert not deny_channel._found(path, b"header", window)  # never read again from the start


def test_an_opening_canary_seen_late_is_retired_and_never_a_denial():
    late = (102, "remote:*:50009")
    body = canary(late)  # the first opening canary's line, after its retry was already seen
    evidence = judge(stream(body), Window(TAG, [C1, C2], retired=[late]))
    assert evidence.state == "CLEAN", evidence


def test_the_opening_canary_runs_again_when_its_line_does_not_come(monkeypatch, tmp_path):
    path, window, calls = tmp_path / "s.ndjson", Window(TAG), []
    path.write_bytes(HEADER.encode())

    def run(argv, log):
        calls.append(argv[1])
        if len(calls) == 2:  # only the second canary's line reaches the stream
            message = f"Sandbox: python3.13(77) deny(1) network-outbound remote:*:{argv[1]}"
            path.write_bytes(path.read_bytes() + line(message).encode())
        return "77\n" if len(calls) == 2 else "76\n"

    monkeypatch.setattr(deny_channel, "WAIT_SECONDS", 0.2)
    deny_channel._canary(run, tmp_path, window, path, 2)
    assert window.problem == "" and window.canaries == [(77, f"remote:*:{calls[1]}")]
    assert window.retired == [(76, f"remote:*:{calls[0]}")]


class _StuckProc(_Proc):
    def terminate(self):
        raise RuntimeError("will not stop")


def test_a_stream_file_that_cannot_be_looked_at_stops_the_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(deny_channel, "POLL_SECONDS", 0.01)
    proc, window = _Proc(), Window(TAG)
    guard = deny_channel._SizeGuard(tmp_path / "gone.ndjson", proc, window)  # never created
    guard.join(timeout=5)
    assert proc.stopped and window.problem.startswith("the stream file cannot be looked at")


def test_a_stream_that_will_not_stop_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(deny_channel, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(deny_channel, "MAX_STREAM_BYTES", 10)
    path, window = tmp_path / "s.ndjson", Window(TAG)
    path.write_bytes(b"x" * 11)
    escaped = []  # an exception that leaves the guard thread
    monkeypatch.setattr(deny_channel.threading, "excepthook", escaped.append)
    guard = deny_channel._SizeGuard(path, _StuckProc(), window)  # the guard's own stop fails
    guard.join(timeout=5)
    assert "byte cap" in window.problem and escaped == []
    deny_channel._halt(guard, _StuckProc(), window)  # and so does the final one: no exception


def test_a_size_guard_that_cannot_start_stops_the_stream_and_the_run_goes_on(monkeypatch):
    proc = _Proc()

    def started(path, window):
        path.write_bytes(HEADER.encode())
        return proc

    def refuse(self):
        raise RuntimeError("no thread")

    monkeypatch.setattr(deny_channel, "_start", started)
    monkeypatch.setattr(deny_channel._SizeGuard, "start", refuse)
    ran = []
    with deny_channel.recording(TAG, lambda argv, log: "") as box:
        ran.append(proc.stopped)
    assert ran == [True]  # stopped before the body: nothing would bound the file
    assert box.evidence == Evidence("UNAVAILABLE", "the size guard did not start: RuntimeError")


def test_the_window_cap_counts_the_span_in_seconds_not_the_seconds_with_lines():
    foreign = "Sandbox: flood(8) deny(1) network-outbound remote:*:9"
    body = [line(foreign, second=s, tag=OTHER) for s in (0, 5, 9) for _ in range(800)]
    evidence = judge(stream(*body, closing=9), window())  # 2402 lines over 10 s: cap 2500
    assert evidence.state == "CLEAN" and evidence.window_lines == 2402


def test_only_the_opening_canary_gets_a_second_try(monkeypatch):
    tries = []
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    monkeypatch.setattr(deny_channel, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(deny_channel, "_canary", lambda run, d, w, p, n=1: tries.append(n))
    with deny_channel.recording(TAG, lambda argv, log: ""):
        assert tries == [2]
    assert tries == [2, 1]


def test_lines_split_only_at_newline_so_unicode_separators_stay_inside_a_path():
    message = "Sandbox: python3.13(9) deny(1) file-read-data /a\u2028b\x85c"
    raw = {"timestamp": "2026-11-05 08:00:00.000001+0800", "eventMessage": f"{message}\n{TAG}"}
    evidence = judge(stream(json.dumps(raw, ensure_ascii=False) + "\n"), window())
    assert evidence == Evidence("DENIED", "file-read-data /a?b?c", 3, 2)  # shown cleaned
