"""#1204/#740: invisible characters are dropped by Unicode category in the PAUSE secret scan, not by
a hand-listed set, and a value that NFKC shortens is still judged as written (#1362 follow-up)."""

import pytest

from experimental.pause.secrets_scan import scan

KEY = "abcdEFGH12345678abcd"  # 20 characters, above the 16-character floor
INVISIBLE = [
    "‎", "‏", "‮", "⁦", "؜", "͏", "️", "\U000e0041",
    "ㅤ", "ᅟ", "ﾠ", "⠀", "\x00", "́", "᠋",
]  # fmt: skip


@pytest.mark.parametrize("c", INVISIBLE)
@pytest.mark.parametrize(
    ("shape", "category"),
    [
        ("pass{c}word={k}", "assigned_secret"),
        ("API_{c}KEY: {k}", "assigned_secret"),
        ("--tok{c}en {k}", "flag_secret"),
        ("--api-key{c}={k}", "flag_secret"),
        ("sk-an{c}t-{k}", "api_key"),
        ("Bear{c}er {k}", "bearer_token"),
    ],
)
def test_an_invisible_character_does_not_hide_a_secret(c, shape, category):
    assert category in scan(shape.format(c=c, k=KEY))


@pytest.mark.parametrize("c", INVISIBLE)
def test_an_invisible_character_does_not_hide_a_configured_name(c):
    assert "account_name" in scan(f"owner is ali{c}ce here", names=("alice",))


@pytest.mark.parametrize("c", ["​", "‎", "ㅤ"])
def test_a_value_that_nfkc_shortens_below_the_floor_is_still_flagged(c):
    """Full-width digits fold to a short ASCII value; the value as written still counts."""
    assert "assigned_secret" in scan(f"pass{c}word=ｈｕｎｔｅｒ２２")


@pytest.mark.parametrize("text", ["the note says nothing", "x = 1", "café résumé naïve", "áb"])
def test_ordinary_text_is_not_newly_flagged(text):
    assert scan(text) == []


@pytest.mark.parametrize("text", ["ｔｏｋｅｎ=ㅤ", "token=́", "--token ㅤ"])
def test_a_value_made_only_of_an_invisible_character_is_still_flagged(text):
    """What the earlier view flagged stays flagged (a value must not vanish with the dropping)."""
    assert scan(text)
