"""Tests for Issue #1148: modified unique module as a shadow (#1111 residual)."""

from __future__ import annotations

import subprocess

import pytest
from conftest import commit_change, git

from cairnwork import candidate_shadow, candidate_sys_path
from cairnwork.workspace import CandidateRejected

REDIR = "import sys\nsys.path.insert(0, 'deep')\n"


def _changed(repo, base, cand):
    """Every changed path as the candidate's allowed files."""
    return tuple(git(repo, "diff", "--name-only", base, cand).split())


def test_modified_unique_module_outside_an_import_root_is_a_shadow(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


def test_find_shadow_file_names_the_modified_unique_module(repo):
    cand = commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"deep/foo.py": "MODIFY"})
        == "deep/foo.py"
    )


def test_importer_from_the_test_commit_counts(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    git(repo, "checkout", "-q", "--detach", base)
    tc = commit_change(repo, {"tests/test_t.py": "import foo\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand), test_commit=tc
        )
    assert "deep/foo.py" in str(got.value)


def test_same_directory_unchanged_importer_is_not_a_shadow(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n", "deep/test_foo.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_package_member_is_not_a_shadow(repo):
    commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1\n",
            "tests/test_t.py": "from pkg import foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_lib_layout_module_is_not_a_shadow(repo):
    commit_change(repo, {"lib/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"lib/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_changed_importer_in_the_same_directory_does_not_exempt(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"deep/foo.py": "X = 2\n", "deep/test_foo.py": "import foo\n", "conftest.py": REDIR},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "deep/foo.py" in str(got.value)


def test_package_member_bare_imported_outside_package_is_refused(repo):
    commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1\n",
            "tests/test_t.py": "import foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"pkg/foo.py": "X = 2\n", "conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/foo.py" in str(got.value)


def test_candidate_added_init_does_not_exempt_package_member(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "deep/__init__.py": "",
            "deep/foo.py": "X = 2\n",
            "conftest.py": REDIR,
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


def test_modified_init_judges_parent_directory_not_itself(repo):
    commit_change(
        repo,
        {"deep/pkg/__init__.py": "X = 1\n", "tests/test_t.py": "import pkg\n"},
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "deep/pkg/__init__.py": "X = 2\n",
            "conftest.py": REDIR,
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/pkg/__init__.py" in str(got.value)


def test_case_mismatched_src_directory_is_not_exempt(repo):
    commit_change(repo, {"Lib/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "Lib/foo.py": "X = 2\n",
            "conftest.py": "import sys\nsys.path.insert(0, 'Lib')\n",
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "Lib/foo.py" in str(got.value)


@pytest.mark.parametrize(
    "importer",
    [
        'import importlib\nimportlib.import_module("foo")\n',
        '__import__("foo")\n',
        'import pytest\npytest.importorskip("foo")\n',
    ],
)
def test_package_member_dynamically_imported_outside_package_is_refused(repo, importer):
    # Codex R3 on #1172: a dynamic bare import must count as a top-level import too
    commit_change(
        repo,
        {"pkg/__init__.py": "", "pkg/foo.py": "X = 1\n", "tests/test_t.py": importer},
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"pkg/foo.py": "X = 2\n", "conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


def test_package_member_with_relative_import_sibling_is_refused(repo):
    # C2: sibling 'from . import foo' does not exempt modified package member
    commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1\n",
            "pkg/bar.py": "from . import foo\n",
            "tests/test_t.py": "import foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"pkg/foo.py": "X = 2\n", "conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/foo.py" in str(got.value)


def test_wrapper_module_with_relative_import_sibling_is_refused(repo):
    # L6b: wrapper module with sibling 'from . import yaml' is refused
    commit_change(
        repo,
        {
            "mypkg/__init__.py": "",
            "mypkg/yaml.py": "X = 1\n",
            "mypkg/loader.py": "from . import yaml\n",
            "tests/test_t.py": "import yaml\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"mypkg/yaml.py": "X = 2\n", "conftest.py": "import sys\nsys.path.insert(0, 'mypkg')\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "mypkg/yaml.py" in str(got.value)


def test_modified_init_with_package_internal_importer_is_refused(repo):
    # F2e: internal relative/package-qualified importer does not exempt modified __init__.py
    commit_change(
        repo,
        {
            "deep/pkg/__init__.py": "X = 1\n",
            "deep/pkg/helper.py": "",
            "deep/pkg/util.py": "from pkg import helper\n",
            "tests/test_t.py": "import pkg\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"deep/pkg/__init__.py": "X = 2\n", "conftest.py": REDIR},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/pkg/__init__.py" in str(got.value)


def test_added_init_with_variable_import_is_refused(repo):
    # I2: candidate added init with test variable import and other.foo elsewhere kills M2
    commit_change(
        repo,
        {
            "deep/foo.py": "X = 1\n",
            "tests/test_t.py": "import importlib\nN = 'foo'\nmod = importlib.import_module(N)\n",
            "tests/h.py": "import other.foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "deep/__init__.py": "",
            "deep/foo.py": "X = 2\n",
            "conftest.py": REDIR,
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


def test_modified_init_with_variable_import_is_refused(repo):
    # I3: modified init with test variable import and other.pkg elsewhere kills M3
    commit_change(
        repo,
        {
            "deep/pkg/__init__.py": "X = 1\n",
            "tests/test_t.py": "import importlib\nN = 'pkg'\nmod = importlib.import_module(N)\n",
            "tests/h.py": "import other.pkg\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "deep/pkg/__init__.py": "X = 2\n",
            "conftest.py": REDIR,
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/pkg/__init__.py" in str(got.value)


def test_case_mismatched_init_filename_is_not_an_init(repo):
    # M5: case-exact '__init__.py' filename, not casefolded
    commit_change(
        repo,
        {
            "pkg/__INIT__.py": "",
            "pkg/foo.py": "X = 1\n",
            "tests/test_t.py": "import importlib\nN = 'foo'\nmod = importlib.import_module(N)\n",
            "tests/h.py": "import other.foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "pkg/foo.py": "X = 2\n",
            "conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n",
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/foo.py" in str(got.value)


def test_case_mismatched_importer_directory_does_not_exempt(repo):
    # M6: case-exact importer directory matching pkg_dir
    # Index-only entry with exact-case paths: a case-insensitive filesystem (macOS) would fold
    # Deep/ into deep/.
    git(repo, "config", "core.ignorecase", "false")
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="import foo\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},Deep/test_foo.py")
    git(repo, "update-index", "--skip-worktree", "Deep/test_foo.py")
    base = commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    cand = commit_change(
        repo,
        {"deep/foo.py": "X = 2\n", "conftest.py": REDIR},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)
