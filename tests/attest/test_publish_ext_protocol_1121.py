"""publish._git keeps git's ext:: transport off even when the caller's GIT_CONFIG_COUNT/KEY/VALUE
(which publish passes through for the notes push credential helper) or the repo enable it
(#1106, #1121)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork_attest.publish import _git


@pytest.mark.parametrize("key", ["protocol.ext.allow", "protocol.allow"])
def test_env_config_cannot_enable_ext_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", key, "always"], check=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", key)
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    marker = tmp_path / "MARKER"
    # ext:: splits its command on spaces; "% " is a literal space (git-remote-ext(1)).
    command = f"touch {marker}".replace(" ", "% ")
    result = _git(repo, "fetch", f"ext::sh -c {command}")
    assert result.returncode != 0
    assert not marker.exists()
