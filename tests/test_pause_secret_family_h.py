"""#476 step 2, Claude round CW1200H: subscript and glued-name secret families."""

import pytest

from experimental.pause.secrets_scan import scan

_VALUE = "Zx9Qm4Rt7Ls2Vb8Nc3Wd"
_SECRET = {"assigned_secret", "flag_secret"}


@pytest.mark.parametrize(
    "text",
    [
        'config["api_key"] = "' + _VALUE + '"',
        "config['token'] = '" + _VALUE + "'",
        'os.environ["TOKEN"]="' + _VALUE + '"',
        "self.headers['X-Api-Key'] = '" + _VALUE + "'",
        'cfg["db"]["password"] = "' + _VALUE + '"',
        'settings[ "SECRET_KEY" ] = "' + _VALUE + '"',
        'env["GITHUB_TOKEN"]=\'' + _VALUE + "'",
    ],
)
def test_subscript_assignment_names_are_blocked(text):
    assert _SECRET.intersection(scan(text)), text


_FORMS = (
    "{n}={v}",
    "{n}: {v}",
    '"{n}": "{v}"',
    "--{n} {v}",
    "--{n}={v}",
    "{n} = {v}",
    "{n}=>{v}",
    "{n}:={v}",
)
_QUALIFIERS = ("value", "hash", "old", "new", "prod", "dev", "test", "base", "pem", "v2")
_STEMS = (
    "token",
    "password",
    "secret",
    "apikey",
    "dbpassword",
    "accesstoken",
    "secretkey",
    "privatekey",
    "authtoken",
)


@pytest.mark.parametrize("case", ("lower", "upper"))
@pytest.mark.parametrize(
    "stem,qualifier,shape",
    [(s, q, f) for s in _STEMS for q in _QUALIFIERS for f in _FORMS],
)
def test_glued_tail_qualifier_names_are_blocked(stem, qualifier, shape, case):
    name = stem + qualifier
    if case == "upper":
        name = name.upper()
    text = shape.format(n=name, v=_VALUE)
    assert _SECRET.intersection(scan(text)), text


_DIGIT_NAMES = ("apikey2", "APIKEY2", "githubtoken3", "PGPASSWORD1", "masterkey2", "mastertoken2")


@pytest.mark.parametrize("name,shape", [(n, f) for n in _DIGIT_NAMES for f in _FORMS])
def test_glued_names_with_trailing_digits_are_blocked(name, shape):
    text = shape.format(n=name, v=_VALUE)
    assert _SECRET.intersection(scan(text)), text


@pytest.mark.parametrize(
    "text",
    [
        'data["monkey"] = "' + _VALUE + '"',
        'items["keyboard"] = "' + _VALUE + '"',
        'config["tokenizer"] = "' + _VALUE + '"',
        'x["tokens_per_second"] = 4096',
        "keyword=" + _VALUE,
        "secretary=" + _VALUE,
        "tokenizer=" + _VALUE,
        "passwordless: " + _VALUE,
        "monkeys=" + _VALUE,
        "pwd=/opt/app/build/output/dir",
        "PWD=/opt/app/build/output/dir",
        "the key point is that passwords should rotate every quarter",
        "monkeyValue=" + _VALUE,
        "keyboardLayout=" + _VALUE,
        "tokenizerVersion=" + _VALUE,
        "passwordlessMode=" + _VALUE,
        "apiKeypad=" + _VALUE,
        "secretaryName=" + _VALUE,
    ],
)
def test_ordinary_text_still_passes(text):
    assert _SECRET.isdisjoint(scan(text)), text
