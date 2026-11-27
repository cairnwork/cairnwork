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
