"""tools/blackbox_suggest (#370): pytest options that collect doctests.

``--doctest-modules`` imports every Python module under the collected directories and
``--doctest-glob`` collects matching text files; both add items the reader does not count and
run code it does not read (a module's top level, a doctest that rebinds ``pkg.core.SCALE``). The
reader keeps a test only when neither is set.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"


def run(tmp_path, extra):
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": CORE, "tests/test_x.py": TEST, **extra}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "modules": {"pytest.ini": "[pytest]\naddopts = --doctest-modules\n"},
    "modules_after_a_safe_flag": {"pytest.ini": "[pytest]\naddopts = -q --doctest-modules\n"},
    "glob_with_equals": {"pytest.ini": "[pytest]\naddopts = --doctest-glob=*.rst\n"},
    "glob_with_a_space": {"pytest.ini": "[pytest]\naddopts = --doctest-glob *.rst\n"},
    "modules_in_pyproject": {
        "pyproject.toml": '[tool.pytest.ini_options]\naddopts = ["-q", "--doctest-modules"]\n'
    },
    "glob_in_setup_cfg": {"setup.cfg": "[tool:pytest]\naddopts = --doctest-glob=*.md\n"},
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_options_that_collect_doctests_refuse(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_doctest_settings_without_collection_keep_the_draft(tmp_path):
    ini = "[pytest]\naddopts = -q --doctest-report udiff\ndoctest_optionflags = ELLIPSIS\n"
    item = run(tmp_path, {"pytest.ini": ini})
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


DOCTEST = ">>> import pkg.core\n>>> pkg.core.SCALE = 10\n"
NATIVE = '[tool.pytest]\nminversion = "9.0"\n'
DEFAULT_REFUSED = {
    "text_file_beside_the_tests": {"tests/test_notes.txt": DOCTEST},
    "text_file_at_the_root": {"test_readme.txt": DOCTEST},
    "text_file_in_another_directory": {"docs/testing.txt": DOCTEST},
    "text_file_in_upper_case": {"tests/TEST_NOTES.TXT": DOCTEST},
    "native_table_beside_ini_options": {
        "pyproject.toml": NATIVE + '[tool.pytest.ini_options]\nminversion = "8.0"\n'
    },
    "ini_options_not_a_table": {"pyproject.toml": '[tool.pytest]\nini_options = "x"\n'},
}


@pytest.mark.parametrize("extra", DEFAULT_REFUSED.values(), ids=DEFAULT_REFUSED.keys())
def test_doctests_pytest_collects_by_default_refuse(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


DEFAULT_KEPT = {
    "other_text_file": {"tests/notes.txt": DOCTEST, "docs/readme.txt": DOCTEST},
    "rst_file": {"tests/test_notes.rst": DOCTEST},
    "text_file_in_a_directory_not_collected": {"build/test_notes.txt": DOCTEST},
    "native_table_beside_empty_ini_options": {
        "pyproject.toml": NATIVE + "[tool.pytest.ini_options]\n"
    },
}


@pytest.mark.parametrize("extra", DEFAULT_KEPT.values(), ids=DEFAULT_KEPT.keys())
def test_files_pytest_does_not_collect_keep_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
