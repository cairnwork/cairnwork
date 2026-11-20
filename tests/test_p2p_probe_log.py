"""#331: the tail of a failed base probe's log, as it goes into the output's logs."""

import pytest

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


def test_a_secret_field_name_with_a_hidden_character_or_odd_case_is_still_masked(tmp_path):
    log = tmp_path / "verifier.log"
    names = [
        "pass\u200bword",
        "pass\u00adword",
        "pass\u202eword",
        "PassWord",
        "api\u200b_KEY",
        "x\u200btoken",
    ]
    log.write_text("".join(f"{n}=PLACEHOLDER\n" for n in names) + "color=blue\n")
    tail = probe_log_tail(log)
    assert "PLACEHOLDER" not in tail and tail.endswith("color=blue\n")
    assert tail.count("<redacted>") == len(names)


def test_a_secret_field_inside_another_fields_value_is_still_masked(tmp_path):
    log = tmp_path / "verifier.log"
    lines = [
        "env=PASSWORD=V1", "color=token=V2 x", "user=x:password=V3", "foo=client_secret=V4&z=1",
        "a=1,password=V5", "user=bob,token=V6", "-\u200bpassword=V7", "-x\u200bid_token=V8",
    ]  # fmt: skip
    log.write_text("\n".join(lines) + "\n")
    tail = probe_log_tail(log)
    assert not any(f"V{i}" in tail for i in range(1, 9)), tail
    assert "z=1" in tail and "color=token=" in tail


def test_full_width_underscore_in_secret_name_is_masked(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text(
        "\n".join(
            (
                "ＡＰＩ＿ＫＥＹ=v1",
                "ＳＥＣＲＥＴ＿ＫＥＹ=v3",
                "ＧＩＴＨＵＢ＿ＴＯＫＥＮ=v2",
            )
        )
        + "\n"
    )
    tail = probe_log_tail(log)
    assert not any(value in tail for value in ("v1", "v2", "v3")), tail
    assert tail.count("<redacted>") == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("deploy-github＿token=v", "deploy-github＿token=<redacted>"),
        (
            "x-ＧＩＴＨＵＢ＿ＴＯＫＥＮ=v",
            "x-ＧＩＴＨＵＢ＿ＴＯＫＥＮ=<redacted>",
        ),
        ("ci-db＿password=v", "ci-db＿password=<redacted>"),
        ("my-app＿secret=v", "my-app＿secret=<redacted>"),
        ("foo-bar＿api_key=v", "foo-bar＿api_key=<redacted>"),
        ("ＡＰＩ＿ＫＥＹ=v1", "ＡＰＩ＿ＫＥＹ=<redacted>"),
        ("ＳＥＣＲＥＴ＿ＫＥＹ=v3", "ＳＥＣＲＥＴ＿ＫＥＹ=<redacted>"),
        (
            "ＧＩＴＨＵＢ＿ＴＯＫＥＮ=v2",
            "ＧＩＴＨＵＢ＿ＴＯＫＥＮ=<redacted>",
        ),
        ("API＿KEY=v", "API＿KEY=<redacted>"),
        ("SECRET＿ACCESS＿KEY=v", "SECRET＿ACCESS＿KEY=<redacted>"),
        ("--x-b＿token=v", "--x-b＿token=<redacted>"),
        ("color=x＿token=s", "color=x＿token=<redacted>"),
        ("my-token=v", "my-token=v"),
        ("deploy-github_token=v", "deploy-github_token=v"),
        ("x-api＿key=v", "x-api＿key=v"),
        ("my-api＿key=v", "my-api＿key=v"),
        ("token＿=v", "token＿=v"),
        ("＿＿=v", "＿＿=v"),
        ("a=1 ＿=2", "a=1 ＿=2"),
    ],
)
def test_dashed_name_cut_at_full_width_underscore_matches_main(tmp_path, text, expected):
    log = tmp_path / "verifier.log"
    log.write_text(text)
    assert probe_log_tail(log) == expected


def test_dotted_and_dotless_i_in_a_secret_name_still_count(tmp_path):
    log = tmp_path / "verifier.log"
    log.write_text("apİ_key=W1\napıkey=W2\nİd_token=W3\n")
    tail = probe_log_tail(log)
    assert "W1" not in tail and "W2" not in tail and "W3" not in tail
    assert tail.count("<redacted>") == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("x＿apİ_key=SV", "x＿apİ_key=<redacted>"),
        ("x＿apİkey=SV", "x＿apİkey=<redacted>"),
        ("x＿APİ_KEY=SV", "x＿APİ_KEY=<redacted>"),
        ("x＿APİKEY=SV", "x＿APİKEY=<redacted>"),
        ("x＿prİvate_key=SV", "x＿prİvate_key=<redacted>"),
        ("x＿PRİVATE_KEY=SV", "x＿PRİVATE_KEY=<redacted>"),
        ("--a-x＿apİ_key=SV", "--a-x＿apİ_key=<redacted>"),
        ("a=x＿apİkey=SV", "a=x＿apİkey=<redacted>"),
        ("k=1&x＿apİ_key=SV&z=1", "k=1&x＿apİ_key=<redacted>&z=1"),
        ("a\x00x＿apİ_key=SV", "a?x＿apİ_key=<redacted>"),
        ("x＿a\x00pİ_key=SV", "x＿a?pİ_key=<redacted>"),
        ("x＿apİ=SV", "x＿apİ=SV"),
        ("x＿İd=SV", "x＿İd=SV"),
        ("x＿apİ_kex=SV", "x＿apİ_kex=SV"),
        ("x＿İ_key=SV", "x＿İ_key=SV"),
        ("my-x＿apİ_keys=SV", "my-x＿apİ_keys=SV"),
        ("foo＿bar=SV", "foo＿bar=SV"),
        ("my-token=SV", "my-token=SV"),
        ("x-api＿key=SV", "x-api＿key=SV"),
    ],
)
def test_full_width_underscore_keeps_dotted_capital_i_masked(tmp_path, text, expected):
    log = tmp_path / "verifier.log"
    log.write_text(text)
    assert probe_log_tail(log) == expected


_FORMS = {  # #402: one failing line per credential form the Issue lists
    "env_token": ("GITHUB_TOKEN=fake-token-123", "GITHUB_TOKEN=<redacted>"),
    "env_password": ("PYPI_PASSWORD=fake-token-123", "PYPI_PASSWORD=<redacted>"),
    "env_secret_access_key": (
        "AWS_SECRET_ACCESS_KEY=fake-token-123",
        "AWS_SECRET_ACCESS_KEY=<redacted>",
    ),
    "flag_token": ("--token=fake-token-123", "--token=<redacted>"),
    "flag_password": ("--password=fake-token-123", "--password=<redacted>"),
    "flag_space": ("run --password fake-token-123 --next", "run --password <redacted> --next"),
    "dashed_secret": ("client-secret=fake-token-123", "client-secret=<redacted>"),
    "dashed_api_key": ("api-key=fake-token-123", "api-key=<redacted>"),
    "colon_token": ("token: fake-token-123", "token: <redacted>"),
    "colon_password": ("password: fake-token-123", "password: <redacted>"),
    "space_before_colon": ("Authorization : fake-token-123", "Authorization : <redacted>"),
    "x_api_key": ("X-Api-Key: fake-token-123", "X-Api-Key: <redacted>"),
    "x_auth_token": ("X-Auth-Token: fake-token-123", "X-Auth-Token: <redacted>"),
    "private_token": ("Private-Token: fake-token-123", "Private-Token: <redacted>"),
    "cookie": ("Cookie: sid=fake-token-123; b=2", "Cookie: <redacted>"),
    "json_quoted": ('{"Authorization": "Basic fake-token-123"}', '{"Authorization": <redacted>'),
    "repr_quoted": ("{'Authorization': 'Basic fake-token-123'}", "{'Authorization': <redacted>"),
    "url_encoded": ("access_token%3Dfake-token-123&x=1", "access_token%3D<redacted>&x=1"),
    "compound_flag_equals": ("--github-token=fake-token-123", "--github-token=<redacted>"),
    "compound_flag_key": (
        "--aws-secret-access-key=fake-token-123",
        "--aws-secret-access-key=<redacted>",
    ),
    "compound_flag_space": ("--pypi-password fake-token-123", "--pypi-password <redacted>"),
    "flag_private_key": ("--private-key fake-token-123", "--private-key <redacted>"),
    "full_width": ("ｐａｓｓｗｏｒｄ=fake-token-123", None),
}


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_each_credential_form_the_tail_used_to_print_is_masked(tmp_path, form):
    line, expected = _FORMS[form]
    log = tmp_path / "verifier.log"
    log.write_text(f"before\n{line}\nafter\n")
    tail = probe_log_tail(log)
    assert "fake-token-123" not in tail and tail.startswith("before\n") and tail.endswith("after\n")
    if expected is not None:
        assert tail == f"before\n{expected}\nafter\n"


def test_look_alike_text_next_to_the_new_forms_is_left_alone(tmp_path):
    log = tmp_path / "verifier.log"
    text = (
        "my-token=plain tokens: 5 used password_policy=strict OLDPWD=/home/u\n"
        "run --password-file /tmp/pw --verbose --token --quiet Content-Type: text/plain\n"
        "retry after: 5s status: ok key%3Dvalue cooking: slow\n"
    )
    log.write_text(text)
    assert probe_log_tail(log) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("API︳KEY=SV", "API︳KEY=<redacted>"),
        ("API︴KEY=SV", "API︴KEY=<redacted>"),
        ("API﹍KEY=SV", "API﹍KEY=<redacted>"),
        ("API﹎KEY=SV", "API﹎KEY=<redacted>"),
        ("API﹏KEY=SV", "API﹏KEY=<redacted>"),
        ("ＡＰＩ︳ＫＥＹ=SV", "ＡＰＩ︳ＫＥＹ=<redacted>"),
        ("ＳＥＣＲＥＴ︴ＡＣＣＥＳＳ﹍ＫＥＹ=SV", "ＳＥＣＲＥＴ︴ＡＣＣＥＳＳ﹍ＫＥＹ=<redacted>"),
        ("ＧＩＴＨＵＢ﹎ＴＯＫＥＮ=SV", "ＧＩＴＨＵＢ﹎ＴＯＫＥＮ=<redacted>"),
        ("ＰＲＩＶＡＴＥ﹏ＫＥＹ=SV", "ＰＲＩＶＡＴＥ﹏ＫＥＹ=<redacted>"),
        ("deploy-github︳token=SV", "deploy-github︳token=<redacted>"),
        ("ci-db︴password=SV", "ci-db︴password=<redacted>"),
        ("my-app﹍secret=SV", "my-app﹍secret=<redacted>"),
        ("foo-bar﹎api_key=SV", "foo-bar﹎api_key=<redacted>"),
        ("foo-bar﹏api﹏key=SV", "foo-bar﹏api﹏key=<redacted>"),
        ("--x-b︳token=SV", "--x-b︳token=<redacted>"),
        ("GITHUB_TOKEN＝SV", "GITHUB_TOKEN＝<redacted>"),
        ("password＝SV", "password＝<redacted>"),
        ("ＡＰＩ＿ＫＥＹ＝SV", "ＡＰＩ＿ＫＥＹ＝<redacted>"),
        ("API︳KEY＝SV", "API︳KEY＝<redacted>"),
        ("--token＝SV", "--token＝<redacted>"),
        ("--github-token＝SV", "--github-token＝<redacted>"),
        ("client-secret＝SV", "client-secret＝<redacted>"),
        ("api-key＝SV", "api-key＝<redacted>"),
        ("env＝PASSWORD＝SV", "env＝PASSWORD＝<redacted>"),
        ("k=1&access_token＝SV&z=1", "k=1&access_token＝<redacted>&z=1"),
        ("ＡＰİ＿ＫＥＹ=SV", "ＡＰİ＿ＫＥＹ=<redacted>"),
        ("ＡＰİ︳ＫＥＹ=SV", "ＡＰİ︳ＫＥＹ=<redacted>"),
        ("ＡＰİ﹍ＫＥＹ＝SV", "ＡＰİ﹍ＫＥＹ＝<redacted>"),
        ("ＡＰı＿ＫＥＹ=SV", "ＡＰı＿ＫＥＹ=<redacted>"),
        ("x︳apİ_key=SV", "x︳apİ_key=<redacted>"),
        ("x﹏APİ_KEY=SV", "x﹏APİ_KEY=<redacted>"),
        ("ＰＲİＶＡＴＥ＿ＫＥＹ=SV", "ＰＲİＶＡＴＥ＿ＫＥＹ=<redacted>"),
        ("İＤ＿ＴＯＫＥＮ=SV", "İＤ＿ＴＯＫＥＮ=<redacted>"),
        ("ＰＡßＷＯＲＤ=SV", "ＰＡßＷＯＲＤ=<redacted>"),
        ("my-token＝SV", "my-token＝SV"),
        ("deploy-github_token＝SV", "deploy-github_token＝SV"),
        ("x-api＿key＝SV", "x-api＿key＝SV"),
        ("x-api︳key=SV", "x-api︳key=SV"),
        ("my-api﹏key=SV", "my-api﹏key=SV"),
        ("token︳=SV", "token︳=SV"),
        ("︳︳=SV", "︳︳=SV"),
        ("foo︳bar=SV", "foo︳bar=SV"),
        ("foo＝bar", "foo＝bar"),
        ("x︳apİ=SV", "x︳apİ=SV"),
        ("x﹏apİ_kex=SV", "x﹏apİ_kex=SV"),
        ("my-x︳apİ_keys=SV", "my-x︳apİ_keys=SV"),
        ("ⓣoken＝SECRETVAL", "ⓣoken＝<redacted>"),
        ("token﹦SV token⁼SV token₌SV", "token﹦<redacted> token⁼<redacted> token₌<redacted>"),
        ("API_KEY⩵SV API_KEY⩶SV", "API_KEY⩵<redacted> API_KEY⩶<redacted>"),
        (
            "ﬁle=ok ﬃ=ok paﬆ_token=SV ㌀ secret=SV",
            "ﬁle=ok ﬃ=ok paﬆ_token=<redacted> ㌀ secret=<redacted>",
        ),
    ],
)
def test_low_line_lookalikes_full_width_equals_and_mixed_scripts_1313(tmp_path, text, expected):
    log = tmp_path / "verifier.log"
    log.write_text(text)
    assert probe_log_tail(log) == expected


def test_every_single_codepoint_nfkc_variant_of_key_char_is_masked_1313():
    import unicodedata

    from cairnwork.p2p_probe_log import mask_secrets

    for cp in range(0x110000):
        ch = chr(cp)
        nf = unicodedata.normalize("NFKC", ch)
        if nf == ch or len(nf) != 1:
            continue
        low = nf.casefold()
        if low in "token":
            idx = "token".index(low)
            sample = "token"[:idx] + ch + "token"[idx + 1 :] + "=SECRETVAL"
            assert mask_secrets(sample) == sample[:-9] + "<redacted>", (hex(cp), sample)
        elif nf == "_":
            sample = f"api{ch}key=SECRETVAL"
            assert mask_secrets(sample) == f"api{ch}key=<redacted>", (hex(cp), sample)
        elif nf == "=":
            sample = f"token{ch}SECRETVAL"
            assert mask_secrets(sample) == f"token{ch}<redacted>", (hex(cp), sample)
