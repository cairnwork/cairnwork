"""CW_DEBUG_KEEP_CANDIDATE_DIR copies the candidate only when set."""

import subprocess
from types import SimpleNamespace

from experimental.local_relay.debug_keep import ENV_VAR, keep_candidate


def _result(tmp_path):
    repo = tmp_path / "work" / "checkout"
    repo.mkdir(parents=True)
    (repo / "a.txt").write_text("x\n")
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "b"], check=True)
    base = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("y\n")
    return SimpleNamespace(checkout=str(repo), base_sha=base)


def test_keep_candidate_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    keep_candidate(_result(tmp_path))
    assert not (tmp_path / "keep").exists()


def test_keep_candidate_copies_tree_base_and_patch(tmp_path, monkeypatch):
    res = _result(tmp_path)
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "keep"))
    keep_candidate(res)
    (d,) = (tmp_path / "keep").iterdir()
    assert (d / "tree" / "a.txt").read_text() == "y\n"
    assert (d / "base.txt").read_text().strip() == res.base_sha
    assert b"+y" in (d / "candidate.patch").read_bytes()
