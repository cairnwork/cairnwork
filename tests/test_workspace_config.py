from conftest import git

from cairnwork.workspace import create_attempt_clone

WORKER = ("-c", "user.email=w@example.invalid", "-c", "user.name=w")


def test_verify_checkout_ignores_operator_git_filters(repo, tmp_path, monkeypatch):
    """The checkout of a candidate reads no global or system git config: a filter set there
    is not run for the candidate's .gitattributes."""
    marker = tmp_path / "filter-ran"
    script = tmp_path / "smudge.sh"
    script.write_text(f"#!/bin/sh\necho ran >> '{marker}'\ncat\n")
    script.chmod(0o755)
    home = tmp_path / "home"  # git strips GIT_* from its env, so the operator's file is $HOME's
    home.mkdir()
    (home / ".gitconfig").write_text(f'[filter "probe"]\n\tsmudge = "{script}"\n')
    work = tmp_path / "work"
    git(repo, "clone", "-q", "--no-local", str(repo), str(work))
    (work / ".gitattributes").write_text("* filter=probe\n")
    git(work, "add", "-A")
    git(work, *WORKER, "commit", "-q", "-m", "candidate with attributes")
    candidate = git(work, "rev-parse", "HEAD")
    git(repo, "fetch", "-q", str(work), candidate)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    made = create_attempt_clone(repo, candidate, tmp_path / "attempts", "f1")
    assert made.path.is_dir()
    assert not marker.exists()
