"""A configured log.showSignature does not add signature-check output, or run gpg, when
gitcmd reads a commit message (#1126)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork.gitcmd import run_git, run_git_bytes

_MESSAGE = "subject line\n\nbody line\n"


def _signed_looking_repo(tmp_path: Path, scope: str) -> tuple[Path, Path]:
    """A repo with one commit whose config turns showSignature on and points gpg.program at a
    fake that leaves a marker and prints a line, as gpg would on a signature check."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            _MESSAGE,
        ],
        check=True,
    )
    # Give the commit a (bogus) gpgsig header: git only runs gpg for a commit that has one.
    raw = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    head, _, message = raw.partition("\n\n")
    sig = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQ==\n -----END PGP SIGNATURE-----"
    signed = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=f"{head}\n{sig}\n\n{message}",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", "HEAD", signed], check=True)
    marker = tmp_path / "GPG_RAN"
    fake = tmp_path / "fake-gpg.sh"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\necho 'gpg: injected line' >&2\nexit 1\n")
    fake.chmod(0o755)
    where = ["config", "--global"] if scope == "global" else ["-C", str(repo), "config"]
    for key, value in (("log.showSignature", "true"), ("gpg.program", str(fake))):
        subprocess.run(["git", *where, key, value], check=True)
    return repo, marker


@pytest.mark.parametrize("scope", ["repo", "global"])
def test_commit_message_reads_stay_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    if scope == "global":  # a throwaway global config, never the operator's own
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
    repo, marker = _signed_looking_repo(tmp_path, scope)
    # the two read shapes from #1126: binding_extras (log -1 --format=%B) and
    # issue_runner (show -s --format=%B)
    logged = run_git_bytes(repo, "log", "-1", "--format=%B", "HEAD", "--").decode()
    shown = run_git(repo, "show", "-s", "--format=%B", "HEAD")
    assert logged.strip() == _MESSAGE.strip()
    assert shown.strip() == _MESSAGE.strip()
    assert not marker.exists()
