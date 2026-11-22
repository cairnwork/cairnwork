"""tools/blackbox_suggest (#370): configured warning filters pytest does not parse.

pytest parses each configured filter at start. A filter with too many fields, a category that is
not a warning class, a line that is not a whole number or a pattern that does not compile stops
the run, so no test runs. The reader keeps a draft only under filters pytest parses: builtin
categories, whole line numbers and patterns that compile.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, extra):
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": CORE, "tests/test_x.py": TEST, **extra}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


BAD = {
    "stdlib_name_not_defined": "ignore::warnings.Bogus",
    "stdlib_name_not_a_class": "ignore::os.sep",
    "stdlib_module_without_that_warning": "ignore::os.UserWarning",
    "stdlib_class_not_a_warning": "ignore::warnings.WarningMessage",
    "builtin_not_a_warning": "ignore::ValueError",
    "unknown_bare_name": "ignore::NoSuchWarning",
    "message_pattern_does_not_compile": "ignore:[:UserWarning",
    "module_pattern_does_not_compile": "ignore:::[",
    "too_many_fields": "ignore:a:UserWarning:m:1:extra",
    "line_not_a_number": "ignore::UserWarning:m:x",
    "line_negative": "ignore::UserWarning:m:-1",
}
GOOD = {
    "bare_action": "ignore",
    "builtin_category": "ignore::DeprecationWarning",
    "builtin_under_module": "ignore::builtins.UserWarning",
    "message_and_module_patterns": "default:some .* text:UserWarning:pkg\\..*:3",
}


@pytest.mark.parametrize("text", BAD.values(), ids=BAD.keys())
def test_a_configured_filter_pytest_does_not_parse_refuses(tmp_path, text):
    for name, body in [
        ("pytest.ini", f"[pytest]\nfilterwarnings =\n    ignore::DeprecationWarning\n    {text}\n"),
        ("pyproject.toml", f"[tool.pytest]\nfilterwarnings = [{text!r}]\n"),
    ]:
        item = run(tmp_path / name, {name: body})
        assert item.refusal is not None and not item.protected(KINDS_NOW), name


@pytest.mark.parametrize("text", GOOD.values(), ids=GOOD.keys())
def test_a_filter_pytest_parses_keeps_the_draft(tmp_path, text):
    item = run(tmp_path, {"pytest.ini": f"[pytest]\nfilterwarnings =\n    {text}\n"})
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
