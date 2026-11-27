"""#1886: Digest/Token/ApiKey authorization schemes and auth/credentials/session/cookie key
names are refused; prose and placeholders still pass (#1239 must not come back)."""

from __future__ import annotations

import json
import pathlib

import pytest

from experimental.pause.secrets_scan import Scanner

_FAKE = "fakevalue0123456789"


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Digest abcdef",
        'Authorization: Digest username="alice", realm="x", nonce="abc123"',
        "Authorization: Token abcdef",
        "Authorization: ApiKey abcdef",
        "authorization: api-key abcdef",
        'headers = {"Authorization": "Token abcdef123"}',
        'auth = "hunter2hunter2"',
        'credentials = "hunter2hunter2"',
        'creds = "hunter2hunter2"',
        'bearer = "hunter2hunter2"',
        'userinfo = "hunter2hunter2"',
        'session = "hunter2hunter2"',
        'cookie = "hunter2hunter2"',
        'cookies = "hunter2hunter2"',
        f"AUTH={_FAKE}",
        f"session_cookie: {_FAKE}",
        f'{{"credentials": "{_FAKE}"}}',
        f"--session {_FAKE}",
        f"--cookie={_FAKE}",
        f"sessionCookie = '{_FAKE}'",
    ],
)
def test_scheme_and_key_name_gaps_are_blocked(text: str) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "The digest authentication flow is described in the document.",
        "Token authentication and API key rotation are explained below.",
        "Authorization: Digest <redacted>",
        "Authorization: Token <YOUR_TOKEN>",
        "Authorization: ApiKey ${API_KEY}",
        "Authorization: Basic <base64-encoded username:password>",
        "Set the session cookie before calling the endpoint.",
        "session: str",
        "cookie = None",
        "auth = None,",
        "credentials = load_credentials()",
        "cookie = <redacted>",
        "session = <your-session>",
        "creds = ${CREDS}",
        'auth = os.environ["AUTH"]',
        "The userinfo part of a URL holds the user name.",
        "author = alice",
        "health: ok",
    ],
)
def test_prose_and_placeholders_still_pass(text: str) -> None:
    assert not Scanner().scan(text)


_OLD = ("password", "api_key", "token", "secret", "pwd", "passphrase")
_NEWW = ("session", "cookie", "auth", "creds", "credentials", "bearer", "userinfo")
_PLACEHOLDERS = (
    "<hunter2hunter2>",
    "$hunter2hunter2",
    '"<hunter2hunter2>"',
    'os.environ["x"]or["hunter2hunter2"]',
)


def _names(old: str, new: str) -> list[str]:
    return [f"{old}{new}", f"{old}_{new}", f"{old}-{new}", f"{old}{new.capitalize()}"]


@pytest.mark.parametrize("old", _OLD)
@pytest.mark.parametrize("new", _NEWW)
def test_old_word_names_keep_their_old_refusals_with_placeholder_values(old: str, new: str) -> None:
    for name in _names(old, new)[1:]:  # [0] is glued: main did not refuse that name
        for value in _PLACEHOLDERS:
            assert Scanner().scan(f"{name} = {value}"), (name, value)
        assert Scanner().scan(f'--{name}=os.getenv("X","hunter2hunter2")')
        assert Scanner().scan(f'{{"{name}": "<hunter2hunter2>"}}')


@pytest.mark.parametrize(
    "value",
    [
        'os.getenv("X","hunter2hunter2hunter2")',
        'os.getenv("X", default="hunter2hunter2")',
        'os.environ.get("X", "hunter2hunter2")',
        'os.environ["x"]or["hunter2hunter2"]',
        'os.environ["x"] or "hunter2hunter2"',
        'getenv("X") or "hunter2hunter2"',
    ],
)
def test_env_lookups_with_a_literal_fallback_are_not_placeholders(value: str) -> None:
    assert Scanner().scan(f"session_cookie = {value}")
    assert Scanner().scan(f"cookie = {value}")


@pytest.mark.parametrize(
    "text",
    [
        'session = os.getenv("SESSION")',
        'cookie = os.environ["COOKIE"]',
        'auth = os.environ.get("AUTH")',
        "Enable oauth for the login page.",
        "session: main",
        'author = "alice smith"',
        'headers = {"Authorization": "Token " + token}',
        'headers = {"Authorization": f"Token {self.key}"}',
        "self.http_session = requests.Session()",
        "session = Session()",
        '@pytest.fixture(scope="session")',
    ],
)
def test_more_must_pass(text: str) -> None:
    assert not Scanner().scan(text)


_REVIEW_MUST_BLOCK = [
    'password_session = os.getenv("X","hunter2hunter2")',
    'api_key_cookie = os.environ["x"]or["hunter2hunter2"]',
    'token_auth = "<hunter2hunter2>"',
    'secret_session = "$hunter2hunter2"',
    '--password-session=os.getenv("X","hunter2hunter2")',
    '{"password_session": "<hunter2hunter2>"}',
    'session_cookie = os.getenv("X","hunter2hunter2hunter2")',
]
_REVIEW_MUST_PASS = [
    "Enable oauth for the login page.",
    "session: main",
    'author = "alice smith"',
    'headers = {"Authorization": "Token " + token}',
    'headers = {"Authorization": f"Token {self.key}"}',
    "self.http_session = requests.Session()",
    "session = Session()",
    '@pytest.fixture(scope="session")',
]


@pytest.mark.parametrize("text", _REVIEW_MUST_BLOCK)
def test_reviewer_must_block_inputs(text: str) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize("text", _REVIEW_MUST_PASS)
def test_reviewer_must_pass_inputs(text: str) -> None:
    assert not Scanner().scan(text)


def test_head_blocks_everything_main_blocked() -> None:
    """Differential against a frozen file (made from 3e3097bd by gen_pause_secret_1886_main.py):
    every input main refused is still refused. A missing file fails; it never skips."""
    path = pathlib.Path(__file__).with_name("pause_secret_1886_main_blocked.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["blocked"]) >= 400
    for text in data["blocked"]:
        assert Scanner().scan(text), text


def differential_corpus() -> list[str]:
    corpus = [
        f"{n} = {v}" for o in _OLD for w in _NEWW for n in _names(o, w) for v in _PLACEHOLDERS
    ]
    corpus += [f"{o} = {v}" for o in _OLD for v in _PLACEHOLDERS]
    corpus += [f"{n}=hunter2hunter2hunter2" for o in _OLD for w in _NEWW for n in _names(o, w)]
    corpus += [f"--{n} hunter2hunter2hunter2" for o in _OLD for w in _NEWW for n in _names(o, w)]
    corpus += [f"{w}_{o}: <x1x1x1x1x>" for o in _OLD for w in _NEWW]
    return corpus


_SCHEMES = ("Token", "ApiKey", "api-key", "Api_Key", "API_KEY", "apikey", "TOKEN", "token")
_WRAPS = ("{s}", '"{s}"', "'{s}'")


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("wrap", _WRAPS)
def test_scheme_case_separator_quote_matrix(scheme: str, wrap: str) -> None:
    s = wrap.format(s=scheme)
    assert Scanner().scan(f"Authorization: {s} {_FAKE}")
    assert Scanner().scan(f'Authorization: "{scheme} {_FAKE}"')
    assert Scanner().scan(f"authorization = '{scheme} {_FAKE}'")


@pytest.mark.parametrize("brk", ["\r", "\n", "\r\n", "\v", "\f", "\x85", "\u2028", "\u2029"])
def test_header_name_split_by_any_line_break(brk: str) -> None:
    assert Scanner().scan(f"Authori{brk}zation: Token {_FAKE}")
    assert Scanner().scan(f"Authorization:{brk}Token {_FAKE}")


@pytest.mark.parametrize(
    "text", ["auth = hunter2()", "session = a1()", "cookie = x" + "y" * 40 + "()"]
)
def test_unquoted_calls_with_digits_or_long_names_are_not_exempt(text: str) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "auth = get_session()",
        "session = os.getcwd()",
        "cookie = jar.load_cookies()",
        "auth = Session()",
    ],
)
def test_ordinary_calls_still_pass(text: str) -> None:
    assert not Scanner().scan(text)


_NEW_WORDS_ONLY = ("session", "cookie", "auth", "creds", "credentials", "bearer", "userinfo")
_SHAPES = ('os.environ["P"]', 'os.getenv("C")', "<name>", "${NAME}", "$NAME", "load_it()")
_TAILS = (
    ' + "fakesid0123456789abcdef"',
    ' - "fakesid0123456789abcdef"',
    ' * "fakesid0123456789abcdef"',
    ' % "fakesid0123456789abcdef"',
    ' if x else "fakesid0123456789abcdef"',
    ' \\\n    or "fakesid0123456789abcdef"',
    ' and "fakesid0123456789abcdef"',
    ', "fakesid0123456789abcdef"',
    ' + 1 + "fakesid0123456789abcdef"',
)


@pytest.mark.parametrize("word", _NEW_WORDS_ONLY)
@pytest.mark.parametrize("shape", _SHAPES)
def test_placeholder_followed_by_more_code_is_not_exempt(word: str, shape: str) -> None:
    for tail in _TAILS:
        assert Scanner().scan(f"{word} = {shape}{tail}"), (word, shape, tail)


@pytest.mark.parametrize("word", _NEW_WORDS_ONLY)
@pytest.mark.parametrize("shape", _SHAPES)
def test_placeholder_with_closing_punctuation_still_passes(word: str, shape: str) -> None:
    for tail in ("", ",", ";", "  # read from the environment", " )", " }", "),", " ]"):
        assert not Scanner().scan(f"{word} = {shape}{tail}"), (word, shape, tail)
    assert not Scanner().scan(f"f({word}={shape})")
    assert not Scanner().scan(f'cfg = {{"{word}": {shape}}}')


@pytest.mark.parametrize("scheme", ["Token", "ApiKey", "api-key", "Api_Key"])
@pytest.mark.parametrize("prefix", ["a", "ab", "abc", "abcd", "abcde"])
def test_scheme_value_with_a_short_prefix_and_colon_is_blocked(scheme: str, prefix: str) -> None:
    assert Scanner().scan(f"Authorization: {scheme} {prefix}:{'x' * 24}")
    assert Scanner().scan(f'Authorization: "{scheme} {prefix}:{"z" * 24}"')


@pytest.mark.parametrize("brk", ["\n", "\r\n", "\r", "\v", "\f", "\x85", " ", " "])
@pytest.mark.parametrize("indent", [" ", "    ", "\t", " \t "])
def test_header_name_split_by_a_line_break_and_indent_is_blocked(brk: str, indent: str) -> None:
    assert Scanner().scan(f"Authori{brk}{indent}zation: Token {_FAKE}")
    assert Scanner().scan(f"Authorization:{brk}{indent}Token {_FAKE}")


@pytest.mark.parametrize("word", _NEW_WORDS_ONLY)
@pytest.mark.parametrize(
    "shape",
    [
        "<abc_xxxxxxxx00000000>",
        "<abc-xxxxxxxx00000000>",
        "<pre_1234567890>",
        "<a-b-12345678>",
        "<id_ab12cd34ef>",
        "${ABC_XXXXXXXX00000000}",
        "$ABC_XXXXXXXX00000000",
        "${PRE_1234567890}",
    ],
)
def test_placeholder_with_a_value_like_segment_is_not_exempt(word: str, shape: str) -> None:
    assert Scanner().scan(f"{word} = {shape}")


@pytest.mark.parametrize("word", _NEW_WORDS_ONLY)
@pytest.mark.parametrize(
    "shape",
    [
        "<your-session-cookie-here>",
        "${SESSION_COOKIE}",
        "<YOUR_API_KEY_2>",
        "$SESSION_COOKIE",
        "<your-token-v2>",
        "<ENV_NAME_1>",
    ],
)
def test_plain_word_placeholders_still_pass(word: str, shape: str) -> None:
    assert not Scanner().scan(f"{word} = {shape}")
