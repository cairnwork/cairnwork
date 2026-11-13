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
