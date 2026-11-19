"""#1362: look-alike forms (NFKC) of names and separators are scanned like the ASCII ones."""

import pytest

from experimental.pause.secrets_scan import scan

VALUE = "abcdEFGH12345678abcd"


@pytest.mark.parametrize(
    "text, category",
    [
        (f"API_KEY＝{VALUE}", "assigned_secret"),  # full-width =
        (f"token：{VALUE}", "assigned_secret"),  # full-width :
        (f"ＡＰＩ＿ＫＥＹ={VALUE}", "assigned_secret"),  # full-width name
        (f"－－api-key {VALUE}", "flag_secret"),  # full-width --
        (f"API_KEY＝\n{VALUE}", "assigned_secret"),  # look-alike separator, then wrapped
        (f"ｓｋ-ant-{VALUE}", "api_key"),  # full-width prefix of an API key
        ("／users／alice", "host_path"),  # full-width slashes
    ],
)
def test_lookalike_forms_are_flagged(text, category):
    assert category in scan(text)


def test_account_name_with_fullwidth_letters_is_flagged():
    assert "account_name" in scan("ａｌｉｃｅ logged in", ("alice",))


@pytest.mark.parametrize(
    "text", ["token = next_token()", "publicKey: str", "ＡＢＣ means abc", "a＝b"]
)
def test_ordinary_text_is_still_clean(text):
    assert scan(text) == []
