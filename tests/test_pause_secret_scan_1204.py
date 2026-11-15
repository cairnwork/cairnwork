"""Regression coverage for PAUSE scanner recall and pwd path handling."""

from __future__ import annotations

from typing import Any

import pytest

from experimental.pause.secrets_scan import _is_path, scan


def _fake() -> str:
    return "fake" + "_" + "value" + "_" + "1234567890"


def _value() -> str:
    """Runtime-built 22-character fake credential used by the review probes."""
    return "v" * 22


@pytest.mark.parametrize(
    "text",
    [
        "?a=1&api_key=" + _value(),
        "?id=7&token=" + _value(),
        "a=1&b=2&client_secret=" + _value(),
        '{"id":1,"token":"' + _value() + '"}',
        '{"user":"x","password":"' + _value() + '"}',
        'headers={"Authorization":"x","api-key":"' + _value() + '"}',
        'auth={"token":"' + _value() + '"}',
        "cfg: {secret: " + _value() + "}",
        "env:\n  password: " + _value(),
        "x=a,password=" + _value(),
        "A=b;TOKEN=" + _value(),
        "k:v,pwd:" + _value(),
        "DATABASE_URL=postgres://u:pw@h/db?sslmode=1&secret=" + _value(),
        "--opt=a&--api-key=" + _value(),
        "--config=k=v,--token=" + _value(),
        "mon\u200bkey=" + _value(),
        "public\u200bKey=" + _value(),
        "x\u200bBearer " + _value(),
        "cd /root\u200bx",
        "bob\u200bx",
        "token = abcdefghijklmnop()",
        "token = abcdefghijklmnopq()",
    ],
)
def test_review_must_block_cases(text: str) -> None:
    configured_names = ("bob",) if text.startswith("bob") else ()
    assert scan(text, names=configured_names)


@pytest.mark.parametrize(
    "text",
    [
        "token = next_token()",
        "token = abcdefghijklmno()",
        "password: str",
        "publicKey=" + _value(),
        "?page=2&sort=asc",
        '{"id":1,"name":"bob"}',
        "pwd=/opt/app/V",
        'pwd="/opt/app/V"',
    ],
)
def test_review_must_pass_cases(text: str) -> None:
    assert scan(text) == []


def test_zero_width_secret_prefix_requires_normalization() -> None:
    assert scan("sk\u200b-ant-abcDEFGHIJKLMNOP")


def test_crlf_wrapped_secret_prefix_requires_join() -> None:
    assert scan("sk-ant-abc\r\nDEFGHIJKLMNOP")


def test_crlf_assignment_value_requires_join() -> None:
    assert scan("client_secret:\r\n!" + _value())


def test_exactly_six_character_quoted_phrase_is_blocked() -> None:
    assert scan('token = "a b cd"')


@pytest.mark.parametrize(
    "text",
    [
        "api\u200b_key=" + _fake(),
        "Bearer fake\n_token_1234567890",
        "client_secret:\n" + _fake(),
        "api_key: " + _fake().replace("_", "\n", 1),
    ],
)
def test_normalisation_preserves_secret_recall(text: Any) -> None:
    assert scan(text)


@pytest.mark.parametrize("text", ["token = !fake", "--api-key=!fake"])
def test_symbol_values_are_blocked(text: Any) -> None:
    assert {"assigned_secret", "flag_secret"}.intersection(scan(text))


@pytest.mark.parametrize(
    "text",
    [
        'password = "correct horse battery"',
        "token: 'phrase with spaces'",
        "--secret `short6`",
    ],
)
def test_quoted_phrases_are_blocked_at_six_characters(text: Any) -> None:
    assert {"assigned_secret", "flag_secret"}.intersection(scan(text))


@pytest.mark.parametrize(
    "name",
    [
        "publicKey",
        "public_key",
        "primaryKey",
        "sortKey",
        "partitionKey",
        "foreignKey",
        "cacheKey",
        "idempotencyKey",
        "nextToken",
        "pageToken",
        "MonKey",
        "TurKey",
    ],
)
def test_ordinary_key_and_token_names_are_exempt(name: Any) -> None:
    assert scan(f"{name}={_fake()}") == []


@pytest.mark.parametrize(
    "name",
    ["apiKey", "secretKey", "privateKey", "access_token", "client_secret", "DB_PASSWORD"],
)
def test_credential_names_remain_blocked(name: Any) -> None:
    assert scan(f"{name}={_fake()}")


@pytest.mark.parametrize("name", ["csrf_token", "csrfToken"])
def test_csrf_names_are_not_exempt(name: str) -> None:
    assert scan(f"{name}={_fake()}")


def test_pwd_absolute_path_tradeoff_and_path_class() -> None:
    assert _is_path("/opt/app/" + _fake())
    assert scan("pwd=/opt/app/" + _fake()) == []
    assert scan("pwd=" + _fake())


def test_a_long_call_value_stays_blocked_as_before() -> None:
    assert scan("token = " + "get_the_secret_value_now" + "()") == ["assigned_secret"]
    assert scan("token = next_token()") == []
