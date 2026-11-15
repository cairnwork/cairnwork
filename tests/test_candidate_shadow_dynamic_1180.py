"""Issue #1180: unknown dynamic imports fail closed in candidate shadow checks."""

import pytest
from conftest import commit_change

from cairnwork import candidate_shadow


@pytest.mark.parametrize(
    "expression",
    [
        "'f' + 'oo'",
        "f'{prefix}oo'",
        "module_name",
    ],
)
def test_nonliteral_import_name_refuses_changed_nested_module(repo, expression):
    commit_change(
        repo,
        {
            "deep/foo.py": "VALUE = 1\n",
            "tests/test_t.py": (
                "import importlib\n"
                "prefix = 'f'\n"
                "module_name = 'foo'\n"
                f"importlib.import_module({expression})\n"
            ),
        },
    )
    cand = commit_change(
        repo,
        {
            "deep/foo.py": "VALUE = 2\n",
            "conftest.py": "import sys\nsys.path.insert(0, 'deep')\n",
        },
    )

    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"deep/foo.py": "MODIFY"})
        == "deep/foo.py"
    )


@pytest.mark.parametrize(
    "call",
    [
        "importlib.import_module(name)",
        "__import__(name)",
        "pytest.importorskip(name)",
        "importlib.import_module(name=name)",
    ],
)
def test_each_supported_dynamic_import_call_marks_unknown_import(call):
    raw = f"importlib\nname = 'foo'\n{call}\n".encode()

    assert "*" in candidate_shadow._parse_imports(raw, "test_dynamic.py")


@pytest.mark.parametrize(
    "source",
    [
        "from importlib import import_module as load\nload(name)",
        "import importlib as il\nil.import_module(name)",
        "from builtins import __import__ as load\nload(name)",
        "import builtins as bi\nbi.__import__(name)",
        "import importlib as il\nload = il.import_module\nload(name)",
        "import builtins as bi\nfirst = bi.__import__\nload = first\nload(name)",
        "from importlib import unknown_loader as load\nload(name)",
    ],
)
def test_aliased_dynamic_import_refuses_changed_nested_module(repo, source):
    importer = "name = 'foo'\n" + source
    commit_change(repo, {"deep/foo.py": "VALUE = 1\n", "tests/test_t.py": importer})
    cand = commit_change(
        repo,
        {
            "deep/foo.py": "VALUE = 2\n",
            "conftest.py": "import sys\nsys.path.insert(0, 'deep')\n",
        },
    )

    assert "*" in candidate_shadow._parse_imports(importer.encode(), "test_alias.py")
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"deep/foo.py": "MODIFY"})
        == "deep/foo.py"
    )


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module(*args)",
        "from importlib import import_module as load\nload(*args)",
        "from importlib import import_module as load\nload(**kwargs)",
        "import builtins\nbuiltins.__import__(*args)",
        "from builtins import __import__ as load\nload(*args)",
        "import builtins\nbuiltins.__import__(**kwargs)",
        "from builtins import __import__ as load\nload(**kwargs)",
        "import pytest\npytest.importorskip(*args)",
        "from pytest import importorskip as load\nload(*args)",
        "import pytest\npytest.importorskip(**kwargs)",
        "from pytest import importorskip as load\nload(**kwargs)",
        "import importlib as il\nload = il.import_module\nload(**kwargs)",
    ],
)
def test_dynamic_import_unpacking_fails_closed_in_both_parsers(source):
    raw = f"args = ()\nkwargs = {{'name': 'foo'}}\n{source}\n".encode()

    assert "*" in candidate_shadow._parse_imports(raw, "test_unpacking.py")
    assert "*" in candidate_shadow._parse_top_level_imports(raw, "test_unpacking.py")
