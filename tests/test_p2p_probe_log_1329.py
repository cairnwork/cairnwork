"""#1329: marks, look-alike underscores and full-width forms in the non-'=' separator shapes."""

import time

import pytest

from cairnwork.p2p_probe_log import mask_secrets

SV = "SV12345secretvalue"


@pytest.mark.parametrize(
    "line",
    [
        "API_KE͏Y=SV",  # combining grapheme joiner inside the name
        "API_KEY️=SV",  # variation selector right after it
        "API_KÉY=SV",  # combining acute on a letter
        "API_KEÝ=SV",
        "token͏: SV",  # the same marks in the colon form
        "--api͏key SV",  # and the flag-plus-space form
        "--github＿token SV",  # full-width low line in a flag name
        "--github﹍token SV",
        "API︳KEY: SV",  # presentation-form low line, colon
        "GITHUB＿TOKEN: SV",
        "client-ｓｅｃｒｅｔ=SV",  # dashed full-width name
        "client-ｓｅｃｒｅｔ: SV",
        "token： SV",  # full-width colon
        "API_KEY： SV",
        "ａｃｃｅｓｓ＿ｔｏｋｅｎ%3DSV",  # %3D
        "access_token%3dSV",
        "access_tok͏en%3DSV",
        "Authorization： Basic SV",  # header with a full-width colon
    ],
)
def test_each_shape_is_masked(line):
    out = mask_secrets(line.replace("SV", SV))
    assert SV not in out
    assert "<redacted>" in out


@pytest.mark.parametrize(
    "line",
    [
        "tokenizer: fast",
        "password_policy: strict",
        "keyboard： layout",
        "naïve résumé: café",
        "名前： テスト",
        "my-token＝ shown",
        "status: ok",
    ],
)
def test_ordinary_text_is_not_newly_masked(line):
    assert "<redacted>" not in mask_secrets(line)


def test_text_outside_a_masked_value_keeps_its_original_form():
    out = mask_secrets(f"名前 café token： {SV}\nnext línea")
    assert out.startswith("名前 café token： ") and out.endswith("\nnext línea")


@pytest.mark.parametrize("mark", ["͏", "́", "️", "＿"])
def test_scanning_stays_linear_on_large_input(mark):
    text = ("a" + mark) * 40000 + " token=" + SV  # 80k characters of dense marks
    started = time.monotonic()
    out = mask_secrets(text)
    assert time.monotonic() - started < 5
    assert SV not in out
