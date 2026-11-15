"""Issue #1180: unknown dynamic imports fail closed in candidate shadow checks."""

import pytest
from conftest import commit_change

from cairnwork import candidate_shadow, candidate_sys_path
from cairnwork.workspace import CandidateRejected

_CLI_TEST = "import subprocess\nVERSION = {}\ndef invoke(): return subprocess.run([])\n"


def _refuse(repo, base, candidate, allowed):
    return candidate_sys_path.refuse_new_sys_path_mutation(repo, base, candidate, allowed)


@pytest.mark.parametrize(
    "expression",
    [
        "'f' + 'oo'",
        "f'{prefix}oo'",
        "module_name",
        "*['f' + 'oo']",
        "**{'name': 'f' + 'oo'}",
    ],
)
def test_nonliteral_import_name_in_test_refuses_changed_nested_module(repo, expression):
    base = commit_change(
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

    with pytest.raises(CandidateRejected) as got:
        _refuse(repo, base, cand, ("deep/foo.py", "conftest.py"))
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


@pytest.mark.parametrize(
    "call",
    [
        "importlib.import_module('f' + 'oo')",
        "importlib.import_module(f'{p}oo')",
        "importlib.import_module(name)",
        "importlib.import_module(*['f'+'oo'])",
        "importlib.import_module(**{'name': 'f'+'oo'})",
        "__import__(name)",
        "pytest.importorskip(name)",
        "importlib.import_module(name=name)",
        "importlib.import_module(**kw)",
        "__import__(**kw)",
    ],
)
def test_dynamic_import_call_in_test_file_blocks_candidate_path_redirect(repo, call):
    base = commit_change(
        repo,
        {
            "deep/foo.py": "VALUE = 1\n",
            "tests/test_dynamic.py": (
                "import importlib\nimport pytest\np = 'f'\nname = 'foo'\nkw = {'name': name}\n"
                f"{call}\n"
            ),
        },
    )
    cand = commit_change(
        repo,
        {
            "deep/foo.py": "VALUE = 2\n",
            "conftest.py": 'import sys\nsys.path.insert(0, "deep")\n',
        },
    )

    with pytest.raises(CandidateRejected) as got:
        _refuse(repo, base, cand, ("deep/foo.py", "conftest.py"))
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


@pytest.mark.parametrize(
    "loader_path",
    ["pkg/loader.py", "src/app/plugins.py"],
)
def test_library_dynamic_loader_does_not_block_unrelated_constant_edit(repo, loader_path):
    base = commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            loader_path: (
                "import importlib\ndef load(name): return importlib.import_module(name)\n"
            ),
            "tools/build.py": "import subprocess\nVERSION = 1\n",
        },
    )
    cand = commit_change(repo, {"tools/build.py": "import subprocess\nVERSION = 2\n"})

    _refuse(repo, base, cand, ("tools/build.py",))


def test_library_dynamic_loader_does_not_block_test_constant_edit(repo):
    base = commit_change(
        repo,
        {
            "src/app/plugins.py": (
                "import importlib\ndef load(name): return importlib.import_module(name)\n"
            ),
            "tests/test_cli.py": _CLI_TEST.format(1),
        },
    )
    cand = commit_change(
        repo,
        {"tests/test_cli.py": _CLI_TEST.format(2)},
    )

    _refuse(repo, base, cand, ("tests/test_cli.py",))


def test_control_without_library_loader_allows_test_constant_edit(repo):
    base = commit_change(
        repo,
        {"tests/test_cli.py": _CLI_TEST.format(1)},
    )
    cand = commit_change(
        repo,
        {"tests/test_cli.py": _CLI_TEST.format(2)},
    )

    _refuse(repo, base, cand, ("tests/test_cli.py",))


@pytest.mark.parametrize(
    "call",
    [
        "importlib.import_module(name)",
        "__import__(name)",
        "pytest.importorskip(name)",
        "importlib.import_module(name=name)",
        "importlib.import_module(**kw)",
        "__import__(**kw)",
    ],
)
def test_each_supported_dynamic_import_call_marks_unknown_import(call):
    raw = f"importlib\nname = 'foo'\n{call}\n".encode()

    assert "*" in candidate_shadow._parse_imports(raw, "test_dynamic.py")
