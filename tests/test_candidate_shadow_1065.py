"""Issue #1065: which candidate files count as a shadow next to an import-system signal."""

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected


def _changed(repo, base, cand):
    """Every changed path as the candidate's allowed files (a shadow must be the candidate's)."""
    return tuple(git(repo, "diff", "--name-only", base, cand).split())


def test_signal_and_nested_stdlib_module_are_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "src/a.py": "import sys\nsys.path.insert(0, 'outside')\n",
            "pkg/fractions.py": "def Fraction(*args): return 1\n",
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "src/a.py" in str(got.value) and "pkg/fractions.py" in str(got.value)


@pytest.mark.parametrize("shadow", ["pkg/fractions.pyc", "pkg/fractions.abi3.so"])
def test_signal_and_nested_compiled_or_extension_shadow_are_refused(repo, shadow):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo, {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", shadow: ""}
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert shadow in str(got.value)


def test_signal_and_nested_test_import_package_are_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, {"tests/test_x.py": "import helpers\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/helpers/__init__.py": ""},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/helpers/__init__.py" in str(got.value)


def test_harmless_nested_module_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/harmless.py": ""},
    )
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_src_layout_module_the_tests_import_is_not_a_shadow(repo):
    commit_change(repo, {"src/calc.py": "X = 1\n", "tests/test_c.py": "from calc import X\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/calc.py": "import sys\nsys.path.insert(0, 'src')\nX = 2\n"})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_a_second_file_with_a_test_imported_name_is_a_shadow(repo):
    commit_change(repo, {"src/calc.py": "X = 1\n", "tests/test_c.py": "from calc import X\n"})
    base = commit_change(repo, {"vendor/calc.py": "X = 2\n"})
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'vendor')\n", "vendor/calc.py": "X = 1\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "vendor/calc.py" in str(got.value)


def test_a_signal_word_already_in_base_is_no_exemption(repo):
    commit_change(
        repo,
        {"pkg/__init__.py": "# subprocess\n", "tests/test_o.py": "from pkg import oracle\n"},
    )
    base = commit_change(repo, {"pkg/oracle.py": "def answer():\n    return 1\n"})
    cand = commit_change(
        repo,
        {
            "pkg/__init__.py": "# subprocess\n__path__.insert(0, 'sub')\n",
            "sub/oracle.py": "def answer():\n    return 2\n",
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "sub/oracle.py" in str(got.value)


@pytest.mark.parametrize("existing", ["pkg/tests/conftest.py", "pkg/types.py"])
def test_unchanged_base_files_are_never_shadows(repo, existing):
    base = commit_change(repo, {"pkg/calc.py": "X = 1\n", existing: ""})
    cand = commit_change(repo, {"pkg/calc.py": "import subprocess\nX = 2\n"})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_a_package_the_project_code_imports_can_be_shadowed(repo):
    commit_change(repo, {"pkg/calc.py": "import requests\n", "tests/test_c.py": "import pkg\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo, {"src/a.py": "import sys\nsys.path.insert(0, 'shadow')\n", "shadow/requests.py": ""}
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "shadow/requests.py" in str(got.value)


@pytest.mark.parametrize(
    "importer",
    [
        "from . import helper\n",
        "from .helper import X\n",
        "import importlib\nimportlib.import_module('pkg.helper')\n",
        "import pytest\npytest.importorskip('helper')\n",
    ],
)
def test_relative_and_string_imports_name_a_shadow(repo, importer):
    commit_change(repo, {"pkg/__init__.py": "", "pkg/test_h.py": importer})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo, {"pkg/__init__.py": "__path__.insert(0, 'shadow')\n", "shadow/helper.py": ""}
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "shadow/helper.py" in str(got.value)


def test_root_stdlib_named_module_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "json.py": ""},
    )
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_shadow_file_without_signal_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/fractions.py": ""})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_test_commit_import_can_define_shadow_name(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/helpers.py": ""},
    )
    test_commit = commit_change(repo, {"tests/test_x.py": "import helpers\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand), test_commit=test_commit
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


def test_unparseable_candidate_test_file_fails_closed(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "tests/test_x.py": "if:\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "UNREADABLE_CHANGE"
