"""Names bound into pytest_plugins that the reach check cannot read are refused (#1341, #1342)."""

import ast

import pytest

from cairnwork.conftest_reach import reached_change

CONFTEST = "tests/conftest.py"
REFUSED = "has an unreadable pytest_plugins with-binding"


def _reach(text):
    files = frozenset({CONFTEST, "tests/safe.py", "tests/plist.py"})
    return reached_change(
        CONFTEST, ast.parse(text), frozenset({"tests/evil.py"}), files, lambda p: ast.parse("")
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("from plist import P as pytest_plugins\n", id="from_import_as"),
        pytest.param("from plist import pytest_plugins\n", id="from_import_same_name"),
        pytest.param("import plist as pytest_plugins\n", id="import_as"),
        pytest.param("import pytest_plugins\n", id="import_plain"),
        pytest.param("pytest_plugins = f(['safe'])\n", id="assign_call"),
        pytest.param("pytest_plugins: list = f(['safe'])\n", id="annotated_assign_call"),
        pytest.param("pytest_plugins += f(['safe'])\n", id="augmented_assign_call"),
        pytest.param("pytest_plugins = [*f()]\n", id="assign_starred"),
        pytest.param("for pytest_plugins in f(['safe']):\n    pass\n", id="for_call"),
        pytest.param("match f(['safe']):\n    case pytest_plugins:\n        pass\n", id="match"),
        pytest.param(
            "from contextlib import nullcontext\n"
            "with nullcontext(x) as pytest_plugins:\n    pass\n",
            id="with_name",
        ),
        pytest.param(
            "from contextlib import nullcontext\n"
            "with nullcontext(f()) as pytest_plugins:\n    pass\n",
            id="with_call",
        ),
        pytest.param("pytest_plugins.append(f())\n", id="method_call_arg"),
        pytest.param(
            "list = lambda x: ['evil']\npytest_plugins = list(['safe'])\n", id="builtin_assigned"
        ),
        pytest.param(
            "def list(x):\n    return ['evil']\npytest_plugins = list(['safe'])\n",
            id="builtin_def",
        ),
        pytest.param(
            "from plist import P as list\npytest_plugins = list(['safe'])\n",
            id="builtin_imported",
        ),
        pytest.param("with open('p') as pytest_plugins:\n    pass\n", id="with_other_manager"),
        pytest.param("(pytest_plugins := f(['safe']))\n", id="walrus_call"),
    ],
)
def test_unreadable_binding_is_refused(text):
    assert _reach(text) == REFUSED


@pytest.mark.parametrize(
    "text",
    [
        "pytest_plugins = ['safe']\n",
        "pytest_plugins = 'safe'\n",
        "pytest_plugins = ('safe', 'other')\n",
        "pytest_plugins = ['a'] + ['b']\n",
        "pytest_plugins += ['safe']\n",
        "pytest_plugins = []\n",
        "for pytest_plugins in ['safe']:\n    pass\n",
        "pytest_plugins = ['safe', x]\n",
        "match x:\n    case ['safe'] as pytest_plugins:\n        pass\n",
        "pytest_plugins = list(('safe',))\n",
        "pytest_plugins = PLUGINS\n",
        "def h():\n    import pytest_plugins\n",
        "class K:\n    from plist import pytest_plugins\n",
        "import plist\n",
        "from plist import P\n",
    ],
)
def test_literal_binding_is_not_refused(text):
    assert _reach(text) == ""
