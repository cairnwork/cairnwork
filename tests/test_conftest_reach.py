"""#924: a test-commit conftest may not import a file the test commit adds or changes, directly or
through unchanged files in the test commit's top directories (conftest_reach.py). The whole-pipeline
cases are in conftest_scenarios.py; these read the resolution rules alone."""

import ast

import pytest

import cairnwork.conftest_reach as reach
from cairnwork.conftest_reach import reached_change

CONFTEST = "tests/sub/conftest.py"


def _reach(conftest_text, tree, changed, conftest=CONFTEST):
    parsed = {path: ast.parse(text) for path, text in tree.items()}
    files = frozenset(tree) | {conftest}
    return reached_change(
        conftest, ast.parse(conftest_text), frozenset(changed), files, parsed.__getitem__
    )


@pytest.mark.parametrize(
    ("text", "tree", "hit"),
    [
        ("import colorsys\n", {"tests/sub/colorsys.py": ""}, "tests/sub/colorsys.py"),
        ("import colorsys\n", {"tests/colorsys.py": ""}, "tests/colorsys.py"),
        ("from tests import helper\n", {"tests/helper.py": ""}, "tests/helper.py"),
        ("from tests.pkg import x\n", {"tests/pkg/__init__.py": ""}, "tests/pkg/__init__.py"),
        ("import tests.pkg.mod\n", {"tests/pkg/mod.py": ""}, "tests/pkg/mod.py"),
        ("from . import helper\n", {"tests/sub/helper.py": ""}, "tests/sub/helper.py"),
        ("from .. import helper\n", {"tests/helper.py": ""}, "tests/helper.py"),
        ("from .pkg import x\n", {"tests/sub/pkg/x.py": ""}, "tests/sub/pkg/x.py"),
        ("from . import x\n", {"tests/sub/__init__.py": ""}, "tests/sub/__init__.py"),
        (
            "import importlib\nimportlib.import_module('colorsys')\n",
            {"tests/colorsys.py": ""},
            "tests/colorsys.py",
        ),
        ("__import__('colorsys')\n", {"tests/colorsys.py": ""}, "tests/colorsys.py"),
        (
            "import pytest\n\n\n@pytest.fixture\ndef f():\n    import colorsys\n",
            {"tests/colorsys.py": ""},
            "tests/colorsys.py",
        ),  # inside a fixture body
        ("import util\n", {"src/util.py": ""}, ""),  # not changed by the test commit
        # r2 (cw#948 cross-model review): plugin names, fromlist, a relative import_module name,
        # and the package pytest may import the conftest in
        ("pytest_plugins = ['tests.helper']\n", {"tests/helper.py": ""}, "tests/helper.py"),
        ("pytest_plugins = 'helper'\n", {"tests/sub/helper.py": ""}, "tests/sub/helper.py"),
        ("__import__('tests', fromlist=['helper'])\n", {"tests/helper.py": ""}, "tests/helper.py"),
        (
            "import importlib\nimportlib.import_module('..helper', package=__package__)\n",
            {"tests/helper.py": ""},
            "tests/helper.py",
        ),
        ("import pytest\n", {"tests/sub/__init__.py": ""}, "tests/sub/__init__.py"),
        ("import pytest\n", {"tests/__init__.py": ""}, "tests/__init__.py"),
        # r6 (cw#948 Claude review): a native extension or bytecode file is a module too
        ("import colorsys\n", {"tests/colorsys.cpython-314-darwin.so": ""},
         "tests/colorsys.cpython-314-darwin.so"),
        ("import colorsys\n", {"tests/colorsys.pyd": ""}, "tests/colorsys.pyd"),
        ("import colorsys\n", {"tests/colorsys.pyc": ""}, "tests/colorsys.pyc"),
        ("import colorsys\n", {"tests/colorsys.txt": ""}, ""),  # data, not a module
    ],
)  # fmt: skip
def test_a_direct_import_of_a_changed_file_is_found(text, tree, hit):
    changed = [path for path in tree if path.startswith("tests/")]
    assert _reach(text, tree, changed) == hit


def test_an_unchanged_module_in_the_test_directories_is_followed():
    tree = {"tests/util.py": "from tests import helper\n", "tests/helper.py": ""}
    assert _reach("import tests.util\n", tree, ["tests/helper.py"]) == "tests/helper.py"
    chain = {"tests/a.py": "import b\n", "tests/b.py": "import c\n", "tests/c.py": ""}
    assert _reach("import a\n", chain, ["tests/c.py"], conftest="tests/conftest.py") == "tests/c.py"


def test_a_file_outside_the_test_commit_s_top_directories_is_not_followed():
    """The documented residual: an unchanged src module that imports a test-commit file."""
    tree = {"src/util.py": "from tests import helper\n", "tests/helper.py": ""}
    assert _reach("import util\n", tree, ["tests/helper.py"]) == ""


def test_an_unrelated_change_and_an_unresolved_name_are_not_a_hit():
    tree = {"tests/helper.py": "", "tests/other.py": ""}
    assert _reach("import pytest\nimport os\n", tree, ["tests/helper.py"]) == ""
    assert (
        _reach("import importlib\nimportlib.import_module(name)\n", tree, ["tests/helper.py"]) == ""
    )


def test_too_many_modules_to_follow_is_a_reason_not_a_pass(monkeypatch):
    monkeypatch.setattr(reach, "MAX_FILES", 3)
    tree = {f"tests/m{i}.py": f"import m{i + 1}\n" for i in range(6)}
    tree["tests/changed.py"] = ""
    found = _reach("import m0\n", tree, ["tests/changed.py"], conftest="tests/conftest.py")
    assert found == "has more than 3 modules to follow"


def test_a_cycle_ends():
    tree = {"tests/a.py": "import b\n", "tests/b.py": "import a\n", "tests/changed.py": ""}
    assert _reach("import a\n", tree, ["tests/changed.py"], conftest="tests/conftest.py") == ""


def test_an_unchanged_package_init_above_the_conftest_is_followed():
    tree = {"tests/__init__.py": "from tests import helper\n", "tests/helper.py": ""}
    assert _reach("import pytest\n", tree, ["tests/helper.py"]) == "tests/helper.py"


def test_an_unchanged_module_under_any_directory_is_followed_by_name():
    """r3/r5: a base pythonpath entry or a conftest's own sys.path entry puts a directory first."""
    tree = {"tests/support/helper.py": "import data\n", "tests/support/data.py": ""}
    found = _reach("import helper\n", tree, ["tests/support/data.py"], conftest="tests/conftest.py")
    assert found == "tests/support/data.py"


def test_a_package_name_alone_does_not_match_a_changed_test_file():
    """r5: importing ``tests.utils`` loads ``tests/__init__.py`` and ``tests/utils.py`` only."""
    tree = {"tests/__init__.py": "", "tests/utils.py": "", "tests/test_foo.py": ""}
    found = _reach("from tests.utils import x\n", tree, ["tests/test_foo.py"], "tests/conftest.py")
    assert found == ""


@pytest.mark.parametrize(
    ("text", "tree", "changed"),
    [  # #924 r4: matched by name, whatever directory the conftest put on sys.path
        ("import helper\n", {"tests/lib/helper.py": ""}, "tests/lib/helper.py"),
        (
            "import pkg.mod\n",
            {"tests/lib/pkg/__init__.py": "", "tests/lib/pkg/mod.py": ""},
            "tests/lib/pkg/mod.py",
        ),
        ("import pkg.mod\n", {"tests/lib/pkg/mod.py": ""}, "tests/lib/pkg/mod.py"),  # namespace
        (  # importing pkg.mod runs pkg/__init__.py first
            "import pkg.mod\n",
            {"tests/lib/pkg/__init__.py": "", "tests/lib/pkg/mod.py": ""},
            "tests/lib/pkg/__init__.py",
        ),
    ],
)
def test_an_import_named_like_a_changed_module_is_a_hit(text, tree, changed):
    assert _reach(text, tree, [changed], conftest="tests/conftest.py") == changed


def test_the_cap_is_exact(monkeypatch):
    """#924 r4: MAX_FILES unchanged modules are followed; one more refuses."""
    monkeypatch.setattr(reach, "MAX_FILES", 3)
    chain = {f"tests/m{i}.py": f"import m{i + 1}\n" for i in range(3)}
    chain["tests/m3.py"] = ""
    assert _reach("import m0\n", {**chain, "tests/m3.py": ""}, ["tests/m3.py"]) == "tests/m3.py"
    longer = {f"tests/m{i}.py": f"import m{i + 1}\n" for i in range(4)}
    longer["tests/m4.py"] = ""
    found = _reach("import m0\n", longer, ["tests/m4.py"], conftest="tests/conftest.py")
    assert found == "has more than 3 modules to follow"
