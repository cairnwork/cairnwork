"""Regression coverage for PAUSE scanner recall and pwd path handling."""

from __future__ import annotations

from typing import Any

import pytest

from experimental.pause.secrets_scan import _is_path, scan


def _fake() -> str:
    return "fake" + "_" + "value" + "_" + "1234567890"


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
        "csrfToken",
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


def test_pwd_absolute_path_tradeoff_and_path_class() -> None:
    assert _is_path("/opt/app/" + _fake())
    assert scan("pwd=/opt/app/" + _fake()) == []
    assert scan("pwd=" + _fake())


def test_a_long_call_value_stays_blocked_as_before() -> None:
    assert scan("token = " + "get_the_secret_value_now" + "()") == ["assigned_secret"]
    assert scan("token = next_token()") == []
