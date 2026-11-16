"""#995 step A: tagged stream loss only tightens deny evidence."""

from test_deny_channel import C1, HEADER, canary, line, stream, window

from cairnwork.deny_channel import Evidence, judge


def test_no_loss_preserves_the_base_judgment():
    clean = judge(stream(), window())
    denied = judge(stream(line("Sandbox: cat(301) deny(1) file-read-data /private/x")), window())
    assert clean == Evidence("CLEAN", "", 2, 1)
    assert denied == Evidence("DENIED", "file-read-data /private/x", 3, 2)


def test_duplicate_tagged_reports_count_as_loss_and_make_evidence_unavailable():
    duplicate = line("2 duplicate reports for Sandbox: cat(301) deny(1) file-read-data /private/x")
    evidence = judge(stream(duplicate), window())
    assert evidence.state == "UNAVAILABLE"
    assert evidence.loss_count == 2
    assert "lost 2 reports" in evidence.detail


def test_missing_own_canary_line_makes_evidence_unavailable():
    raw = (HEADER + canary(C1)).encode()
    evidence = judge(raw, window())
    assert evidence.state == "UNAVAILABLE"
    assert evidence.detail == "a canary line is missing"


def test_loss_never_turns_failed_into_verified():
    duplicate = line("2 duplicate reports for Sandbox: cat(301) deny(1) file-read-data /private/x")
    failed_before = Evidence("DENIED", "file-read-data /private/x")
    failed_after = judge(stream(duplicate), window())
    assert failed_before.state == "DENIED"
    assert failed_after.state == "UNAVAILABLE"
    assert failed_after.state != "VERIFIED"


def test_singular_duplicate_report_is_loss_too():
    one = line("1 duplicate report for Sandbox: cat(301) deny(1) file-read-data /private/x")
    evidence = judge(stream(one), window())
    assert evidence.state == "UNAVAILABLE"
    assert evidence.loss_count == 1
