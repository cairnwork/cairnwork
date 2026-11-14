"""Test-strength sweep 2: lineage.py survivors.

verify_origin must refuse a non-https, look-alike-host, longer-name or prefix-name origin;
require_on_main takes only a lowercase full 40-hex commit id (never a revision expression, a
short id or a tag object) and must refresh origin/main even when the clone's own fetch
refspec is narrower.
"""

import pytest
import test_issue_runner as shared
from conftest import commit_change, git

import cairnwork.lineage as lineage

setup = shared.setup


def _clone_with_origin(tmp_path, url):
    clone = tmp_path / "c"
    git(tmp_path, "init", "-q", str(clone))
    git(clone, "remote", "add", "origin", url)
    return clone


@pytest.mark.parametrize(
    "url, repo, code",
    [
        ("http://github.com/o/r", "o/r", "LINEAGE_REMOTE_UNKNOWN"),
        ("https://githubXcom/o/r", "o/r", "LINEAGE_REMOTE_UNKNOWN"),
        ("https://github.com/o/r", "o/rx", "LINEAGE_REPOSITORY_MISMATCH"),  # origin shorter
        ("https://github.com/o/rx", "o/r", "LINEAGE_REPOSITORY_MISMATCH"),  # origin longer
        ("https://github.com/o/r-evil", "o/r", "LINEAGE_REPOSITORY_MISMATCH"),
        ("https://github.com/oo/r", "o/r", "LINEAGE_REPOSITORY_MISMATCH"),
    ],
)
def test_verify_origin_refuses(tmp_path, url, repo, code):
    clone = _clone_with_origin(tmp_path, url)
    with pytest.raises(RuntimeError, match=code):
        lineage.verify_origin(clone, repo)


def test_verify_origin_accepts_the_exact_repository(tmp_path):
    lineage.verify_origin(_clone_with_origin(tmp_path, "https://github.com/o/r.git"), "o/r")


def _tip(setup):
    return git(setup["clone"], "rev-parse", "origin/main")


@pytest.mark.parametrize(
    "shape",
    [
        lambda t: t + "^{commit}",
        lambda t: t + "^{tree}",
        lambda t: t[:7],
        lambda t: t[:39],
        lambda t: t.upper(),
        lambda t: t + "\n",
    ],
)
def test_only_a_full_lowercase_commit_id_is_a_base(setup, shape):
    base = shape(_tip(setup))
    with pytest.raises(RuntimeError, match="LINEAGE_BASE_NOT_COMMIT"):
        lineage.require_on_main(setup["clone"], base, fetch=False)


def test_a_tag_object_is_not_a_commit(setup):
    clone = setup["clone"]
    git(
        clone,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "tag",
        "-a",
        "-m",
        "m",
        "t1",
        _tip(setup),
    )
    tag_oid = git(clone, "rev-parse", "t1")
    with pytest.raises(RuntimeError, match="LINEAGE_BASE_NOT_COMMIT"):
        lineage.require_on_main(clone, tag_oid, fetch=False)


def test_main_is_refreshed_even_with_a_narrow_fetch_refspec(setup):
    clone = setup["clone"]
    git(clone, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    repo = setup["origin"].parent / "repo"
    new = commit_change(repo, {"narrow.txt": "n\n"})
    git(repo, "push", "-q", str(setup["origin"]), "main")
    lineage.require_on_main(clone, new)
    assert git(clone, "rev-parse", "refs/remotes/origin/main") == new


@pytest.mark.parametrize(
    "url", [" https://github.com/o/r", "https://github.com/o/r ", "https://github.com/o/r\t"]
)
def test_verify_origin_does_not_normalise_whitespace(tmp_path, url):
    clone = _clone_with_origin(tmp_path, "https://github.com/o/r")
    git(clone, "remote", "set-url", "origin", url)
    with pytest.raises(RuntimeError, match="LINEAGE_REMOTE_UNKNOWN"):
        lineage.verify_origin(clone, "o/r")


def test_a_rewritten_main_is_followed(setup):
    # main was force-pushed (history rewritten): the tracking ref must follow it, not stay stale
    repo = setup["origin"].parent / "repo"
    git(repo, "reset", "-q", "--hard", "HEAD^")
    new = commit_change(repo, {"rewritten.txt": "r\n"})
    git(repo, "push", "-q", "--force", str(setup["origin"]), "main")
    lineage.require_on_main(setup["clone"], new)
