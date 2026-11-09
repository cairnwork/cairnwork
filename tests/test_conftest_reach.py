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
        # #976: the same through a relative import
        ("from . import helper\n", {"tests/sub/helper.cpython-314-darwin.so": ""},
         "tests/sub/helper.cpython-314-darwin.so"),
        ("from .. import helper\n", {"tests/helper.pyd": ""}, "tests/helper.pyd"),
        ("from .pkg import x\n", {"tests/sub/pkg/x.pyc": ""}, "tests/sub/pkg/x.pyc"),
        ("from . import x\n", {"tests/sub/__init__.cpython-314-darwin.so": ""},
         "tests/sub/__init__.cpython-314-darwin.so"),
        ("from . import helper\n", {"tests/sub/helper.txt": ""}, ""),  # data, not a module
        ("from . import helper\n", {"tests/helper.so": ""}, ""),  # another directory
        ("from . import helper\n", {"tests/x/tests/sub/helper.so": ""}, ""),  # same tail, deeper
        # #980 Codex r1: a cached module in __pycache__ is the module beside it
        ("from . import a\n", {"tests/sub/__pycache__/a.cpython-311.pyc": ""},
         "tests/sub/__pycache__/a.cpython-311.pyc"),
        ("import a\n", {"tests/__pycache__/a.cpython-311.pyc": ""},
         "tests/__pycache__/a.cpython-311.pyc"),
        ("from . import x\n", {"tests/sub/__pycache__/__init__.cpython-311.pyc": ""},
         "tests/sub/__pycache__/__init__.cpython-311.pyc"),
    ],
)  # fmt: skip
def test_a_direct_import_of_a_changed_file_is_found(text, tree, hit):
    changed = [path for path in tree if path.startswith("tests/")]
    assert _reach(text, tree, changed) == hit


@pytest.mark.parametrize(
    ("text", "tree", "changed", "hit"),
    [
        ("from tests.pkg import *\n", {"tests/pkg/__init__.py": "", "tests/pkg/mod.py": ""},
         ["tests/pkg/mod.py"], "tests/pkg/mod.py"),
        ("from .pkg import *\n", {"tests/sub/pkg/__init__.py": "", "tests/sub/pkg/mod.py": ""},
         ["tests/sub/pkg/mod.py"], "tests/sub/pkg/mod.py"),
        ("from . import *\n", {"tests/sub/__init__.py": "", "tests/sub/mod.py": ""},
         ["tests/sub/mod.py"], "tests/sub/mod.py"),
        ("from tests.mod import *\n", {"tests/mod.py": "", "tests/other.py": ""},
         ["tests/mod.py"], "tests/mod.py"),
        ("from tests.pkg import *\n", {"tests/pkg/__init__.py": "", "tests/pkg/deep/x.py": ""},
         ["tests/pkg/deep/x.py"], ""),
        ("from tests.pkg import *\n", {"tests/pkg/__init__.py": "", "tests/other.py": ""},
         ["tests/other.py"], ""),
    ],
)  # fmt: skip
def test_star_import_reaches_only_direct_package_modules(text, tree, changed, hit):
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


LITERAL_PLUGINS = [
    'pytest_plugins = []\npytest_plugins += ["x"]\n',
    'pytest_plugins = ()\npytest_plugins += ("x",)\n',
    'pytest_plugins = []\npytest_plugins.append("x")\n',
    'pytest_plugins = []\npytest_plugins.extend(["x"])\n',
    'pytest_plugins = []\npytest_plugins.insert(0, "x")\n',
    'pytest_plugins = ["base"] + ["x"]\n',
    'pytest_plugins = ["x"] + ["base"]\n',
    'pytest_plugins = [*["x"]]\n',
    'pytest_plugins = []\npytest_plugins[:] = ["x"]\n',
    'pytest_plugins, other = ["x"], 1\n',
    'pytest_plugins = []\npytest_plugins.insert(0, *["x"])\n',
    'pytest_plugins = "base, x"\n',  # pytest splits a string on commas
    'pytest_plugins = []\npytest_plugins.__iadd__(["x"])\n',  # any method on the list (r2)
    'pytest_plugins = ["te" + "sts.x"]\n',  # pytest loads the joined name (#1018 r1)
    'pytest_plugins = ["x"] + PLUGINS\n',  # the literal half of a partly unread value
    'pytest_plugins = ["x"] * 1\n',  # a literal under another operator
    '(pytest_plugins := ["x"])\n',  # an assignment expression binds it too (#1027)
]
UNREAD_PLUGINS = [
    'pytest_plugins = ["x"] if FLAG else []\n',
    'pytest_plugins = [p for p in ("x",)]\n',
    "pytest_plugins = PLUGINS\n",
    'pytest_plugins = list(("x",))\n',
    'pytest_plugins = [f"{NAME}"]\n',
    "(pytest_plugins := PLUGINS)\n",  # #1027
    "pytest_plugins = []\npytest_plugins.append(NAME)\n",
    'import sys\nsetattr(sys.modules[__name__], "pytest_plugins", P)\n',
    'globals()["pytest_plugins"] = P\n',
    'def pytest_configure(config):\n    getattr(config, "pytest_plugins", None)\n',
]
TWO = {"tests/x.py": "", "tests/y.py": ""}


@pytest.mark.parametrize("text", LITERAL_PLUGINS)
def test_every_literal_pytest_plugins_form_names_exactly_its_modules(text):
    """#1010: read, not guessed: the named module is a hit, another changed module is not."""
    assert _reach(text, TWO, ["tests/x.py"]) == "tests/x.py"
    assert _reach(text, TWO, ["tests/y.py"]) == ""


@pytest.mark.parametrize("text", UNREAD_PLUGINS)
def test_a_pytest_plugins_value_it_cannot_read_does_not_refuse_the_new_test_file(text):
    """#1014 r2: a value built at run time is not followed (a residual on #1010): refusing every
    changed module would refuse every task in such a repository, the test commit's own new test
    file included."""
    new = {**TWO, "tests/test_new.py": ""}
    assert _reach(text, new, ["tests/test_new.py"]) == ""


def test_the_parts_of_a_joined_plugin_name_are_not_plugins():
    """#1018 r2: pytest loads only the joined name, so a module named like one part is no hit."""
    tree = {"tests/x.py": "", "tests/te.py": ""}
    assert _reach('pytest_plugins = ["te" + "sts.x"]\n', tree, ["tests/te.py"]) == ""


def test_a_long_joined_plugin_name_does_not_raise():
    """#1018 r2: 2,000 ``+`` are read without recursion (3.11 cannot parse 3,000)."""
    for head in ("", "NAME + "):  # one joined string, and a chain with a name in front
        text = "pytest_plugins = [" + head + " + ".join(['"x"'] + ['""'] * 2000) + "]\n"
        assert _reach(text, {"tests/x.py": ""}, ["tests/x.py"]) == "tests/x.py"


def test_append_on_another_name_does_not_add_a_pytest_plugin():
    assert _reach('other.append("x")\n', {"tests/x.py": ""}, ["tests/x.py"]) == ""


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
