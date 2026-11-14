"""#476 step 2, Gemini round CW1200G: recall-first secret names (any name ending in a credential
word), the closed ordinary-word list, and the pwd path exemption."""

from __future__ import annotations

import pytest

from experimental.pause.secrets_scan import scan

SECRET = {"assigned_secret", "flag_secret"}
_V = "abcdefghijklmnopqrst"
_P = "secret_value_1234567890"


def _secret_hits(text):
    return [h for h in scan(text) if h in SECRET]


@pytest.mark.parametrize(
    "text",
    [f"{n}={_V}" for n in (
        "masterkey stripekey accesskey secretkey signingkey consumerkey clientkey licensekey "
        "sessionkey adminpass rootpass dbpass masterpass sitepass userpass").split()]
    + [f"--adminpass {_V}", f"--rootpass {_V}", f"--adminpass={_V}"]
    + [f"{n}={_P}" for n in ("DB_PWD", "OLD_PWD", "db_pwd", "dbPwd")]
    + ["PWD=build/artifacts/output_dir_x", f"--pwd {_P}", f"--db-pwd={_P}"]
    + ['render_pass = "shadow_map_depth_texture"', "forwardPass = resnet_block_evaluation",
       "--compiler-pass dead_code_elimination", "--render-pass shadow_map_depth_texture"]
    + [f"db_pass = {_V}", f"smtp_pass = {_V}", f"dbPass = {_V}", f"--db-pass {_V}"]
    + [f"_secret = '{_V}'", f"__api_key__ = {_V}", f"cfg.apiKey = {_V}",
       f"monkey_key = {_V}", f"turkeypass = {_V}", f"MONKEYTOKEN={_V}"],
)  # fmt: skip
def test_names_ending_in_a_credential_word_are_blocked(text):
    assert _secret_hits(text), text


@pytest.mark.parametrize(
    "text",
    [f"{n}{sep}{_V}" for n in "monkey donkey turkey hockey jockey whiskey bypass compass overpass "
     "underpass trespass surpass MONKEY Bypass".split() for sep in ("=", ": ")]
    + [f"{n}=/opt/app/build/output/dir" for n in (
        "DB_PWD OLD_PWD CURR_PWD TARGET_PWD db_pwd old_pwd curr_pwd dbPwd oldPwd targetPwd "
        "pwd PWD").split()]
    + ["--pwd /opt/app/build/output/dir", "--db-pwd ~/work/out/dir1",
       "pwd=./build/output/directory", "DB_PWD=C:/work/build/output/dir", "--pwd=../build/out/dir"]
    + [f"passport={_V}", f"keyboard={_V}", f"tokenizer={_V}", f"secretary: {_V}",
       "the key point is that passwords should rotate every quarter"],
)  # fmt: skip
def test_ordinary_words_and_pwd_paths_are_not_blocked(text):
    assert _secret_hits(text) == [], text


def test_long_name_runs_scan_in_linear_time():
    import time

    start = time.monotonic()
    scan("a" * 200_000 + " " + "b-" * 100_000 + " " + "--" + "c" * 200_000)
    assert time.monotonic() - start < 2.0
