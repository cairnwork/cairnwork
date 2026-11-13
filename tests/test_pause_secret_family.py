"""#476 step 2: the common ways of writing a secret key are blocked; ordinary text is not."""

from __future__ import annotations

import pytest

from experimental.pause import schema
from experimental.pause.client import HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, PlanRequest


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
    ],
)  # fmt: skip
def test_ordinary_text_family_is_not_blocked(text):
    fake = FakeTransport(TransportResult(200, {"text": "plan"}))
    assert HaikuPlanner(fake).plan(plan_request(({"content": text},))).text == "plan", text
