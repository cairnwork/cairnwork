"""#1556: encoded or upper-cased secrets and a split PEM header are refused. Fake secrets only."""

from __future__ import annotations

import base64

import pytest

from experimental.pause import schema
from experimental.pause.client import HaikuGate, HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, GateRequest, PlanRequest
from experimental.pause.secrets_decode import MAX_CHARS, MAX_VIEWS, decoded_views
from experimental.pause.secrets_scan import Scanner, scan

SETTINGS = CallSettings("haiku-test", "high", 3.0, 3)
NAMES = ("jdoe-account-name-here",)
MARK = "ZQXJ9marker4471"  # unique: must never reach a reply field
KEY = f"sk-ant-{MARK}abcdef"
PEM = "-----BEGIN RSA PRIVATE KEY-----"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _esc_u(s: str) -> str:
    return "".join(f"\\u{ord(c):04x}" for c in s)


CASES = [
    ("upper", KEY.upper(), "api_key"),
    ("url", KEY.replace("-", "%2D"), "api_key"),
    ("url_twice", KEY.replace("-", "%252D"), "api_key"),
    ("u_escape", f"x {KEY[:3]}{_esc_u('-')}{KEY[4:]}", "api_key"),
    ("u_all", _esc_u(KEY), "api_key"),
    ("pct_u", KEY.replace("-", "%u002d"), "api_key"),
    ("x_escape", KEY.replace("-", "\\x2d"), "api_key"),
    ("entity", KEY.replace("-", "&#45;"), "api_key"),
    ("base64", f"data: {_b64(KEY)}", "api_key"),
    ("base64url", _b64(KEY + "??>>").replace("+", "-").replace("/", "_"), "api_key"),
    ("base32", base64.b32encode(KEY.encode()).decode(), "api_key"),
    ("hex", KEY.encode().hex(), "api_key"),
    ("b64_of_url", _b64(KEY.replace("-", "%2D")), "api_key"),
    ("b64_assign", _b64(f"password={MARK}longvalue"), "assigned_secret"),
    ("pem_upper", PEM.lower(), "private_key"),
    ("pem_split", "-----BEGIN RSA\nPRIVATE KEY-----", "private_key"),
    ("pem_split3", "-----BEGIN\n  RSA\r\n PRIVATE\nKEY-----", "private_key"),
    ("pem_b64", _b64(PEM), "private_key"),
    ("pem_hex", PEM.encode().hex(), "private_key"),
    ("pem_plus", "-----BEGIN+RSA+PRIVATE+KEY-----", "private_key"),
    ("pem_underscore", "-----BEGIN_RSA_PRIVATE_KEY-----", "private_key"),
    ("pem_cross_line_upper", "-----Begin PRIVATE\nKey-----", "private_key"),
    # reviewer round 1 (cw-review-desk-a): alignment, splits, deeper layers, normalization
    ("b64_after_x", "x" + _b64(KEY), "api_key"),
    ("b64_after_ab", "ab" + _b64(KEY), "api_key"),
    ("b64_after_path", "/v1/" + _b64(KEY), "api_key"),
    ("b64_trailing_text", _b64(KEY) + "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "api_key"),
    ("b64_lines", "\n".join(_b64(KEY * 2)[i : i + 20] for i in range(0, 200, 20)), "api_key"),
    ("b64_spaces", " ".join(_b64(KEY * 2)[i : i + 20] for i in range(0, 200, 20)), "api_key"),
    ("hex_colon", ":".join(f"{b:02x}" for b in KEY.encode()), "api_key"),
    ("hex_space", " ".join(f"{b:02x}" for b in KEY.encode()), "api_key"),
    ("hex_after_text", "ab" + KEY.encode().hex(), "api_key"),
    ("base32_lower", base64.b32encode(KEY.encode()).decode().lower(), "api_key"),
    ("x_double", KEY.replace("-", "\\\\x2d"), "api_key"),
    ("u_double", KEY.replace("-", "\\\\u002d"), "api_key"),
    ("u_brace", KEY.replace("-", "\\u{2d}"), "api_key"),
    ("b64_x3", _b64(_b64(_b64(KEY))), "api_key"),
    ("fullwidth_pct", KEY.replace("-", "％２Ｄ"), "api_key"),
    ("fullwidth_b64", _b64(KEY).translate({i: i + 0xFEE0 for i in range(33, 127)}), "api_key"),
    ("zwsp_in_pct", KEY.replace("-", "%2\u200bD"), "api_key"),
    ("shortest_b64", _b64("sk-ant-" + "a1" * 6), "api_key"),
    ("url_three_rounds", KEY.replace("-", "%25252D"), "api_key"),
    ("b64_account", _b64("jdoe-account-name-here"), "account_name"),
    ("upper_cross_line", KEY.upper().replace("ABC", "ABC\n", 1), "api_key"),
]


@pytest.mark.parametrize("name, text, category", CASES, ids=[c[0] for c in CASES])
def test_scan_flags_each_class(name, text, category):
    assert category in scan(text, NAMES)


class _Transport:
    def __init__(self):
        self.calls = 0

    def send(self, model, effort, messages, timeout):
        self.calls += 1
        return TransportResult(200, {"text": "ACCEPT"})


@pytest.mark.parametrize("name, text, category", CASES, ids=[c[0] for c in CASES])
def test_real_entry_blocks_before_the_transport(name, text, category):
    for transport, reply in (
        (
            t := _Transport(),
            HaikuGate(t, Scanner(NAMES)).review(GateRequest("t", "c", text, SETTINGS)),
        ),
        (
            t2 := _Transport(),
            HaikuPlanner(t2, Scanner(NAMES)).plan(
                PlanRequest("t", "c", ({"role": "user", "content": text},), SETTINGS)
            ),
        ),
    ):
        assert transport.calls == 0
        assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
        assert category in reply.diagnostics
        assert MARK not in repr(reply)


def test_result_never_holds_the_matched_text():
    assert all(MARK not in h for h in scan(_b64(KEY)))


@pytest.mark.parametrize(
    "text",
    [
        "git sha 3f2a9c1d8e7b6a5f4e3d2c1b0a99887766554433 merged",
        "see https://example.com/a%20b?q=hello%2Cworld&x=1",
        "the quick brown fox jumps over the lazy dog " * 3,
        "ordinary &amp; text with \\n and \\t escapes",
        "path /usr/local/bin and token = next_token()",
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        "-----BEGIN CERTIFICATE-----",
        "begin the private key rotation, then end",
        "region asia-southeast1-a and DESK-1234-fix-login-bug",
        "TASK-4821-migrate-db-schema Asian-American-heritage-month-2024",
        "GHS_RUNNER_TEMP_DIRECTORY",
        pytest.param("word " * 60_000 + " a &amp; b 100%25", id="long-plain-with-escape"),
    ],
)
def test_ordinary_text_is_still_clean(text):
    assert scan(text) == []


def test_bounds_fail_closed():
    many = " ".join(_b64(f"visible text number {i} here") for i in range(MAX_VIEWS + 5))
    assert decoded_views([many]) is None
    assert scan(many)[-1] == "encoded_overflow"
    assert scan("x" * 100 + " " + _b64("y" * 300_000))[-1] == "encoded_overflow"


def test_scan_time_is_bounded():
    import time

    start = time.monotonic()
    result = scan(_b64("password=" + "q" * 150_000))  # one long encoded blob
    assert time.monotonic() - start < 5.0
    assert result[-1] == "encoded_overflow"


def test_limits_are_pinned():
    assert MAX_VIEWS == 32
    views = [_b64(f"visible text number {i:03d} is here") for i in range(MAX_VIEWS)]
    assert decoded_views([" ".join(views)]) is not None
    assert decoded_views([" ".join([*views, _b64("one more visible text here")])]) is None


def test_plain_results_unchanged_order():
    assert scan(f"API_KEY=abcdEFGH12345678abcd {KEY}") == ["api_key", "assigned_secret"]


@pytest.mark.parametrize("pad", [96, 128, 129, 200, 1000, 3000])
def test_binary_padding_in_front_hides_nothing(pad):
    raw = bytes(range(128, 256)) * (pad // 128 + 1)
    blob = base64.b64encode(raw[:pad] + KEY.encode() + b"=" * 5).decode()
    hits = Scanner(NAMES).scan(f"data {blob}")
    assert "api_key" in hits
    assert "encoded_overflow" not in hits


@pytest.mark.parametrize("size", [MAX_CHARS + 4, 1_000_000])
def test_huge_encoded_run_is_refused_fast(size):
    import time

    blob = base64.b64encode(bytes(range(256)) * (size // 340 + 1)).decode()[:size]
    start = time.monotonic()
    assert decoded_views((blob,)) is None  # refused before any decoding (the bound itself)
    assert time.monotonic() - start < 3
    assert "encoded_overflow" in Scanner(NAMES).scan(blob)


def _fullwidth(text):
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


@pytest.mark.parametrize(
    "key",
    [
        KEY[:8] + "\u200b" + KEY[8:],
        KEY[:8] + "\x01" + KEY[8:],
        KEY[:8] + "\ufeff\u00ad" + KEY[8:],
        _fullwidth(KEY),
    ],
    ids=["zero-width", "control", "bom-soft-hyphen", "full-width"],
)
@pytest.mark.parametrize(
    "wrap", [lambda s: base64.b64encode(s.encode()).decode(), lambda s: s.encode().hex()]
)
def test_invisible_chars_inside_an_encoded_secret(key, wrap):
    hits = Scanner(NAMES).scan(f"data {wrap(key)}")
    assert "api_key" in hits
    assert "encoded_overflow" not in hits


@pytest.mark.parametrize("sep", ["\v", "\f", "\x1c", "\x1f", "\x85", "\u2028", "\u2029", "\u1680"])
def test_any_whitespace_rejoins_wrapped_base64(sep):
    blob = base64.b64encode((KEY + "x" * 40).encode()).decode()
    pieces = [blob[i : i + 20] for i in range(0, len(blob), 20)]
    assert "api_key" in Scanner(NAMES).scan(sep.join(pieces))


_HIDERS = [
    "\u0080", "\u034f", "\ufe0f", "\U000e0041", "\u180e", "\u061c", "\ufff9", "\ue000",
    "\uffa0", "\u3164", "\u200b", "\x01", "\ufeff", "\u00ad",
]  # fmt: skip


@pytest.mark.parametrize("hider", _HIDERS, ids=[f"U+{ord(h[0]):04X}" for h in _HIDERS])
@pytest.mark.parametrize(
    "wrap", [lambda s: base64.b64encode(s.encode()).decode(), lambda s: s.encode().hex()]
)
def test_every_hider_category_inside_an_encoded_secret(hider, wrap):
    hits = Scanner(NAMES).scan(f"data {wrap(KEY[:9] + hider + KEY[9:])}")
    assert "api_key" in hits
    assert "encoded_overflow" not in hits


def test_math_alphanumeric_key_is_folded():
    bold = "".join(chr(0x1D41A + ord(c) - 97) if "a" <= c <= "z" else c for c in KEY)
    assert "api_key" in Scanner(NAMES).scan(base64.b64encode(bold.encode()).decode())


def test_nested_layers_each_with_a_hider():
    inner = base64.b64encode((KEY[:9] + "\u200b" + KEY[9:]).encode()).decode()
    outer = base64.b64encode((inner[:10] + "\u200b" + inner[10:]).encode()).decode()
    assert "api_key" in Scanner(NAMES).scan(outer)


@pytest.mark.parametrize(
    "text",
    [
        "img " + base64.b64encode(bytes(5000)).decode(),
        base64.b64encode(("hello world " * 210).encode("utf-16-le")).decode(),
        "00" * 4200,
    ],
    ids=["zero-buffer", "utf16-text", "zero-hex"],
)
def test_binary_and_wide_text_are_not_refused(text):
    assert Scanner(NAMES).scan(text) == []


_PLAIN = [
    KEY[:9] + "\x85" + KEY[9:],
    KEY[:9] + "\u2028" + KEY[9:],
    KEY[:9] + "\u2029" + KEY[9:],
    "curl --api-key\u200bsk-live-" + MARK + "aaaabbbbcccc",
    "curl --api-key\u00adsk-live-" + MARK + "aaaabbbbcccc",
    "curl --api-key sk-live-" + MARK + "aaaabbbbcccc",
    "API_TOKEN=" + MARK + "aaaabbbbccccdddd",
    KEY[:9] + "\u00a0" + KEY[9:],
    "login " + NAMES[0] + " now",
    *(
        "Authorization: Bearer"
        + "\u200b" * n
        + " sk-live-"
        + MARK
        + "abcdefghijklmnopqrstuvwxyz012"
        for n in (1, 4, 5, 8, 40)
    ),
    *(
        "Authorization: " + word + "\u200b" * 4 + " abcdEFGH1234ijklMNOP5678"
        for word in (
            "\uff22\uff45\uff41\uff52\uff45\uff52",
            "\U0001d401\U0001d41e\U0001d41a\U0001d42b\U0001d41e\U0001d42b",
        )
    ),
    "Authorization: Bearer\u200b\u200d\u2060\ufeff sk-live-"
    + MARK
    + "abcdefghijklmnopqrstuvwxyz012",
    "Authorization: Bearer\x01\x02\x03\x04\x05 sk-live-" + MARK + "abcdefghijklmnopqrstuvwxyz012",
]


@pytest.mark.parametrize("plain", _PLAIN, ids=range(len(_PLAIN)))
@pytest.mark.parametrize(
    "wrap", [lambda s: base64.b64encode(s.encode()).decode(), lambda s: s.encode().hex()]
)
def test_encoded_gets_the_same_hits_as_plain(plain, wrap):
    want = set(Scanner(NAMES).scan(plain))
    assert want
    assert set(Scanner(NAMES).scan(f"data {wrap(plain)}")) == want


def test_long_invisible_run_stays_short():
    from experimental.pause.secrets_decode import _pieces

    (piece,) = _pieces("a" + "\u200b" * 5000 + " b")
    assert len(piece) < 20 and piece.endswith(" b")
