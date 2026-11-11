"""Tests for Issue #1145: mask_secrets linear cost and equivalence."""

from __future__ import annotations

import random
import re
import time
import unicodedata
from typing import Any


def _old_is_key(name: str) -> bool:
    from cairnwork.p2p_probe_log import _KEY

    for n in (name, name.casefold(), unicodedata.normalize("NFKC", name).casefold()):
        pieces = [n, *(n[i + 1 :] for i, c in enumerate(n) if c == "_")]
        if any(_KEY.fullmatch(x) for x in pieces):
            return True
    return False


def _old_mask_userinfo(text: str) -> str:
    from cairnwork.p2p_probe_log import _URL_USERINFO

    return _URL_USERINFO.sub("", text)


def _ref_mask_secrets(text: str) -> str:
    from cairnwork.p2p_probe_log import (
        _ANSI,
        _BEARER,
        _COLON,
        _ENCODED,
        _FLAG,
        _FLAG_HEAD,
        _HEADER,
        _HID,
        _KEY,
        _NAME,
        _URL_USERINFO,
        _VALUE,
        replace_hidden,
    )

    def _ref_is_secret_name(t: str, start: int, name: str, names: tuple[str, str]) -> bool:
        if start > 0 and t[start - 1] == "-" and _HID not in name:
            flag = _FLAG_HEAD.search(t, max(0, start - 70), start)
            if flag is not None:
                full = (flag.group(1) + name).replace("-", "_")
                return any(_old_is_key(n) for n in (*names, full))
            word = re.search(r"\w{1,64}$", t[max(0, start - 65) : start - 1])
            return word is not None and bool(_KEY.fullmatch(f"{word.group()}_{name}".casefold()))
        return any(_old_is_key(n) for n in names)

    def _ref_mask_fields(t: str) -> str:
        out: list[str] = []
        pos = 0
        for m in _NAME.finditer(t):
            name = m.group(1)
            names = (name.replace(_HID, ""), name.rsplit(_HID, 1)[-1])
            if m.start() < pos or not _ref_is_secret_name(t, m.start(), name, names):
                continue
            value = _VALUE.match(t, m.end())
            if value is not None:
                out += [t[pos : m.end()], "<redacted>"]
                pos = value.end()
        return "".join([*out, t[pos:]])

    t = _URL_USERINFO.sub(
        "", replace_hidden(_ANSI.sub("", text).replace("\r\n", "\n"), _HID, keep="\n\t")
    )
    for rx in (_HEADER, _BEARER, _COLON, _FLAG, _ENCODED):
        t = rx.sub(r"\1<redacted>", t)
    return _ref_mask_fields(t).replace(_HID, "?")


def test_is_key_equivalence() -> None:
    from cairnwork.p2p_probe_log import _is_key

    required_names = [
        "GITHUB_TOKEN",
        "a_b_c_secret",
        "ＴＯＫＥＮ",
        "my_token_",
        "token",
        "not_a_key",
        "x" * 50 + "_token",
        "x" * 50 + "_notakey",
    ]
    extra_names = [
        "SECRET_ACCESS_KEY",
        "aws_secret_access_key",
        "ACCESS_TOKEN",
        "refresh_token",
        "id_token",
        "auth_token",
        "session_token",
        "password",
        "passwd",
        "pwd",
        "client_secret",
        "api_key",
        "apikey",
        "secret_key",
        "private_key",
        "API_KEY",
        "user_password",
        "db_pwd",
        "normal_field",
        "not_a_token_either",
        "_token",
        "token_",
        "___",
        "a_b_c_d_e_f_token",
        "deeply_nested_prefix_with_many_underscores_secret",
        "deeply_nested_prefix_with_many_underscores_normal",
    ]
    rng = random.Random(42)
    random_names = [
        "_".join(
            rng.choices(
                ["foo", "bar", "token", "secret", "baz", "key", "x"],
                k=rng.randint(3, 15),
            )
        )
        for _ in range(20)
    ]
    all_names = required_names + extra_names + random_names
    assert len(all_names) >= 30

    for name in all_names:
        assert _is_key(name) == _old_is_key(name), f"Mismatch on {name!r}"


def test_userinfo_masking_equivalence() -> None:
    from cairnwork.p2p_probe_log import _mask_userinfo

    test_strings = [
        "https://u:p@h/x",
        "https://u:p@ss@h/x",
        "x@y://u:p@h",
        "https://h/no-at",
        "prefix https://u:p@h1/a middle https://v:w@h2/b suffix",
        "tabs\tand\nnewlines\nhttps://u:p@h/path as separators",
        "https://u:p\rsecret@h/x",
        "foo@bar https://u:p@h/x",
        "",
        "https://",
        "http://foo:bar@example.com:8080/path?q=1#frag",
        "ssh://git:secret@github.com/repo.git",
        "ftp://anonymous:guest@ftp.example.com",
        "no url here at all",
        "just a bare @ sign in text",
        "email@example.com without scheme",
        "https://no-userinfo.com/@user/repo",
        "custom://a:b:c@host/path",
        "http://user@host",
        "http://:password@host",
        "http://user:@host",
        "http://user:p@ss:w@rd@host",
        "https://u:p\r\n@h/x",
        "run1://u:p@h1\trun2://v:w@h2\nrun3://x:y@h3",
        "https://user:pass@host/a/b/c?k=v&x=y",
        "https://@host",
        "https://user@host/path/@123",
        "pre://u1:p1@h1 mid://u2:p2@h2 post://u3:p3@h3",
        "://user:pass@host",
        "scheme://",
        "https://h/no-at/still-no-at",
        "text with https://user:pass@host and trailing text",
        "https://a:b@c\rtoken@d",
        "x@y://h/no-at",
    ]
    assert len(test_strings) >= 30

    for s in test_strings:
        assert _mask_userinfo(s) == _old_mask_userinfo(s), f"Mismatch on {s!r}"


def test_cost_many_underscores() -> None:
    from cairnwork import p2p_probe_log

    real_key = p2p_probe_log._KEY

    class KeyCounter:
        def __init__(self, key: Any) -> None:
            self.real = key
            self.calls = 0

        def fullmatch(self, s: str) -> Any:
            self.calls += 1
            return self.real.fullmatch(s)

    counter = KeyCounter(real_key)
    p2p_probe_log._KEY = counter
    try:
        # Long name with 50,000 underscores: candidate count check
        p2p_probe_log._is_key("_" * 50000)
        # On new code: at most (1 + _MAX_KEY_LEN) candidates checked per form * 3 forms = 54 calls.
        # On old code or no-early-stop mutant: checks all 50,001 suffixes per form = 150,003 calls.
        max_allowed = 3 * (p2p_probe_log._MAX_KEY_LEN + 2)
        assert counter.calls <= max_allowed, f"Too many calls: {counter.calls} > {max_allowed}"

        # Long prefix ending in non-key
        counter.calls = 0
        p2p_probe_log._is_key("x" * 50000 + "_nokey")
        assert counter.calls <= max_allowed, f"Too many calls: {counter.calls} > {max_allowed}"

        # Suffix of length exactly _MAX_KEY_LEN (17 chars: secret_access_key) matches.
        # This kills mutant "_MAX_KEY_LEN - 1" (which would stop at 16 and fail to match).
        assert p2p_probe_log._is_key("x" * 50000 + "_secret_access_key") is True
        assert p2p_probe_log._is_key("x" * 50000 + "_notakey") is False
    finally:
        p2p_probe_log._KEY = real_key


def test_cost_many_schemes() -> None:
    from cairnwork.p2p_probe_log import mask_secrets

    s10k = "://" * 10000
    s20k = "://" * 20000

    mask_secrets("://")  # warm up

    t0 = time.monotonic()
    mask_secrets(s10k)
    t1 = time.monotonic() - t0

    t0 = time.monotonic()
    mask_secrets(s20k)
    t2 = time.monotonic() - t0

    assert t1 < 1.0, f"10k :// took {t1:.3f}s >= 1.0s"
    assert t2 < 1.0, f"20k :// took {t2:.3f}s >= 1.0s"
    if t1 < 0.01:
        t0 = time.monotonic()
        for _ in range(5):
            mask_secrets(s10k)
        t1 = (time.monotonic() - t0) / 5
        t0 = time.monotonic()
        for _ in range(5):
            mask_secrets(s20k)
        t2 = (time.monotonic() - t0) / 5
    assert t2 < 3.0 * max(t1, 0.001), (
        f"Growth {t2 / max(t1, 0.001):.2f}x >= 3x (t1={t1:.4f}s, t2={t2:.4f}s)"
    )


def test_cost_64kib_line_with_many_schemes() -> None:
    from cairnwork.p2p_probe_log import mask_secrets

    # 32 KiB with 6,000 schemes vs 64 KiB with 12,000 schemes.
    # On old code, 12,000 schemes scans 12,000 * 35,000 chars = 420M ops (>2.5s), failing widely.
    s_half = ("a://" * 6000) + ("x" * (32 * 1024 - 24000))
    s_full = ("a://" * 12000) + ("x" * (64 * 1024 - 48000))
    assert len(s_full) == 64 * 1024
    assert s_full.count("://") == 12000

    mask_secrets("://")  # warm up

    t0 = time.monotonic()
    mask_secrets(s_half)
    t1 = time.monotonic() - t0

    t0 = time.monotonic()
    mask_secrets(s_full)
    t2 = time.monotonic() - t0

    assert t2 < 1.0, f"64 KiB line took {t2:.3f}s >= 1.0s (old code takes >2.5s)"
    if t1 < 0.01:
        t0 = time.monotonic()
        for _ in range(5):
            mask_secrets(s_half)
        t1 = (time.monotonic() - t0) / 5
        t0 = time.monotonic()
        for _ in range(5):
            mask_secrets(s_full)
        t2 = (time.monotonic() - t0) / 5
    assert t2 < 3.0 * max(t1, 0.001), (
        f"Growth {t2 / max(t1, 0.001):.2f}x >= 3x (t1={t1:.4f}s, t2={t2:.4f}s)"
    )


def test_random_mixed_lines_equivalence() -> None:
    from cairnwork.p2p_probe_log import mask_secrets

    rng = random.Random(1145)
    chars = "abcdefghijklmnopqrstuvwxyz0123456789_=-:/.@ \t\n\r"
    tokens = [
        "https://u:p@example.com/api",
        "https://u:p@ss@example.com/path",
        "http://no-creds.com/test",
        "x@y://u:p@h",
        "https://h/no-at",
        "token=secret123",
        "GITHUB_TOKEN=ghp_ABC123",
        "a_b_c_secret=val456",
        "my_token_=not_secret",
        "not_a_key=public",
        "Bearer eyJhbGciOi",
        "Authorization: Basic dXNlcjpwYXNz",
        "token: mytoken",
        "--password mypass",
        "api-key: secretkey",
        "normal_text",
        "\x1b[32mgreen\x1b[0m",
        "ＴＯＫＥＮ=fullwidth",
        "://",
        "@foo",
        "foo@",
    ]

    lines: list[str] = []
    for _ in range(200):
        parts: list[str] = []
        n_parts = rng.randint(1, 8)
        for _ in range(n_parts):
            if rng.random() < 0.6:
                parts.append(rng.choice(tokens))
            else:
                length = rng.randint(1, 30)
                parts.append("".join(rng.choice(chars) for _ in range(length)))
        sep = rng.choice([" ", "  ", "\t", "\n", " & ", "; "])
        lines.append(sep.join(parts))

    for line in lines:
        assert mask_secrets(line) == _ref_mask_secrets(line)
