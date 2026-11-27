"""#1886: Digest/Token/ApiKey authorization schemes and auth/credentials/session/cookie key
names are refused; prose and placeholders still pass (#1239 must not come back)."""

from __future__ import annotations

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


def test_head_blocks_everything_main_blocks() -> None:
    """Differential: every input the pre-#1886 scanner (origin/main) refuses is still refused."""
    import importlib.util
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    base = subprocess.run(
        ["git", "-C", str(root), "show", "3e3097bd:experimental/pause/secrets_scan.py"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if base.returncode != 0:
        pytest.skip("base commit not available")
    spec = importlib.util.spec_from_loader("secrets_scan_main", loader=None)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    exec(compile(base.stdout, "secrets_scan_main", "exec"), mod.__dict__)
    corpus = [
        f"{n} = {v}" for o in _OLD for w in _NEWW for n in _names(o, w) for v in _PLACEHOLDERS
    ]
    corpus += [f"{o} = {v}" for o in _OLD for v in _PLACEHOLDERS]
    corpus += [f"{n}=hunter2hunter2hunter2" for o in _OLD for w in _NEWW for n in _names(o, w)]
    corpus += [f"--{n} hunter2hunter2hunter2" for o in _OLD for w in _NEWW for n in _names(o, w)]
    corpus += [f"{w}_{o}: <x1x1x1x1x>" for o in _OLD for w in _NEWW]
    assert len(corpus) >= 400
    for text in corpus:
        if mod.scan(text):
            assert Scanner().scan(text), text


@pytest.mark.parametrize(
    "text",
    [
        'Authorization: "Token" fakevalue0123456789',
        "Authorization: 'ApiKey' fakevalue0123456789",
        'Authorization: Digest nc=1, response="fakevalue0123456789"',
        "Authorization: Digest username=fakeuser, realm=x",
        "Authorization: Api_Key fakevalue0123456789",
        "Authorization: api-key fakevalue0123456789",
        "Authorization: apikey fakevalue0123456789",
        "Author\nization: Token fakevalue0123456789",
        "Authorization:\nToken fakevalue0123456789",
        'cookie = "<fakevalue0123456789fakevalue>"',
        'session = "<0123456789abcdef0123456789abcdef0123>"',
        'cookie = "${fakevalue0123456789abcd}"',
        'auth = "hunter2()"',
        'credentials = "getenv(hunter2)"',
    ],
)
def test_review_round_one_shapes_are_blocked(text: str) -> None:
    assert Scanner().scan(text)
