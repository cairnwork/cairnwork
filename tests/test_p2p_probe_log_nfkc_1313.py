import re
import time
import unicodedata

import pytest

from cairnwork.p2p_probe_log import mask_secrets, probe_log_tail

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
        "₨API＿KEY=SV",
        "™API＿KEY=SV",
        "ⓐAPI＿KEY=SV",
        "℃PRIVATE＿KEY=SV",
        "－API＿KEY=SV",
        "﹣SECRET＿KEY=SV",
        "x™SECRET＿ACCESS＿KEY=SV",
        "x－－api＿key=SV",
        "™API︳KEY=SV",
        "－API︳KEY=SV",
        "my﹣PRIVATE﹏KEY=SV",
        "™password＝SV",
        "₨token﹦SV",
        "ⓐAPI_KEY⁼SV",
        "℃password₌SV",
        "™token⩵SV",
        "－API_KEY⩶SV",
        "™ＡＰＩ＿ＫＥＹ＝SV",
    ],
)
def test_nfkc_symbol_or_dash_before_key_still_masks_1313(line):
    assert "SV" not in mask_secrets(line)


@pytest.mark.parametrize(
    "line",
    [
        "my-token=SV",
        "x-password=SV",
        "safe=hello",
        "x-api＿key=SV",
        "x-api︳key=SV",
        "my-api﹏key=SV",
        "-API︳KEY=SV",
        "x-API＿KEY=SV",
        "my-token=SV",
        "my-token＝SV",
        "deploy-github_token＝SV",
        "foo︳bar=SV",
        "token︳=SV",
        "ªpassword=SV",
        "x™API＿KEYS=SV",
    ],
)
def test_must_stay_shown_1313(line):
    assert mask_secrets(line) == line


def test_every_nfkc_non_word_non_dash_codepoint_before_keys_and_seps_masks_1313():
    nfkc_chars = [
        chr(cp)
        for cp in range(0x110000)
        if unicodedata.normalize("NFKC", chr(cp)) != chr(cp)
        and not _WORD_OR_DASH.fullmatch(chr(cp))
    ]
    names = ("password", "API＿KEY", "API︳KEY", "PRIVATE﹏KEY")
    seps = ("=", "＝", "﹦", "⁼", "₌", "⩵")
    leaks = []
    for name in names:
        for sep in seps:
            for ch in nfkc_chars:
                if "SV" in mask_secrets(f"{ch}{name}{sep}SV"):
                    leaks.append((name, sep, hex(ord(ch)), ch))
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


def test_probe_log_tail_linear_cost_on_64k_nfkc_and_lowline_input_1313(tmp_path):
    log = tmp_path / "probe.log"
    line = ("a＿" * 16 + "password＝SV ").encode("utf-8") * 20 + b"\n"
    log.write_bytes(line * (65536 // len(line) + 1))
    t0 = time.monotonic()
    tail = probe_log_tail(log)
    elapsed = time.monotonic() - t0
    assert "SV" not in tail
    assert "<redacted>" in tail
    assert elapsed < 2.0
