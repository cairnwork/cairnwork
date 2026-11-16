import re
import unicodedata

import pytest

from cairnwork.p2p_probe_log import mask_secrets

_WORD_OR_DASH = re.compile(r"[\w-]")


@pytest.mark.parametrize(
    "line",
    [
        "™password=SV",
        "ⓐpassword=SV",
        "℃token=SV",
        "㎏secret=SV",
        "№password=SV",
        "₨API_KEY=SV",
        "﷼token=SV",
        "my－token=SV",
        "my﹣token=SV",
    ],
)
def test_nfkc_symbol_or_dash_before_key_still_masks_1313(line):
    assert "SV" not in mask_secrets(line)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("my-token=SV", "my-token=SV"),
        ("x-password=SV", "x-password=SV"),
        ("safe=hello", "safe=hello"),
    ],
)
def test_plain_dashed_and_non_secret_names_stay_unmasked_1313(line, expected):
    assert mask_secrets(line) == expected


def test_every_nfkc_non_word_non_dash_codepoint_before_password_masks_1313():
    leaks = []
    for cp in range(0x110000):
        ch = chr(cp)
        if unicodedata.normalize("NFKC", ch) == ch or _WORD_OR_DASH.fullmatch(ch):
            continue
        if "SV" in mask_secrets(f"{ch}password=SV"):
            leaks.append((hex(cp), ch))
    assert leaks == []


def test_every_nfkc_non_word_non_dash_codepoint_in_my_token_masks_1313():
    leaks = []
    for cp in range(0x110000):
        ch = chr(cp)
        if unicodedata.normalize("NFKC", ch) == ch or _WORD_OR_DASH.fullmatch(ch):
            continue
        if "SV" in mask_secrets(f"my{ch}token=SV"):
            leaks.append((hex(cp), ch))
    assert leaks == []
