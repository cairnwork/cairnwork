"""#331: the tail of a failed base probe's log, as it goes into the output's logs."""

from cairnwork.p2p_probe_log import probe_log_tail


def test_a_missing_log_reads_as_no_log(tmp_path):
    assert probe_log_tail(tmp_path / "absent.log") == "(no log)"
    assert probe_log_tail(None) == "(no log)"


def test_colour_codes_go_crlf_ends_a_line_and_other_controls_are_replaced(tmp_path):
    log = tmp_path / "verifier.log"
    text = "ok\r\n\x1b[31mred\x1b[0m\r\x00\x1b]0;t\x07\u202eevil\u2028\tend"
    log.write_bytes(text.encode())
    assert probe_log_tail(log) == "ok\nred???]0;t??evil?\tend"


def test_url_credentials_are_removed_even_when_the_url_starts_before_the_tail(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("fetch https://user:s3cret@example.com/x\n" + "y" * 2040)
    tail = probe_log_tail(log)
    assert "s3cret" not in tail and len(tail) <= 2048
    log.write_text("fetch https://user:s3cret@example.com/x failed\n")
    assert probe_log_tail(log) == "fetch https://example.com/x failed\n"


def test_the_tail_is_the_last_2048_characters(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("a" * 5000 + "END")
    tail = probe_log_tail(log)
    assert len(tail) == 2048 and tail.endswith("END")
