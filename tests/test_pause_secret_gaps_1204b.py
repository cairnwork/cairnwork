"""Follow-up recall gaps from the #1200 acceptance read (#1204): placeholder values only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from experimental.pause.client import _Client
from experimental.pause.schema import Reason
from experimental.pause.secrets_scan import Scanner

_B64 = "QUJDREVGR0g6WFlaMTIzNDU2"  # placeholder: base64 of made-up text


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Basic {_B64}",
        f"authorization = Basic {_B64}==",
        f"curl -H 'Authorization: Basic {_B64}' https://example.invalid",
        f'{{"Authorization": "Basic {_B64}"}}',
    ],
)
def test_a_basic_authorization_value_is_refused(text: str) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "Basic internationalization support is planned",
        "The basic authorization flow is described in the doc",
        "Authorization: Basic",
    ],
)
def test_ordinary_prose_about_basic_is_not_refused(text: str) -> None:
    assert not Scanner().scan(text)


class _Transport:
    def send(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a refused request must never reach the transport")


class _QuotaTransport:
    def send(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(quota_blocked=True)


def _blocked(messages: list[dict]) -> bool:
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=1)
    )
    outcome = _Client(_Transport(), Scanner())._send(request, messages, planner=True)  # type: ignore[arg-type]
    return outcome[3] == Reason.PAUSE_SECRET_BLOCKED


@pytest.mark.parametrize("key", ["password", "api_key", "token", "client_secret"])
def test_a_credential_key_with_a_separate_value_piece_is_refused(key: str) -> None:
    assert _blocked([{"role": "user", "content": [{key: "Qwertyuiop12345x"}]}])


def test_a_harmless_mapping_is_not_refused_by_the_joined_scan() -> None:
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=0)
    )
    messages = [{"role": "user", "content": "Please plan the next step"}]
    outcome = _Client(_Transport(), Scanner())._send(request, messages, planner=True)  # type: ignore[arg-type]
    assert outcome[3] != Reason.PAUSE_SECRET_BLOCKED


@pytest.mark.parametrize("lead", list("!#@$%^&*-+/~"))
@pytest.mark.parametrize("sep", ["=", ": ", " = ", "=>"])
def test_a_value_that_starts_with_punctuation_is_refused(lead: str, sep: str) -> None:
    assert Scanner().scan(f"password{sep}{lead}Abcdefgh12345")
    assert Scanner().scan(f"--password {lead}Abcdefgh12345")


@pytest.mark.parametrize(
    "text",
    [
        f'headers["Authorization"] = "Basic {_B64}"',
        f"headers['Authorization'] = 'Basic {_B64}'",
        f"'Authorization' => 'Basic {_B64}'",
        f'req.Header.Set("Authorization", "Basic {_B64}")',
        f"xhr.setRequestHeader('Authorization', 'Basic {_B64}')",
        f"('Authorization', 'Basic {_B64}')",
    ],
)
def test_basic_authorization_in_header_api_shapes_is_refused(text: str) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Basic <base64 of user:pass>",
        'req.Header.Set("Content-Type", "application/json")',
    ],
)
def test_header_api_prose_is_not_refused(text: str) -> None:
    assert not Scanner().scan(text)


@pytest.mark.parametrize(
    "item",
    [
        {"password": "hunter22"},
        {"api_key": "Abc12345"},
        {"password": {"value": "Qwertyuiop12345x"}},
        {"password": ["Qwertyuiop12345x"]},
        {"headers": {"Authorization": [f"Basic {_B64}"]}},
        ["Authorization", f"Basic {_B64}"],
    ],
)
def test_nested_and_short_credential_mappings_are_refused(item: object) -> None:
    assert _blocked([{"role": "user", "content": [item]}])


@pytest.mark.parametrize(
    "content",
    [
        "Please plan the next step",
        [{"key": "next"}],
        [{"max_tokens": "4096"}],
        [{"type": "text", "text": "ordinary words"}],
    ],
)
def test_ordinary_content_is_not_refused(content: object) -> None:
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=1)
    )
    messages = [{"role": "user", "content": content}]
    outcome = _Client(_QuotaTransport(), Scanner())._send(request, messages, planner=True)  # type: ignore[arg-type]
    assert outcome[3] != Reason.PAUSE_SECRET_BLOCKED


def test_a_cyclic_message_fails_closed_as_scanner_error() -> None:
    message: dict = {"role": "user"}
    message["content"] = message
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=1)
    )
    outcome = _Client(_Transport(), Scanner())._send(request, [message], planner=True)  # type: ignore[arg-type]
    assert outcome[3] == Reason.PAUSE_SECRET_BLOCKED
    assert outcome[4] == ("scanner_error",)


# --- review round 2 (#1204): short, nested and integer values blocked; ordinary code kept ---


@pytest.mark.parametrize(
    "text",
    [
        f'headers[ "Authorization" ] = "Basic {_B64}"',
        f"headers[ 'Authorization' ]= 'Basic {_B64}'",
        f'authorization := "Basic {_B64}"',
    ],
)
def test_basic_authorization_with_a_space_before_the_bracket_or_walrus_is_refused(
    text: str,
) -> None:
    assert Scanner().scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "Basic internationalization support is planned",
        "The basic authorization flow is described in the doc",
        "Authorization: Basic",
        "Authorization: Basic <base64 of user:pass>",
        'req.Header.Set("Content-Type", "application/json")',
    ],
)
def test_prose_about_basic_stays_unrefused_round_two(text: str) -> None:
    assert not Scanner().scan(text)


@pytest.mark.parametrize(
    "item",
    [
        {"password": "hunter2"},
        {"password": "abc123"},
        {"token": "Abc1234"},
        {"password": {"v": "hunter2"}},
        {"password": 1234567890123456},
    ],
)
def test_short_nested_and_integer_credential_values_are_refused(item: object) -> None:
    assert _blocked([{"role": "user", "content": [item]}])


def _passes(message: dict) -> bool:
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=1)
    )
    outcome = _Client(_QuotaTransport(), Scanner())._send(request, [message], planner=True)  # type: ignore[arg-type]
    return outcome[3] != Reason.PAUSE_SECRET_BLOCKED


@pytest.mark.parametrize(
    "content",
    [
        "def f():\n    token = next_token()\n    return token",
        "page = fetch(\n    token = None,\n    limit=10)",
        [{"type": "text", "text": "token = next_token()"}],
        [{"type": "text", "text": "password = get_password()"}],
        [{"key": "next"}],
        [{"max_tokens": "4096"}],
        [{"max_tokens": 4096}],
        "Please plan the next step",
        [{"type": "text", "text": "ordinary words"}],
    ],
)
def test_ordinary_code_and_words_in_a_user_message_are_not_refused(content: object) -> None:
    assert _passes({"role": "user", "content": content})


def test_ordinary_code_in_a_tool_message_is_not_refused() -> None:
    content = "def f():\n    token = next_token()\n    return token"
    assert _passes({"role": "tool", "tool_call_id": "c1", "content": content})


@pytest.mark.parametrize(
    "line",
    ["token: str", "password = input()", "self.token = fetch_token()"],
)
def test_a_list_of_code_lines_is_not_refused(line: str) -> None:
    request = SimpleNamespace(
        settings=SimpleNamespace(model_id="m", effort="low", timeout_seconds=1.0, max_attempts=0)
    )
    messages = [{"role": "user", "content": [line, "return value_of_something"]}]
    outcome = _Client(_Transport(), Scanner())._send(request, messages, planner=True)  # type: ignore[arg-type]
    assert outcome[3] != Reason.PAUSE_SECRET_BLOCKED


def test_a_header_pair_list_is_still_refused() -> None:
    pair = ["Authorization", "Basic dXNlcjpwYXNzd29yZA=="]
    assert _blocked([{"role": "user", "content": [pair]}])
