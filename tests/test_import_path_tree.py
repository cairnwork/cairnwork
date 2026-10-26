"""Tests for cairnwork.import_path_tree: import paths must be plain git trees."""

import os
import sys

import pytest
from conftest import commit_change

from cairnwork.gitcmd import run_git
from cairnwork.import_path_tree import ImportPathError, require_plain_dirs
from cairnwork.pipeline import TaskRejected
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_candidate import verify_candidate

PYTEST = ["python3", "-m", "pytest"]


def make_symlink(repo, link_name, target):
    link_path = repo / link_name
    link_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link_path)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", f"add symlink {link_name}")
    return run_git(repo, "rev-parse", "HEAD").strip()


def test_require_plain_dirs_symlink_rejected(repo):
    commit = make_symlink(repo, "lib", "tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("lib",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "lib" in str(exc.value)


def test_require_plain_dirs_symlink_outside_rejected(repo):
    commit = make_symlink(repo, "outside", "/tmp")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("outside",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "outside" in str(exc.value)


def test_require_plain_dirs_nested_symlink_rejected(repo):
    commit = make_symlink(repo, "linkdir", "tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("linkdir/pkg",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "linkdir" in str(exc.value)


def test_require_plain_dirs_symlink_in_subdirectory_rejected(repo):
    commit_change(repo, {"src/m.py": "x = 1\n"})
    commit = make_symlink(repo, "src/pkg", "tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("src/pkg",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "pkg" in str(exc.value)


def test_require_plain_dirs_case_insensitive(repo):
    commit = make_symlink(repo, "lib", "tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("LIB",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "LIB" in str(exc.value)


def test_require_plain_dirs_submodule_rejected(repo):
    fake_sha = "0123456789abcdef0123456789abcdef01234567"
    run_git(repo, "update-index", "--add", "--cacheinfo", "160000", fake_sha, "submod")
    run_git(repo, "commit", "-m", "add submodule")
    commit = run_git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("submod",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "submod" in str(exc.value)


def test_require_plain_dirs_regular_file_rejected(repo):
    commit = commit_change(repo, {"lib": "file contents\n"})
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("lib",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "lib" in str(exc.value)


def test_require_plain_dirs_normal_and_nonexistent_pass(repo):
    commit = commit_change(repo, {"src/pkg/__init__.py": ""})
    require_plain_dirs(repo, commit, ("src", "src/pkg", "nonexistent", ".", ""))


def test_require_plain_dirs_git_error_rejected(repo):
    invalid_commit = "0" * 40
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, invalid_commit, ("src",))
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_require_plain_dirs_ambiguous_case_rejected(repo):
    f = repo / "sub" / "a.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a")
    run_git(repo, "add", "-A")
    blob_sha = run_git(repo, "hash-object", "-w", str(f)).strip()
    run_git(repo, "update-index", "--add", "--cacheinfo", "100644", blob_sha, "SUB/b.txt")
    run_git(repo, "commit", "-m", "add colliding dirs")
    commit = run_git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("sub",))
    assert exc.value.code == "BAD_IMPORT_PATH"
    assert "ambiguous" in str(exc.value)


def test_run_verifiers_rejects_symlink_import_path(repo, tmp_path):
    commit_change(repo, {"tests/test_x.py": "def test_ok(): pass\n"})
    commit = make_symlink(repo, "lib", "tests")
    spec = parse_verifier("pytest", PYTEST + ["tests/test_x.py"])
    with pytest.raises(ImportPathError) as exc:
        run_verifiers(
            repo,
            commit,
            (spec,),
            tmp_path / "runs",
            "r1",
            python=sys.executable,
            import_paths=("lib",),
        )
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_run_verifiers_rejects_pyproject_pythonpath_symlink(repo, tmp_path):
    pyproject = '[tool.pytest.ini_options]\npythonpath = ["lib"]\n'
    commit_change(
        repo,
        {"pyproject.toml": pyproject, "tests/test_x.py": "def test_ok(): pass\n"},
    )
    commit = make_symlink(repo, "lib", "tests")
    spec = parse_verifier("pytest", PYTEST + ["tests/test_x.py"])
    with pytest.raises(ImportPathError) as exc:
        run_verifiers(
            repo,
            commit,
            (spec,),
            tmp_path / "runs",
            "r1",
            python=sys.executable,
            base_commit=commit,
        )
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_verify_candidate_rejects_symlink_import_path(repo, state):
    from verify_helpers import FIX, candidate

    from cairnwork.intake import Intake

    commit_change(repo, {"src/calc.py": "x = 1\n", "tests/test_a.py": ""})
    base = make_symlink(repo, "lib", "tests")
    cand = candidate(repo, base, {"src/calc.py": FIX})
    intake = Intake(state)
    with pytest.raises(TaskRejected) as exc:
        verify_candidate(
            intake,
            repo=repo,
            base=base,
            candidate=cand,
            allowed_files=("src/calc.py",),
            verifiers=(("pytest", ("python3", "-m", "pytest", "tests/test_a.py")),),
            import_paths=("lib",),
        )
    assert exc.value.code == "BAD_IMPORT_PATH"


@pytest.mark.parametrize("entry", ["a/./b", "a//b", "a/../a/b", "./a/./b"])
def test_require_plain_dirs_dot_or_empty_segment_rejected(repo, entry):
    commit_change(repo, {"a/m.py": "x = 1\n"})
    commit = make_symlink(repo, "a/b", "../tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, (entry,))
    assert exc.value.code == "BAD_IMPORT_PATH"


@pytest.mark.parametrize("entry", [":(top)q/x", ":/q/x"])
def test_require_plain_dirs_pathspec_magic_is_literal(repo, entry):
    commit_change(repo, {"q/x/m.py": "x = 1\n"})
    commit = make_symlink(repo, f"{entry.rsplit('/', 1)[0]}/x", "../tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, (entry,))
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_require_plain_dirs_whitespace_is_part_of_the_name(repo):
    commit_change(repo, {"lib/m.py": "x = 1\n"})
    commit = make_symlink(repo, " lib", "tests")
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, (" lib",))
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_require_plain_dirs_non_ascii_rejected(repo):
    commit = commit_change(repo, {"café/m.py": "x = 1\n"})
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, ("café",))
    assert exc.value.code == "BAD_IMPORT_PATH"


def test_run_verifiers_checks_the_candidate_tree(repo, tmp_path):
    commit_change(repo, {"tests/test_x.py": "def test_ok(): pass\n", "lib/m.py": "x = 1\n"})
    base = run_git(repo, "rev-parse", "HEAD").strip()
    run_git(repo, "rm", "-q", "-r", "lib")
    cand = make_symlink(repo, "lib", "tests")
    spec = parse_verifier("pytest", PYTEST + ["tests/test_x.py"])
    with pytest.raises(ImportPathError) as exc:
        run_verifiers(
            repo,
            cand,
            (spec,),
            tmp_path / "runs",
            "r1",
            python=sys.executable,
            base_commit=base,
            import_paths=("lib",),
        )
    assert exc.value.code == "BAD_IMPORT_PATH"
