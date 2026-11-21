"""tools/blackbox_suggest (#370): standard-library names that the tests' own files take over.

pytest puts the test directories (and the configured ``pythonpath``) at the front of
``sys.path``, so a file there named like a standard-library module is what ``import fractions``
loads, in the test file or lazily inside pytest. A star import from the standard library can
rebind a name of a class defined in the file. The reader keeps a module only when neither can
happen.
"""

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
        {"tests/helpers.py": "X = 1\n", "tests/json/data.txt": "{}\n"},
    )
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
