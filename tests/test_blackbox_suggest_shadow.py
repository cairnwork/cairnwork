"""tools/blackbox_suggest (#370): standard-library names that the tests' own files take over.

pytest puts the test directories (and the configured ``pythonpath``) at the front of
``sys.path``, so a file there named like a standard-library module is what ``import fractions``
loads, in the test file or lazily inside pytest. A star import from the standard library can
rebind a name of a class defined in the file. The reader keeps a module only when neither can
happen.
"""

import os
import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
SIDE = "from pkg import core\ncore.SCALE = 10\n"


def run(tmp_path, body="", extra=None):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + textwrap.dedent(body) + PLAIN,
        **(extra or {}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = [
    pytest.param("import fractions\n", {"tests/fractions.py": SIDE}, id="module_in_test_dir"),
    pytest.param(
        "import fractions\n", {"tests/fractions/__init__.py": SIDE}, id="package_in_test_dir"
    ),
    pytest.param("", {"tests/difflib.py": SIDE}, id="not_imported_by_the_test_file"),
    pytest.param(
        "import fractions\n",
        {"tests/a_dir/fractions.py": SIDE, "tests/a_dir/test_z.py": "def test_z():\n    pass\n"},
        id="module_beside_another_test_file",
    ),
    pytest.param("import fractions\n", {"tests/deep/er/fractions.py": SIDE}, id="module_deeper"),
    pytest.param("import fractions\n", {"scripts/fractions.py": SIDE}, id="module_in_any_dir"),
    pytest.param(
        "import fractions\n", {"tests/fractions/__init__.pyc": ""}, id="package_with_compiled_init"
    ),
    pytest.param("import fractions\n", {"tests/fractions.pyc": ""}, id="compiled_module"),
    pytest.param(
        "import fractions\n",
        {"pytest.ini": "[pytest]\nnorecursedirs = other\n", "node_modules/fractions.py": SIDE},
        id="configured_norecursedirs_replace_the_defaults",
    ),
    pytest.param("import fractions\n", {"fractions.py": SIDE}, id="module_in_project_root"),
    pytest.param("import fractions\n", {"src/fractions.py": SIDE}, id="module_in_source_root"),
    pytest.param(
        "import fractions\n",
        {
            "helpers/fractions.py": SIDE,
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["helpers"]\n',
        },
        id="module_on_configured_pythonpath",
    ),
    pytest.param(
        "import fractions\n",
        {"helpers/fractions.py": SIDE, "pytest.ini": "[pytest]\npythonpath = helpers\n"},
        id="module_on_ini_pythonpath",
    ),
    pytest.param(
        """
        class Enum:
            pass
        from enum import *
        class Color(Enum):
            def __init__(self, *a):
                core.SCALE = 10
            RED = 1
        """,
        None,
        id="stdlib_star_import_rebinds_a_same_file_base",
    ),
    pytest.param(
        """
        class Base:
            pass
        from os import *
        class K(Base):
            pass
        """,
        None,
        id="any_stdlib_star_import_before_a_same_file_base",
    ),
    pytest.param(
        """
        from unittest import TestCase
        from os import *
        class K(TestCase):
            pass
        """,
        None,
        id="stdlib_star_import_before_a_testcase_base",
    ),
]


@pytest.mark.parametrize(("body", "extra"), REFUSED)
def test_stdlib_names_taken_over_by_the_tests_refuse(tmp_path, body, extra):
    item = run(tmp_path, body, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_helpers_with_other_names_keep_the_draft(tmp_path):
    item = run(
        tmp_path,
        """
        import fractions
        class Base:
            pass
        class K(Base):
            pass
        from os import *
        """,
        {
            "tests/helpers.py": "X = 1\n",
            "tests/json/data.txt": "{}\n",
            "tests/json/notes.py": "X = 1\n",
            "src/pkg/types.py": "X = 1\n",
            "tests/sub/__init__.py": "",
            "tests/sub/types.py": "X = 1\n",
            "tests/sub/fractions.py": SIDE,
            "tests/sub/test_y.py": "",
            "venv/pyvenv.cfg": "",
            "venv/lib/fractions.py": SIDE,
            "env/pyvenv.cfg": "",
            "env/lib/fractions.py": SIDE,
            ".git/fractions.py": SIDE,
            ".hidden/fractions.py": SIDE,
            "build/fractions.py": SIDE,
            "dist/fractions.py": SIDE,
            "node_modules/fractions.py": SIDE,
            "x.egg/fractions.py": SIDE,
            "tests/__pycache__/fractions.cpython-312.pyc": "",
        },
    )
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


def test_a_file_added_after_an_earlier_read_counts(tmp_path):
    assert run(tmp_path).protected(KINDS_NOW)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/fractions.py").write_text(SIDE)
    item = run(tmp_path, "import fractions\n")
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists any directory")
def test_a_directory_that_cannot_be_listed_refuses(tmp_path):
    locked = tmp_path / "tests/locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        item = run(tmp_path)
    finally:
        locked.chmod(0o755)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_a_linked_directory_refuses(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "fractions.py").write_text(SIDE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/linked").symlink_to(outside, target_is_directory=True)
    item = run(tmp_path, "import fractions\n")
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists any directory")
def test_an_unlistable_directory_pytest_skips_keeps_the_draft(tmp_path):
    locked = tmp_path / "tests/.cache"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        item = run(tmp_path)
    finally:
        locked.chmod(0o755)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_a_directory_that_cannot_be_searched_refuses(tmp_path):
    half = tmp_path / "tests/half"
    half.mkdir(parents=True)
    (half / "x.py").write_text("")
    half.chmod(0o444)
    try:
        item = run(tmp_path)
    finally:
        half.chmod(0o755)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists any directory")
def test_an_unreadable_pythonpath_entry_refuses(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("lib")
    outside.chmod(0)
    try:
        item = run(tmp_path, extra={"pytest.ini": f"[pytest]\npythonpath = {outside}\n"})
    finally:
        outside.chmod(0o755)
    assert item.refusal is not None and not item.protected(KINDS_NOW)
