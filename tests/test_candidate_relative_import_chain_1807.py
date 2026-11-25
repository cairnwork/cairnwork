"""Issue #1807 (design C'): the ``__init__.py`` chain must exist, as a regular file, in BOTH the
base and the candidate tree, and the top-level package must not be a name pytest always shadows.
Helpers come from the main test file."""

from __future__ import annotations

import os

import pytest
from conftest import git
from test_candidate_relative_import_1807 import REFUSED, TYPES, _verdict

from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.workspace import CandidateRejected

IMPORT = "from .types import type_ok\n"
EXTRA = "\nEXTRA = 1\n"


def test_an_init_added_by_the_same_change_does_not_count(tmp_path):
    base = {"pkg/types.py": TYPES, "pkg/validator.py": IMPORT}
    new = {"pkg/__init__.py": "", "pkg/types.py": TYPES + EXTRA, "pkg/validator.py": IMPORT + EXTRA}
    assert _verdict(tmp_path, base, new) == REFUSED


def test_an_init_deleted_by_the_same_change_does_not_count(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/validator.py": IMPORT}
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    for path, text in base.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    (repo / "pkg/__init__.py").unlink()
    (repo / "pkg/types.py").write_text(TYPES + EXTRA)
    (repo / "pkg/validator.py").write_text(IMPORT + EXTRA)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    cand = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CandidateRejected) as exc:
        refuse_new_sys_path_mutation(repo, base_sha, cand, ["pkg/types.py", "pkg/validator.py"])
    assert exc.value.code == REFUSED


def test_an_init_renamed_by_the_same_change_does_not_count(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/validator.py": IMPORT}
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    for path, text in base.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    (repo / "pkg/__init__.py").rename(repo / "pkg/_init_old.py")
    (repo / "pkg/types.py").write_text(TYPES + EXTRA)
    (repo / "pkg/validator.py").write_text(IMPORT + EXTRA)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    cand = git(repo, "rev-parse", "HEAD")
    changed = ["pkg/types.py", "pkg/validator.py", "pkg/_init_old.py"]
    with pytest.raises(CandidateRejected) as exc:
        refuse_new_sys_path_mutation(repo, base_sha, cand, changed)
    assert exc.value.code == REFUSED


def test_an_intermediate_subpackage_without_init_is_refused(tmp_path):
    base = {
        "pkg/__init__.py": "",
        "pkg/types.py": TYPES,
        "pkg/sub/validator.py": "from ..types import type_ok\n",
    }
    new = {path: text + EXTRA for path, text in base.items() if path.endswith("validator.py")}
    new["pkg/types.py"] = TYPES + EXTRA
    assert _verdict(tmp_path, base, new) == REFUSED


def _symlink_repo(tmp_path, link_init):
    """Either pkg/__init__.py is a symlink, or the package a relative import names as its
    target (pkg/types) is a symlinked directory. The candidate edits regular files only."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "other").mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    (repo / "other/__init__.py").write_text("")
    (repo / "other/types.py").write_text(TYPES)
    if link_init:
        (repo / "pkg/types.py").write_text(TYPES)
        os.symlink("../other/__init__.py", repo / "pkg/__init__.py")
        (repo / "pkg/validator.py").write_text(IMPORT)
    else:
        (repo / "pkg/__init__.py").write_text("")
        os.symlink("../other", repo / "pkg/types")  # the target of the import is a symlink
        (repo / "pkg/validator.py").write_text(IMPORT)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    edited = ["pkg/validator.py", "pkg/types.py" if link_init else "other/types.py"]
    for path in edited:
        target = repo / path
        target.write_text(target.read_text() + EXTRA)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    try:
        refuse_new_sys_path_mutation(repo, base, git(repo, "rev-parse", "HEAD"), edited)
    except CandidateRejected as exc:
        return exc.code
    return None


def test_a_symlinked_init_is_refused(tmp_path):
    assert _symlink_repo(tmp_path, link_init=True) == REFUSED


def test_a_symlinked_package_directory_is_refused(tmp_path):
    assert _symlink_repo(tmp_path, link_init=False) == REFUSED


@pytest.mark.parametrize("top", ["_pytest", "pytest", "pluggy"])
def test_a_top_level_package_pytest_always_shadows_is_refused(tmp_path, top):
    base = {
        f"{top}/__init__.py": "",
        f"{top}/doctest.py": "class DoctestModule:\n    pass\n",
        f"{top}/test_x.py": "from .doctest import DoctestModule\n",
    }
    new = {path: text + EXTRA for path, text in base.items()}
    assert _verdict(tmp_path, base, new) == REFUSED
