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


def test_url_credentials_are_removed_even_when_the_cut_falls_inside_the_url(tmp_path):
    log = tmp_path / "verifier.log"
    head = "fetch https://user:s3cret@example.com/x\n"  # 40 characters
    log.write_text(head + "y" * 2027)  # the last 2048 start at "s3cret@": inside the credentials
    tail = probe_log_tail(log)
    assert "s3cret" not in tail and len(tail) == 2048  # redacted before the cut
    log.write_text("fetch https://user:s3cret@example.com/x failed\n")
    assert probe_log_tail(log) == "fetch https://example.com/x failed\n"


def test_a_multibyte_log_still_gives_2048_whole_characters(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("中" * 6000 + "\n" + "中" * 3000 + "\nEND")  # the window starts mid-character
    tail = probe_log_tail(log)
    assert len(tail) == 2048 and tail.endswith("END") and "\ufffd" not in tail


def test_the_tail_is_the_last_2048_characters(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("a" * 5000 + "END")
    tail = probe_log_tail(log)
    assert len(tail) == 2048 and tail.endswith("END")


def test_credentials_cut_at_the_window_start_do_not_survive_a_sanitizing_shrink(tmp_path):
    log = tmp_path / "verifier.log"  # #335: escape codes shrink the window below 2048 characters
    log.write_bytes(b"https://user:" + b"\x1b[m" * 6000 + b"SECRET@x END")
    assert "SECRET" not in probe_log_tail(log)
    log.write_bytes(b"https://user:" + b"A/" * 10000 + b"SECRET@x END")  # no shrink: the scheme
    assert "SECRET" not in probe_log_tail(log)  # is before the window, no "://" inside it
    log.write_bytes(b"https://user:" + b"\x1b[m" * 6000 + b"SECRET@x\nnext line")
    assert probe_log_tail(log) == "next line"  # later lines are kept


def test_an_ordinary_long_log_keeps_its_last_2048_characters(tmp_path):
    log = tmp_path / "verifier.log"
    text = "".join(f"line {i:05d} \x1b[32mok\x1b[0m\r\n" for i in range(2000))  # ~56 KB
    log.write_bytes(text.encode())
    expected = "".join(f"line {i:05d} ok\n" for i in range(2000))[-2048:]
    assert probe_log_tail(log) == expected
