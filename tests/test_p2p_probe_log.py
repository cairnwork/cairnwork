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


def test_a_cut_credential_padded_with_escape_codes_that_hold_a_space_leaves_no_fragment(tmp_path):
    log = tmp_path / "verifier.log"  # #366: ESC[1 q has a space before its final byte
    log.write_bytes(b"https://user:" + b"\x1b[1 q" * 4000 + b"SECRET@x END")
    assert "SECRET" not in probe_log_tail(log)
    log.write_bytes(b"https://user:" + b"\x1b[1 q" * 4000 + b"SECRET@x\r50%\r100%\nnext line")
    assert probe_log_tail(log) == "next line"  # CR records after the cut go with the cut line


def test_a_base64_credential_with_slash_plus_and_equals_is_removed_whole(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("fetch https://u:ab/cd+ef==@example.com/x failed\n")
    assert probe_log_tail(log) == "fetch https://example.com/x failed\n"
    log.write_text("fetch https://u:ab/cd+ef==@example.com/x\n" + "y" * 2030)  # cut inside it
    tail = probe_log_tail(log)
    assert len(tail) == 2048 and not any(part in tail for part in ("ab/", "cd+", "ef=="))


def test_a_window_with_no_complete_line_says_so_instead_of_an_empty_tail(tmp_path):
    log = tmp_path / "verifier.log"  # #366: one 20 KB line of \r progress, or one-line JSON
    log.write_bytes(b"".join(b"\r%05d%%" % i for i in range(3500)))
    assert probe_log_tail(log) == "[tail omitted: no complete line in the last 16 KB]"
    log.write_bytes(b"x" * 20000 + b"\n")  # its only newline ends the window
    assert probe_log_tail(log) == "[tail omitted: no complete line in the last 16 KB]"
    log.write_bytes(b"short single line")  # the window starts the file: nothing is cut
    assert probe_log_tail(log) == "short single line"


def test_authorization_headers_bearer_tokens_and_secret_fields_are_masked(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text(
        "> Authorization: Basic dXNlcjpwYXNz\n"
        "proxy-authorization: Negotiate abc123 more\n"
        "curl -H 'X: Bearer eyJhbGciOi.payload.sig' https://h/x\n"
        "GET /api?a=1&access_token=AT_SECRET&b=2\n"
        "form: password=PW_SECRET, api_key=AK_SECRET; ApiKey=AK2_SECRET\n"
        "passwd=PWD2_SECRET pwd=PWD3_SECRET\n"
        "tok\x1b[0men=ANSI_SECRET\n"
    )
    tail = probe_log_tail(log)
    for secret in ("dXNlcjpwYXNz", "abc123", "eyJhbGciOi", "_SECRET"):
        assert secret not in tail
    assert tail == (
        "> Authorization: <redacted>\n"
        "proxy-authorization: <redacted>\n"
        "curl -H 'X: Bearer <redacted> https://h/x\n"
        "GET /api?a=1&access_token=<redacted>&b=2\n"
        "form: password=<redacted> api_key=<redacted> ApiKey=<redacted>\n"
        "passwd=<redacted> pwd=<redacted>\n"
        "token=<redacted>\n"
    )


def test_ordinary_text_that_resembles_a_secret_is_left_alone(tmp_path):
    log = tmp_path / "verifier.log"
    text = "token expired\napi_key_status=ok password_policy=strict\nmail user@example.com\n"
    text += "OLDPWD=/home/u my-token=plain\n"  # a key name only after a word char or '-'
    log.write_text(text)
    assert probe_log_tail(log) == text


def test_a_secret_field_is_masked_before_the_cut_lands_inside_its_key(tmp_path):
    log = tmp_path / "verifier.log"  # the last 2048 characters start at "word=": no key left
    log.write_text("password=FIELD_SECRET\n" + "y" * (2048 - len("word=<redacted>\n")))
    tail = probe_log_tail(log)
    assert len(tail) == 2048 and tail.startswith("word=<redacted>") and "FIELD_SECRET" not in tail
