"""#1701: failure text printed by the relay CLI and stored by the runtime is redacted, paths too."""

from __future__ import annotations

import json
import subprocess

import pytest

from experimental.local_relay import cli, runtime
from experimental.local_relay.diagnostics import redact, redacted_tail

PATH_MARK = "/srv/uniq-1701-zq/private-dir/file.txt"
TOKEN_MARK = "ghp_UNIQ1701zqTOKENxxxxxxxx"


def test_redact_hides_absolute_paths_but_not_urls_or_relative_paths():
    text = (
        f"open {PATH_MARK}: denied; see https://example.org/a/b "
        f"and src/pkg/mod.py (x=({PATH_MARK}))"
    )
    out = redact(text)
    assert "uniq-1701-zq" not in out
    assert "https://example.org/a/b" in out
    assert "src/pkg/mod.py" in out


def test_redacted_tail_hides_paths_too():
    assert "uniq-1701-zq" not in redacted_tail(f"a\n{PATH_MARK}\n", 5, 500)


def _run_failing(monkeypatch, capsys, exc, extra=()):
    def boom(_args, _ledger):
        raise exc

    code = cli.main(
        ["--state", "s", "--repo", "r", "--clone", "c", "--ledger", "l.jsonl", *extra, "run-once"],
        run_once=boom,
    )
    return code, capsys.readouterr().out


def test_cli_run_failed_detail_is_redacted(monkeypatch, capsys):
    monkeypatch.setenv(cli.FLAG, "1")
    exc = RuntimeError(f"failed at {PATH_MARK} with token={TOKEN_MARK}")
    code, out = _run_failing(monkeypatch, capsys, exc)
    assert code == 1
    assert json.loads(out)["error"] == "RUN_FAILED"
    assert "uniq-1701-zq" not in out and "UNIQ1701zq" not in out


def test_cli_dry_route_failure_detail_is_redacted(monkeypatch, capsys):
    def boom(_args):
        raise RuntimeError(f"no fallback at {PATH_MARK} token={TOKEN_MARK}")

    monkeypatch.setattr(cli, "dry_route", boom)
    code = cli.main(["--state", "s", "--repo", "r", "--clone", "c", "--dry-route", "run-once"])
    out = capsys.readouterr().out
    assert code == 1 and "RUN_FAILED" in out
    assert "uniq-1701-zq" not in out and "UNIQ1701zq" not in out


def test_git_failure_text_is_redacted(monkeypatch):
    fake = subprocess.CompletedProcess([], 128, "", f"fatal: {PATH_MARK} token={TOKEN_MARK}")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: fake)
    with pytest.raises(RuntimeError) as err:
        runtime._git(runtime.Path("x"), "status")
    assert "uniq-1701-zq" not in str(err.value) and "UNIQ1701zq" not in str(err.value)
