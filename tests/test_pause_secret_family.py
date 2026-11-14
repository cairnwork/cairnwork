"""#476 step 2: the common ways of writing a secret key are blocked; ordinary text is not."""

from __future__ import annotations

import pytest

from experimental.pause import schema
from experimental.pause.client import HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, PlanRequest
from experimental.pause.secrets_scan import Scanner


class FakeTransport:
    def __init__(self, *results: TransportResult):
        self.results = list(results)
        self.calls = []

    def send(self, model, effort, messages, timeout):
        self.calls.append((model, effort, messages, timeout))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


SETTINGS = CallSettings("haiku-test", "high", 3.0, 3)


def plan_request(messages=({"role": "user", "content": "task"},), settings=SETTINGS):
    return PlanRequest("t", "c", messages, settings)


_VALUE = "Zx9Qm4Rt7Ls2Vb8Nc3Wd"
_NAMES = [
    "api_key", "apiKey", "api-key", "API_KEY", "APIKEY", "apikey",
    "access_token", "accessToken", "access-token", "ACCESS_TOKEN",
    "client_secret", "clientSecret", "client-secret", "CLIENT_SECRET",
    "secret_access_key", "secretAccessKey", "SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY",
    "auth_token", "authToken", "AUTH_TOKEN", "GITHUB_TOKEN",
    "private_key", "privateKey", "PRIVATE_KEY",
    "password", "Password", "passwd", "DB_PASSWORD", "dbPassword",
    "token", "secret", "key", "Token", "Secret",
]  # fmt: skip
_FORMS = [
    "{n}={v}", "{n} = {v}", "{n}: {v}", "{n}:{v}", '{n}: "{v}"', "{n}: '{v}'",
    '"{n}": "{v}"', "'{n}': '{v}'", "{n} => {v}", '{n} => "{v}"', "export {n}={v}",
    "--{n}={v}", "--{n} {v}",
]  # fmt: skip


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("form", _FORMS)
def test_common_key_writing_family_is_blocked(name, form):
    text = "run it with " + form.format(n=name, v=_VALUE) + " now"
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(({"content": text},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED, text


@pytest.mark.parametrize(
    "text",
    [
        "monkey = " + _VALUE, "monkey: " + _VALUE, "turkey: " + _VALUE, "MONKEY=" + _VALUE,
        "keyboard = " + _VALUE, "tokenizer: " + _VALUE, "secretary = " + _VALUE,
        "passwordless: " + _VALUE, "token = next_token()", "password: str", "key = value",
        "the key is in the lock", "set the token field and the secret field",
        "donkey = " + _VALUE, "hockey: " + _VALUE, "bypass=" + _VALUE, "compass: " + _VALUE,
        "passport=" + _VALUE, "overpass=" + _VALUE, "PWD=/opt/app/build/output/dir",
        "cd $PWD", "maxTokens: 4096", "tokens: " + _VALUE,
        "passwords are rotated every ninety days", "hockeystick = " + _VALUE,
    ],
)  # fmt: skip
def test_ordinary_text_family_is_not_blocked(text):
    fake = FakeTransport(TransportResult(200, {"text": "plan"}))
    assert HaikuPlanner(fake).plan(plan_request(({"content": text},))).text == "plan", text


# abbreviation + keyword family: myAPIKey=, clientAPIKey=, xAPIKEY=, myapitoken=, ...
_ABBRS = [
    "api", "aws", "ssh", "jwt", "oauth", "auth", "access", "client", "private", "secret", "db",
]  # fmt: skip
_WORDS = ["key", "token", "secret", "password"]
_PREFIXES = ["", "my", "client", "x", "user"]


def _abbr_names():
    for prefix in _PREFIXES:
        for abbr in _ABBRS:
            for word in _WORDS:
                camel = prefix + abbr.upper() + word.capitalize()
                yield camel  # myAPIKey
                yield prefix + abbr.upper() + word.upper()  # xAPIKEY
                yield prefix + abbr + word  # myapikey
                yield prefix + abbr.capitalize() + word.capitalize()  # myApiKey


@pytest.mark.parametrize("name", sorted(set(_abbr_names())))
@pytest.mark.parametrize("form", ["{n}={v}", '{n}: "{v}"', "--{n} {v}", "export {n}={v}"])
def test_abbreviation_camel_family_is_blocked(name, form):
    text = "run " + form.format(n=name, v=_VALUE)
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(({"content": text},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED, text


class _RaisingScanner(Scanner):
    def scan(self, text: str) -> list[str]:
        raise RuntimeError("boom " + text)


@pytest.mark.parametrize("cls", ["planner", "gate"])
def test_scan_failure_closes_without_sending_or_leaking(cls):
    from experimental.pause.client import HaikuGate

    fake = FakeTransport(TransportResult(200, {"text": "ACCEPT"}))
    secret_text = "token=" + _VALUE
    if cls == "planner":
        reply = HaikuPlanner(fake, _RaisingScanner()).plan(
            plan_request(({"content": secret_text},))
        )
    else:
        from experimental.pause.interfaces import GateRequest

        reply = HaikuGate(fake, _RaisingScanner()).review(
            GateRequest("t", "c", secret_text, SETTINGS)
        )
    assert fake.calls == []
    assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert _VALUE not in repr(reply)


_SHAPES = ["{n}={v}", "export {n}='{v}'", '"{n}": "{v}"', "--{n} {v}"]


def _blocked(names, shapes=_SHAPES):
    return [(n, f) for n in names for f in shapes]


def _assert_blocked(name, shape):
    from experimental.pause.secrets_scan import scan

    text = "run " + shape.format(n=name, v=_VALUE)
    assert scan(text), text


@pytest.mark.parametrize(
    ("name", "shape"),
    _blocked(
        "PGPASSWORD pgpassword GITHUBTOKEN githubtoken SESSIONTOKEN sessiontoken NPMTOKEN "
        "HFTOKEN SLACKTOKEN BOTTOKEN APPSECRET appsecret REDISPASSWORD mysqlpassword "
        "ADMINPASSWD".split()
    ),
)
def test_flat_glued_secret_names_are_blocked(name, shape):
    _assert_blocked(name, shape)


@pytest.mark.parametrize(
    ("name", "shape"),
    _blocked("sessionToken refreshToken botToken stripeKey appSecret smtpPassword".split()),
)
def test_camel_names_without_listed_prefix_are_blocked(name, shape):
    _assert_blocked(name, shape)


@pytest.mark.parametrize(
    ("name", "shape"),
    _blocked(
        "DB_PASS db_pass dbPass SMTP_PASS pass MYSQL_PWD DB_PWD PASSPHRASE KEY_PASSPHRASE "
        "passphrase".split()
    ),
)
def test_password_abbreviation_names_are_blocked(name, shape):
    _assert_blocked(name, shape)


@pytest.mark.parametrize(
    "text",
    [
        "api_key: `" + _VALUE + "`", "apiKey = `" + _VALUE + "`",
        "TOKEN := " + _VALUE, "password := " + _VALUE,
    ],
)  # fmt: skip
def test_backtick_quotes_and_walrus_assignment_are_blocked(text):
    from experimental.pause.secrets_scan import scan

    assert scan(text), text


@pytest.mark.parametrize(("name", "shape"), _blocked("pwd PWD DBPWD dbpwd mysqlpwd Pwd".split()))
def test_pwd_names_with_a_non_path_value_are_blocked(name, shape):
    _assert_blocked(name, shape)


@pytest.mark.parametrize(
    "text",
    [
        "PWD=/opt/app/build/output/dir", "pwd: /home/build/output/directory",
        "DBPWD=~/work/output/dir1",
    ],
)  # fmt: skip
def test_pwd_with_a_path_value_is_not_blocked(text):
    from experimental.pause.secrets_scan import scan

    assert [h for h in scan(text) if h != "host_path"] == [], text
