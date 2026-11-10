"""Issue #1116: batch tree and source reads without changing shadow judgments."""

import warnings

import pytest
from conftest import commit_change, git

from cairnwork import candidate_shadow
from cairnwork.gitcmd import GitCommandError


def test_git_reads_are_bounded_as_file_count_grows(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {f"pkg/f{i}.py": "import json\n" for i in range(40)})
    calls = []
    original = candidate_shadow.run_git_bytes

    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(candidate_shadow, "run_git_bytes", counted)
    candidate_shadow.find_shadow_file(repo, cand, changed={})
    assert calls.count("ls-tree") == 1
    assert calls.count("cat-file") == 1
    assert len(calls) == 2
    assert base


def test_unchanged_syntaxwarning_is_suppressed(repo):
    commit = commit_change(repo, {"pkg/unrelated.py": 'x = "\\d"\n'})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        candidate_shadow.find_shadow_file(repo, commit, changed={})
    assert not [w for w in seen if issubclass(w.category, SyntaxWarning)]


def test_changed_unparseable_source_still_raises(repo):
    commit = commit_change(repo, {"pkg/broken.py": "if:\n"})
    with pytest.raises(ValueError, match=rf"{commit}:pkg/broken\.py could not be parsed"):
        candidate_shadow.find_shadow_file(repo, commit, changed={"pkg/broken.py": "MODIFY"})


def test_gitlink_python_path_still_raises(repo):
    child = commit_change(repo, {"leaf": "x\n"})
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{child},pkg/link.py")
    git(repo, "commit", "-m", "gitlink")
    commit = git(repo, "rev-parse", "HEAD")
    with pytest.raises(GitCommandError):
        candidate_shadow.find_shadow_file(repo, commit, changed={})


def test_shared_candidate_and_test_blob_has_same_import_result(repo):
    commit = commit_change(repo, {"pkg/importer.py": "import helperx\n", "pkg/helperx.py": ""})
    result = candidate_shadow.find_shadow_file(
        repo, commit, test_commit=commit, changed={"pkg/helperx.py": "ADD"}
    )
    assert result == "pkg/helperx.py"
    unused = commit_change(repo, {"pkg/importer.py": "import other\n"})
    assert (
        candidate_shadow.find_shadow_file(
            repo, unused, test_commit=commit, changed={"pkg/helperx.py": "ADD"}
        )
        == "pkg/helperx.py"
    )  # the test commit's import still counts
