"""#1556: encoded or upper-cased secrets and a split PEM header are refused. Fake secrets only."""

from __future__ import annotations

import base64

import pytest

from experimental.pause import schema
from experimental.pause.client import HaikuGate, HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, GateRequest, PlanRequest
from experimental.pause.secrets_decode import MAX_VIEWS, decoded_views
from experimental.pause.secrets_scan import Scanner, scan

SETTINGS = CallSettings("haiku-test", "high", 3.0, 3)
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
]


@pytest.mark.parametrize("name, text, category", CASES, ids=[c[0] for c in CASES])
def test_scan_flags_each_class(name, text, category):
    assert category in scan(text)


class _Transport:
    def __init__(self):
        self.calls = 0

    def send(self, model, effort, messages, timeout):
        self.calls += 1
        return TransportResult(200, {"text": "ACCEPT"})


@pytest.mark.parametrize("name, text, category", CASES, ids=[c[0] for c in CASES])
def test_real_entry_blocks_before_the_transport(name, text, category):
    for transport, reply in (
        (t := _Transport(), HaikuGate(t, Scanner()).review(GateRequest("t", "c", text, SETTINGS))),
        (
            t2 := _Transport(),
            HaikuPlanner(t2, Scanner()).plan(
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
    ],
)
def test_ordinary_text_is_still_clean(text):
    assert scan(text) == []


def test_bounds_fail_closed():
    many = " ".join(_b64(f"visible text number {i} here") for i in range(MAX_VIEWS + 5))
    assert decoded_views(many) is None
    assert scan(many)[-1] == "encoded_overflow"
    assert scan("x" * 300_000 + " " + _b64("y" * 300_000))[-1] == "encoded_overflow"


def test_plain_results_unchanged_order():
    assert scan(f"API_KEY=abcdEFGH12345678abcd {KEY}") == ["api_key", "assigned_secret"]
