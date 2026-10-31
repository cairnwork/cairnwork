"""The start allow-list (#261): its flags, the manifest, resume, and the real digest query."""

import hashlib
import json
import subprocess

import local_worker_fakes
import pytest
from local_worker_allow_fakes import AllowWorld, canary_report
from local_worker_fakes import (
    NO_AUTO_MAINTENANCE,
    TAG,
    cli_harness,
    lines,
    make_runs,
    reply,
)
from local_worker_fakes import run_wrapper as run

from experimental.local_worker import corpus_io
from experimental.local_worker.base_check import compress
from experimental.local_worker.gitstate import TreeState, parse_digest, parse_status
from experimental.local_worker.untracked_allow import UNTRACKED_ALLOW_RULE, build, roots

READ = reply("read_file", path="src/app.py")
FLAGS = ("--allow-untracked", "data", "--allow-untracked", "gen/fixture.json")


# -- matching and validation --------------------------------------------------------------------
def test_a_path_entry_matches_whole_segments_only():
    allow = build(["data", "pkg/_version.py"], None)
    assert allow.matches("data") and allow.matches("data/") and allow.matches("data/sub/y.dat")
    assert not allow.matches("data2/x") and not allow.matches("data2")
    assert not allow.matches("datax") and not allow.matches("src/data/x")
    assert allow.matches("pkg/_version.py") and not allow.matches("pkg/_version.pyc")


def test_a_glob_entry_matches_the_path_or_a_leading_prefix():
    allow = build(None, ["*.egg-info", "src/*.egg-info"])
    assert allow.matches("pkg.egg-info/PKG-INFO") and allow.matches("src/a.egg-info/x")
    assert not allow.matches("pkg.egg-info2/x")


@pytest.mark.parametrize(
    "bad", ["/abs", "../x", "a/../b", ".git", ".GIT/x", "a/.git/b", "a\0b", "a\nb", "", "./a"]
)
def test_a_bad_entry_is_refused(bad):
    with pytest.raises(ValueError):
        build([bad], None)
    with pytest.raises(ValueError):
        build(None, [bad + "/x*"])


@pytest.mark.parametrize("glob", ["*", "**", "*/*", "?", "*/"])
def test_a_glob_that_matches_any_path_is_refused(glob):
    with pytest.raises(ValueError, match="matches any path"):
        build(None, [glob])


def test_a_path_entry_with_a_glob_character_is_refused():
    with pytest.raises(ValueError, match="glob character"):
        build(["*.egg-info"], None)


def test_entries_are_sorted_unique_and_inactive_when_empty():
    allow = build(["data/", "data", "a"], ["b*"])
    assert (allow.paths, allow.globs) == (("a", "data"), ("b*",))
    assert not build(None, None).active and not build([], []).active


# -- the CLI, the manifest and resume -----------------------------------------------------------
@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(local_worker_fakes, "World", AllowWorld)  # each run root: harness files
    run_cli, out = cli_harness(tmp_path, monkeypatch)
    SEEN.clear()

    def canary(path, out_dir, **kw):
        SEEN.append(kw)
        return {"report_sha256": "c" * 64, "report": "r.json"}

    monkeypatch.setattr(corpus_io, "verify_canary", canary)
    return run_cli, out


SEEN: list[dict] = []  # verify_canary's keyword arguments, per CLI run


def _transcript(out, n=0):
    return out / "private" / lines(out)[n]["episode_id"] / "transcript.json"


def test_the_list_is_frozen_in_the_manifest(harness):
    run_cli, out = harness
    assert run_cli([READ], "run", *FLAGS, "--allow-untracked-glob", "*.egg-info") == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["untracked_allow"] == {
        "paths": ["data", "gen/fixture.json"],
        "globs": ["*.egg-info"],
        "rule": UNTRACKED_ALLOW_RULE,
    }
    assert lines(out)[0]["first_edit_cut"]["cut_status"] == "NO_CUT"
    assert SEEN == [{"require_current": True}]  # the digest needs this repo's wrapper
    assert run_cli([READ], "run", *FLAGS) == 2  # another list: the frozen manifest refuses


def test_without_a_list_the_manifest_has_no_field_and_harness_files_refuse(harness):
    run_cli, out = harness
    assert run_cli([READ], "run") == 2  # the old start check: no episode
    assert SEEN == [{}]  # the call as before #261: the old wrapper is accepted too
    assert "untracked_allow" not in json.loads((out / "manifest.json").read_text())


@pytest.mark.parametrize("bad", ["../x", ".git", "/abs", "data*"])
def test_the_cli_refuses_a_bad_entry_before_anything(harness, bad):
    run_cli, out = harness
    assert run_cli([], "run", "--allow-untracked", bad) == 2
    assert not (out / "manifest.json").exists()


def test_a_resume_repeats_the_parents_list(harness, tmp_path):
    run_cli, out = harness
    edit = reply("edit_file", path="src/app.py", target="x = 1", replacement="x = 2")
    assert run_cli([READ, edit], "run", *FLAGS) == 0
    tr = _transcript(out)
    private = json.loads(tr.read_text())
    assert private["untracked_allow"]["paths"] == ["data", "gen/fixture.json"]
    assert any(e.startswith("! data/") for e in private["untracked_allow"]["base"])
    done = [reply("finish", status="DONE")]
    assert run_cli(done, "resume", "--parent-transcript", str(tr)) == 2  # no list
    assert run_cli(done, "resume", "--parent-transcript", str(tr), *FLAGS) == 0
    # the parent's list differs from the resume's (here edited in the transcript itself)
    private["untracked_allow"]["paths"] = ["data"]
    tr.write_text(json.dumps(private))
    assert run_cli(done, "resume", "--parent-transcript", str(tr), *FLAGS) == 2


def test_a_resume_refuses_a_base_with_other_harness_files(harness, monkeypatch):
    run_cli, out = harness
    assert run_cli([READ, reply("list_dir", path="src")], "run", *FLAGS) == 0
    tr = str(_transcript(out))

    class Other(AllowWorld):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.files["data/x.dat"] = "regenerated differently\n"

    monkeypatch.setattr(local_worker_fakes, "World", Other)
    done = [reply("finish", status="DONE")]
    assert run_cli(done, "resume", "--parent-transcript", tr, "--cut-turn", "1", *FLAGS) == 2


# -- the real wrapper's digest query ------------------------------------------------------------
@pytest.fixture
def runs(tmp_path):
    return make_runs(tmp_path)


def _harness_checkout(runs):
    co = runs / TAG / "checkout"
    (co / ".git").rmdir()
    git = ["git", "-C", str(co), "-c", "user.email=t@t", "-c", "user.name=t", *NO_AUTO_MAINTENANCE]
    subprocess.run([*git, "init", "-q"], check=True)
    (co / "sub" / "keep.txt").write_text("k\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    (co / "data" / "sub").mkdir(parents=True)  # what a harness lays down, excluded locally
    (co / "data" / "x.dat").write_text("one\ntwo\n")
    (co / "data" / "sub" / "y.dat").write_text("y\n")
    (co / "data" / "stub").mkdir()
    (co / ".git" / "info").mkdir(exist_ok=True)
    (co / ".git" / "info" / "exclude").write_text("data\n")
    return co, str(runs / TAG)


def _observe(runs, root):
    status = run(runs, "status", root)
    digest = run(runs, "digest", root)
    assert status.returncode == 0 and digest.returncode == 0, (status.stdout, digest.stdout)
    tree, records = parse_status(status.stdout), parse_digest(digest.stdout)
    assert tree.observed and records is not None
    return TreeState(True, tree.head, tree.entries, digest=records)


@pytest.mark.sandbox
def test_the_digest_sees_inside_an_ignored_directory(runs):
    co, root = _harness_checkout(runs)
    allow = build(["data"], None)
    base = _observe(runs, root)
    assert "! data/" in base.entries and "x d data/stub" in base.entries
    assert sum(e.startswith("h 100644 ") for e in base.digest) == 2
    assert allow.refusal(base) is None
    assert build(["dat"], None).refusal(base) is not None  # "dat" is no segment of "data"
    assert run(runs, "write", root, "data/x.dat", stdin=b"one\nTWO\n").returncode == 0
    after = _observe(runs, root)
    assert after.entries == base.entries  # the status alone does not see the write
    hashed = compress(base.digest, roots(base), base)
    assert compress(after.digest, roots(base), after) != hashed  # the digest does
    assert list((runs / TAG / "q").iterdir()) == []  # its temp dir is removed


@pytest.mark.sandbox
def test_the_digest_shows_a_symlink_and_the_start_refuses_it(runs):
    co, root = _harness_checkout(runs)
    (co / "data" / "link").symlink_to(runs / ("f" * 32) / "secret.txt")  # outside the checkout
    base = _observe(runs, root)
    assert any(e.startswith("h 120000 ") and e.endswith(" data/link") for e in base.digest)
    assert "h 'data/link'" in (build(["data"], None).refusal(base) or "")


@pytest.mark.sandbox
def test_a_nested_repository_inside_the_list_is_never_observed_clean(runs):
    co, root = _harness_checkout(runs)
    nested = ["git", "-C", str(co / "data" / "sub"), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*nested, "init", "-q"], check=True)
    subprocess.run([*nested, *NO_AUTO_MAINTENANCE, "commit", "-q", "--allow-empty", "-m", "n"])
    digest = run(runs, "digest", root)
    records = parse_digest(digest.stdout) if digest.returncode == 0 else None
    if records is not None:  # git staged it as a gitlink: the mode refuses it
        base = TreeState(True, "a" * 40, digest=records)
        assert build(["data"], None).refusal(base) is not None
    assert list((runs / TAG / "q").iterdir()) == []


@pytest.mark.sandbox
def test_a_tracked_change_inside_the_list_still_refuses(runs):
    co, root = _harness_checkout(runs)
    (co / "sub" / "keep.txt").write_text("changed\n")
    reason = build(["data", "sub"], None).refusal(_observe(runs, root))
    assert reason is not None and reason.startswith("entry not allowed: 1 ")


# -- the canary binding: the pre-#261 wrapper serves runs without a list ------------------------
def test_the_previous_wrapper_is_accepted_only_without_a_list(tmp_path, monkeypatch):
    old = b"#!/bin/sh\n# the wrapper before the digest query\n"
    monkeypatch.setattr(corpus_io, "PREVIOUS_WRAPPER_SHA256", hashlib.sha256(old).hexdigest())
    path, wrapper, out = canary_report(tmp_path, old)
    corpus_io.verify_canary(path, out, wrapper=wrapper, host="mini")
    with pytest.raises(corpus_io.InputRefused, match="differs from this repository"):
        corpus_io.verify_canary(path, out, wrapper=wrapper, host="mini", require_current=True)


def test_the_current_wrapper_is_accepted_with_a_list(tmp_path):
    path, wrapper, out = canary_report(tmp_path, corpus_io.REPO_WRAPPER.read_bytes())
    corpus_io.verify_canary(path, out, wrapper=wrapper, host="mini", require_current=True)


def test_another_wrapper_is_refused_either_way(tmp_path):
    path, wrapper, out = canary_report(tmp_path, b"#!/bin/sh\n")
    for strict in (False, True):
        with pytest.raises(corpus_io.InputRefused):
            corpus_io.verify_canary(path, out, wrapper=wrapper, host="mini", require_current=strict)


def test_the_pinned_previous_wrapper_has_no_digest_and_this_one_does():
    assert corpus_io.REPO_WRAPPER.read_bytes().count(b"digest") > 0
    assert len(corpus_io.PREVIOUS_WRAPPER_SHA256) == 64
    assert hashlib.sha256(corpus_io.REPO_WRAPPER.read_bytes()).hexdigest() != (
        corpus_io.PREVIOUS_WRAPPER_SHA256
    )


def test_the_wrapper_before_pytest_ro_still_serves_every_run(tmp_path, monkeypatch):
    old = b"#!/bin/sh\n# the wrapper before pytest-ro\n"
    monkeypatch.setattr(corpus_io, "PRE_PYTEST_RO_WRAPPER_SHA256", hashlib.sha256(old).hexdigest())
    path, wrapper, out = canary_report(tmp_path, old)
    corpus_io.verify_canary(path, out, wrapper=wrapper, host="mini", require_current=True)


def test_the_pinned_pre_pytest_ro_wrapper_is_not_this_one():
    assert corpus_io.REPO_WRAPPER.read_bytes().count(b"pytest-ro)") == 2  # parsed, then run
    assert len(corpus_io.PRE_PYTEST_RO_WRAPPER_SHA256) == 64
    assert hashlib.sha256(corpus_io.REPO_WRAPPER.read_bytes()).hexdigest() != (
        corpus_io.PRE_PYTEST_RO_WRAPPER_SHA256
    )
