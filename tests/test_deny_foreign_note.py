"""#900: another process's note through the Sandbox log sender (CI test-macos on #895 and #901:
dirhelper's "set rootless flags") counts toward volume only; anything shaped like a sandbox
report, or carrying a tag, stays unreadable, so none of the run's denials can be skipped."""

from test_deny_channel import line, stream, window

from cairnwork.deny_channel import Evidence, judge

NOTE = "/usr/libexec/dirhelper set rootless flags on /private/var/folders/x/C/"


def test_another_process_s_note_counts_toward_volume_only():
    ours = line("Sandbox: cat(301) deny(1) file-read-data /private/etc/services")
    evidence = judge(stream(line(NOTE, tag=""), ours), window())  # AC1
    assert evidence == Evidence("DENIED", "file-read-data /private/etc/services", 4, 3)
    assert judge(stream(line(NOTE, tag="")), window()) == Evidence("CLEAN", "", 3, 2)


def test_a_note_with_a_tag_or_a_report_shape_stays_unreadable():
    for kept in (
        line(NOTE),  # AC2: the run's tag
        line(NOTE, tag="cwvtag-zz"),  # AC3: a malformed tag
        line(f"{NOTE} deny(1)", tag=""),
        line("Sandbox: odd", tag=""),
        line(f"{NOTE} cwvtag-0123456789abcdef", tag=""),
        line("", tag=""),  # desk-b r1: an empty or redacted message names no sender
        line("  ", tag=""),
        line("<private>", tag=""),
    ):
        assert judge(stream(kept), window()).state == "UNAVAILABLE", kept
