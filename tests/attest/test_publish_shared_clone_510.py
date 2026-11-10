"""Shared-clone publishing races from Issue #510."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import git
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from publish_helpers import (
    _NOTES_REF,
    _make_bundle_bytes,
    _read_note_from_bare,
    _remote_tip,
    _write_bundle,
    make_publish_env,
)

from cairnwork_attest.publish import _git, publish_bundle

pytestmark = pytest.mark.usefixtures("production_git_env")


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _second_candidate(src: Path) -> str:
    (src / "second.txt").write_text("second\n")
    git(src, "add", "second.txt")
    git(src, "commit", "-m", "second")
    return git(src, "rev-parse", "HEAD")


def test_push_uses_captured_note_commit_when_shared_ref_moves(
    tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    src, bare, first_candidate = make_publish_env(tmp_path)
    first_bundle = _write_bundle(tmp_path, _make_bundle_bytes(first_candidate, key), "first.json")
    assert publish_bundle(first_bundle, src, first_candidate).code == "PUBLISHED"
    remote_tip = _remote_tip(bare)
    assert remote_tip is not None

    candidate = _second_candidate(src)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key), "second.json")
    moved = False

    def moving_run(repo: Path, *args: str, input: bytes | None = None):  # noqa: ANN202
        nonlocal moved
        if args[:1] == ("push",) and not moved:
            moved = True
            _git(repo, "update-ref", _NOTES_REF, remote_tip)
        return _git(repo, *args, input=input)

    result = publish_bundle(bundle, src, candidate, run=moving_run)
    assert result.code == "PUBLISHED"
    assert result.notes_tip is not None and result.notes_tip != remote_tip
    assert _remote_tip(bare) == result.notes_tip
    assert _read_note_from_bare(bare, candidate).encode() == bundle.read_bytes()


def test_unpushed_shared_note_does_not_make_noop(tmp_path: Path, key: Ed25519PrivateKey) -> None:
    src, bare, candidate = make_publish_env(tmp_path)
    first = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key), "first.json")
    assert publish_bundle(first, src, candidate).code == "PUBLISHED"
    candidate = _second_candidate(src)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key))
    blob = _git(src, "hash-object", "-w", "--no-filters", "--stdin", input=bundle.read_bytes())
    assert blob.returncode == 0
    injected = False

    def injecting_run(repo: Path, *args: str, input: bytes | None = None):  # noqa: ANN202
        nonlocal injected
        result = _git(repo, *args, input=input)
        if args[:1] == ("fetch",) and not injected:
            injected = True
            _git(repo, "notes", f"--ref={_NOTES_REF}", "add", "-C", blob.stdout.strip(), candidate)
        return result

    result = publish_bundle(bundle, src, candidate, run=injecting_run)
    assert result.code == "PUBLISHED"
    assert _read_note_from_bare(bare, candidate).encode() == bundle.read_bytes()


def test_normal_publish_and_remote_note_noop(tmp_path: Path, key: Ed25519PrivateKey) -> None:
    src, bare, candidate = make_publish_env(tmp_path)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key))
    published = publish_bundle(bundle, src, candidate)
    assert published.code == "PUBLISHED"
    tip = _remote_tip(bare)
    assert tip == published.notes_tip

    again = publish_bundle(bundle, src, candidate)
    assert (again.code, again.notes_tip) == ("NOOP", tip)


def _mktree(repo: Path, entry: str) -> str:
    proc = _git(repo, "mktree", input=(entry + "\n").encode())
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_note_at_a_deeper_fanout_is_found(tmp_path: Path, key: Ed25519PrivateKey) -> None:
    """git resolves any notes fanout depth; an ls-tree of two paths would miss aa/bb/rest."""
    src, bare, candidate = make_publish_env(tmp_path)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key))
    blob = _git(src, "hash-object", "-w", "--no-filters", "--stdin", input=bundle.read_bytes())
    leaf = _mktree(src, f"100644 blob {blob.stdout.strip()}\t{candidate[4:]}")
    mid = _mktree(src, f"040000 tree {leaf}\t{candidate[2:4]}")
    root = _mktree(src, f"040000 tree {mid}\t{candidate[:2]}")
    commit = git(src, "commit-tree", root, "-m", "deep fanout note")
    git(src, "push", "origin", f"{commit}:{_NOTES_REF}")

    result = publish_bundle(bundle, src, candidate)
    assert (result.code, result.notes_tip) == ("NOOP", commit)

    other_key = Ed25519PrivateKey.generate()
    other = _write_bundle(tmp_path, _make_bundle_bytes(candidate, other_key), "other.json")
    conflict = publish_bundle(other, src, candidate)
    assert conflict.code == "NOTE_CONFLICT"
    assert _remote_tip(bare) == commit


def test_local_notes_ref_untouched_and_private_ref_removed(
    tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    src, bare, candidate = make_publish_env(tmp_path)
    tree = git(src, "rev-parse", "HEAD^{tree}")
    git(src, "notes", f"--ref={_NOTES_REF}", "add", "-m", "local only", tree)
    local_tip = git(src, "rev-parse", _NOTES_REF)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key))

    assert publish_bundle(bundle, src, candidate).code == "PUBLISHED"
    assert git(src, "rev-parse", _NOTES_REF) == local_tip
    refs = git(src, "for-each-ref", "--format=%(refname)", "refs/notes/")
    assert refs.split() == [_NOTES_REF]
    assert _read_note_from_bare(bare, candidate).encode() == bundle.read_bytes()


def test_cleanup_failure_after_a_push_keeps_published(
    tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    """The remote already holds the note; a leftover private ref is reported, not a failure."""
    src, bare, candidate = make_publish_env(tmp_path)
    bundle = _write_bundle(tmp_path, _make_bundle_bytes(candidate, key))

    def locked_run(repo: Path, *args: str, input: bytes | None = None):  # noqa: ANN202
        if args[:2] == ("update-ref", "-d"):
            return subprocess.CompletedProcess(args, 1, "", "ref locked\n")
        return _git(repo, *args, input=input)

    result = publish_bundle(bundle, src, candidate, run=locked_run)
    assert (result.ok, result.code, result.notes_tip) == (True, "PUBLISHED", _remote_tip(bare))
    assert result.detail is not None and "not deleted: ref locked" in result.detail
    assert _read_note_from_bare(bare, candidate).encode() == bundle.read_bytes()
