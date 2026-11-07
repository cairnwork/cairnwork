"""#991: whether a run was denied one exact (operation, target) is read from every denial the
judge kept, not from the summary, which names only the first MAX_LISTED. Fixed ndjson replays, no
sandbox and no log process; the shapes follow test_deny_channel.py."""

from test_deny_channel import C1, C2, HEADER, OTHER, canary, line, stream, window

from cairnwork import deny_channel
from cairnwork.deny_channel import judge

PROTOCOLS = ("file-read-data", "/private/etc/protocols")
TARGET = line("Sandbox: python3.13(7) deny(1) file-read-data /private/etc/protocols")
NOISE = [  # the run's own start-up denials, as many as the summary lists
    line(f"Sandbox: python3.13(7) deny(1) file-read-data /noise/{n}")
    for n in range(deny_channel.MAX_LISTED)
]


def test_a_run_denied_the_target_has_it():
    evidence = judge(stream(TARGET), window())
    assert evidence.state == "DENIED" and evidence.denied(*PROTOCOLS)


def test_the_target_after_a_full_summary_is_still_found():  # CI run 42335035331
    evidence = judge(stream(*NOISE, TARGET), window())
    assert evidence.state == "DENIED"
    assert "protocols" not in evidence.detail  # the summary itself is unchanged: 8 names
    assert evidence.detail.count(" | ") == deny_channel.MAX_LISTED - 1
    assert evidence.denied(*PROTOCOLS)


def test_the_same_path_under_another_tag_or_none_is_not_the_runs():
    theirs = line("Sandbox: python3.14(8) deny(1) file-read-data /private/etc/protocols", tag=OTHER)
    untagged = line("Sandbox: cat(9) deny(1) file-read-data /private/etc/protocols", tag="")
    evidence = judge(stream(*NOISE, theirs, untagged), window())
    assert evidence.state == "DENIED" and not evidence.denied(*PROTOCOLS)


def test_other_denials_without_the_target_do_not_have_it():
    other = line("Sandbox: python3.13(7) deny(1) file-read-data /private/etc/services")
    evidence = judge(stream(other), window())
    assert evidence.state == "DENIED" and not evidence.denied(*PROTOCOLS)
    near = [line(f"Sandbox: python3.13(7) deny(1) {op} {path}") for op, path in (
        ("file-write-data", "/private/etc/protocols"),
        ("file-read-data", "/private/etc/protocols.d"),
    )]  # fmt: skip
    evidence = judge(stream(*near), window())  # the same path by another operation, a longer path
    assert evidence.state == "DENIED" and not evidence.denied(*PROTOCOLS)


def test_a_canary_or_a_benign_line_is_not_a_denial():
    benign = line("Sandbox: python3.13(7) deny(1) system-info vfs.disk-space")
    assert judge(stream(benign), window()).state == "CLEAN"
    evidence = judge(stream(benign, TARGET), window())  # DENIED, so its set is filled
    assert evidence.denials == {PROTOCOLS}
    assert not evidence.denied("network-outbound", C2[1])


def test_an_unavailable_stream_has_no_denials_even_with_the_target_in_it():
    over = deny_channel.MAX_WINDOW_LINES + 2 * deny_channel.BACKGROUND_PER_SECOND + 1
    flood = line("Sandbox: sharingd(1) deny(1) file-read-data /x", tag="") * over
    cases = {
        "volume over the cap": stream(TARGET, flood),
        "a missing canary": (HEADER + canary(C1) + TARGET).encode(),
        "a half-written line": stream(TARGET).rstrip(b"\n"),
        "an unparseable line": stream(TARGET, line("Sandbox: not a report")),
    }
    for name, raw in cases.items():
        evidence = judge(raw, window())
        assert evidence.state == "UNAVAILABLE", (name, evidence)
        assert not evidence.denials and not evidence.denied(*PROTOCOLS), name


def test_the_peak_a_rerun_reads_is_the_evidences():
    """test_deny_channel_sandbox reruns on a burst by the peak in the printed evidence."""
    from test_deny_channel_sandbox import _peak

    evidence = judge(stream(TARGET), window())
    assert _peak(str(evidence)) == evidence.peak_per_second > 0
    assert _peak("UNAVAILABLE: a canary line is missing") == 0
