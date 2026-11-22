"""tools/blackbox_suggest (#370): ini option names keep their case for pytest.

pytest reads an ini file's option names as written, so ``Xfail_Strict`` is not ``xfail_strict``:
with ``strict_config`` (or ``strict``) pytest stops at start on the unknown name, and without it
the option has no effect. A configuration with such a name refuses.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x * 2\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"


def run(tmp_path, name, text):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": TEST,
        name: text,
    }
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "strict_config": ("pytest.ini", "[pytest]\nstrict_config = true\nXfail_Strict = false\n"),
    "strict": ("pytest.ini", "[pytest]\nstrict = true\nXFAIL_STRICT = false\n"),
    "no_strict": ("pytest.ini", "[pytest]\nXfail_Strict = false\n"),
    "tox": ("tox.ini", "[pytest]\nstrict_config = true\nAddopts = -q\n"),
    "setup_cfg": ("setup.cfg", "[tool:pytest]\nStrict_Config = true\n"),
}


@pytest.mark.parametrize("name,text", REFUSED.values(), ids=REFUSED.keys())
def test_an_ini_option_name_not_in_lower_case_refuses(tmp_path, name, text):
    item = run(tmp_path, name, text)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "lower_case": ("pytest.ini", "[pytest]\nstrict_config = true\nxfail_strict = false\n"),
    "other_section": ("setup.cfg", "[metadata]\nName = x\n[tool:pytest]\nxfail_strict = true\n"),
    "toml": ("pyproject.toml", "[tool.pytest.ini_options]\nxfail_strict = false\n"),
}


@pytest.mark.parametrize("name,text", KEPT.values(), ids=KEPT.keys())
def test_lower_case_ini_option_names_keep_the_draft(tmp_path, name, text):
    item = run(tmp_path, name, text)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
