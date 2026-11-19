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


@pytest.mark.parametrize(
    "line",
    [
        "access_tok\u034fen%3DSV12345\uff06tail-secret",  # a full-width & is no '&' in the value
        "token\u034f: SV12345\u2002tail-secret",  # the colon form runs to the end of the line
    ],
)
def test_the_value_is_read_from_the_original_not_the_folded_text(line):
    out = mask_secrets(line)
    assert "SV12345" not in out
    assert "tail-secret" not in out


@pytest.mark.parametrize("space", ["\u00a0", "\u3000", "\u2002"])
def test_a_space_like_character_ends_a_word_value_as_a_space_does(space):
    out = mask_secrets(f"--api\u034fkey SV12345{space}next Bearer SV67890{space}more")
    assert "SV12345" not in out and "SV67890" not in out


def test_original_form_is_still_masked_when_the_view_would_not_match():
    # a name that only matches as written, next to marks elsewhere in the line
    out = mask_secrets(f"café token: {SV}")
    assert SV not in out


@pytest.mark.parametrize(
    "line",
    [
        "token: ́",
        "token=́",
        "--token ́",
        "Bearer ́",
        "access_token%3D́",
        "Authorization: ́",
    ],
)
def test_a_value_that_is_only_a_combining_mark_is_still_masked(line):
    """The as-written pass masks what it always did; the view (which drops marks) adds to it."""
    assert "<redacted>" in mask_secrets(line)


@pytest.mark.parametrize(
    "line",
    [
        "\uff22\uff45\uff41\uff52\uff45\uff52 token: SV",  # full-width Bearer, then a name
        "\uff22\uff25\uff21\uff32\uff25\uff32 password: SV",
        "Bearer token: SV",
        "bearer\u3000secret: SV",
        "Bearer\u00b4token: SV",
        "Bearer\u00a8--token SV",
        "x Bearer\u02dcapi_key: SV",
    ],
)
def test_a_folded_bearer_or_flag_does_not_eat_a_later_colon_form_name(line):
    """Every form is first masked as written, so a value the view finds cannot swallow the name
    of the next field before its own turn (the review's counterexamples, all masked on main)."""
    assert "SV" not in mask_secrets(line.replace("SV", SV))


@pytest.mark.parametrize(
    "line", ["注意：password 欄位必填（見文件）。", "第一步：安裝；第二步：執行", "token數量：12"]
)
def test_prose_with_full_width_colons_is_not_newly_masked(line):
    assert "<redacted>" not in mask_secrets(line)


FILLERS = ["ㅤ", "ᅟ", "ᅠ", "ﾠ", "⠀", "᠋", "឴", "‎", "\U000e0100"]


@pytest.mark.parametrize("fill", FILLERS)
@pytest.mark.parametrize(
    "shape",
    [
        "tok{f}en={v}",
        "--tok{f}en {v}",
        "--tok{f}en={v}",
        '{{"tok{f}en": "{v}"}}',
        "Bear{f}er {v}",
        "token{f}: {v}",
        "Authorization:{f} {v}",
    ],
)
def test_invisible_filler_characters_do_not_hide_a_secret_name(fill, shape):
    """Not only the listed marks: any filler (Hangul fillers, Braille blank, Default_Ignorable
    Mn/Lo/So) inside or after a name is skipped, chosen by category as textclean does (#1329)."""
    assert SV not in mask_secrets(shape.format(f=fill, v=SV))


@pytest.mark.parametrize("fill", FILLERS)
def test_a_filler_between_words_in_prose_is_not_masked(fill):
    assert "<redacted>" not in mask_secrets(f"the{fill} token count is 12")
