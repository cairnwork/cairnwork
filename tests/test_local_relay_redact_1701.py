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


def test_file_urls_are_masked():
    out = redact("open file:///srv/uniq-1701-zq/x.txt failed")
    assert "uniq-1701-zq" not in out and out.endswith("failed")


def test_home_paths_with_spaces_are_masked_whole():
    out = redact("cannot open /Users/Uniq1701 Zq/Docs Dir/a.txt")
    assert "Uniq1701" not in out and "Zq" not in out and "Docs" not in out
    out = redact("cannot open /home/uniq1701 zq/a.txt\nnext line /ok")
    assert "uniq1701" not in out and "zq" not in out and "next line" in out


def test_error_reason_survives_after_a_path():
    out = redact("/Users/uniq1701/x: permission denied")
    assert "uniq1701" not in out and out.endswith("permission denied")


@pytest.mark.parametrize(
    "text",
    [
        "open file:/srv/uniq-1701-zq/x failed",
        "open FILE:///srv/uniq-1701-zq/x failed",
        "open /Volumes/Uniq 1701 Zq/proj/a.txt",
        "open /opt/uniq 1701 zq/x.txt",
    ],
)
def test_more_path_forms_are_masked(text):
    out = redact(text)
    assert "uniq" not in out.lower() and "1701" not in out and "Zq" not in out


def test_adapter_and_setup_sources_redact_their_exception_text(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from experimental.local_relay import adapters, worker_setup
    from experimental.local_relay.adapters import HaikuProxyFallback, LocalAttemptFailed
    from experimental.local_relay.gate_plan import PlanIncomplete
    from experimental.local_worker.endpoint import EndpointUnreachable

    def plan_fail(*_a):
        raise PlanIncomplete(f"bad {PATH_MARK}")

    monkeypatch.setattr(adapters, "changed_paths", plan_fail)
    result = SimpleNamespace(checkout=tmp_path, base_sha="a" * 40)
    with pytest.raises(RuntimeError) as one:
        adapters._changed_paths(result, tmp_path)

    def request(*_a):
        raise OSError(f"proxy {PATH_MARK}")

    with pytest.raises(LocalAttemptFailed) as two:
        adapters._proxy_call(request, "GET", "http://x", None)

    def attempt(*_a):
        raise EndpointUnreachable(f"worker {PATH_MARK}")

    fallback = HaikuProxyFallback("http://127.0.0.1:18301/v1", attempt, lambda *_a: {})
    with pytest.raises(LocalAttemptFailed) as three:
        fallback("t")

    def refuse(*_a, **_k):
        raise PermissionError(f"owner {PATH_MARK}")

    monkeypatch.setattr(worker_setup.os, "fchown", refuse)
    (tmp_path / "f").write_text("x")
    with pytest.raises(RuntimeError) as four:
        worker_setup.restore_judge_modes(tmp_path, frozenset())
    for err in (one, two, three, four):
        assert "uniq-1701-zq" not in str(err.value)


def test_cli_output_and_ledger_rows_are_redacted_end_to_end(monkeypatch, capsys, tmp_path):
    import dataclasses

    from experimental.local_relay.adapters import LocalAttemptFailed
    from experimental.local_relay.config import RelayConfig
    from experimental.local_relay.relay import run_relay

    monkeypatch.setenv(cli.FLAG, "1")
    ledger_path = tmp_path / "ledger.jsonl"

    def worker(_context):
        raise LocalAttemptFailed(f"local {PATH_MARK} token={TOKEN_MARK}")

    def fallback(_task):
        raise LocalAttemptFailed(f"fallback {PATH_MARK}")

    def run_once(_args, ledger):
        outcome = run_relay("t1", worker, None, None, fallback, ledger, RelayConfig(enabled=True))
        return dataclasses.asdict(outcome)

    code = cli.main(
        ["--state", "s", "--repo", "r", "--clone", "c", "--ledger", str(ledger_path), "run-once"],
        run_once=run_once,
    )
    out = capsys.readouterr().out
    assert code == 0 and "FAILED" in out
    stored = ledger_path.read_text()
    assert stored
    for text in (out, stored):
        assert "uniq-1701-zq" not in text and "UNIQ1701zq" not in text


def test_a_failing_ledger_write_does_not_leak_its_path(monkeypatch, capsys):
    import dataclasses

    from experimental.local_relay.adapters import LocalAttemptFailed
    from experimental.local_relay.config import RelayConfig
    from experimental.local_relay.relay import run_relay

    monkeypatch.setenv(cli.FLAG, "1")

    class BadLedger:
        def append(self, _row):
            raise OSError(13, "denied", PATH_MARK)

    def worker(_context):
        raise LocalAttemptFailed("x")

    def run_once(_args, _ledger):
        outcome = run_relay("t1", worker, None, None, None, BadLedger(), RelayConfig(enabled=True))
        return dataclasses.asdict(outcome)

    cli.main(
        ["--state", "s", "--repo", "r", "--clone", "c", "--ledger", "l.jsonl", "run-once"],
        run_once=run_once,
    )
    out = capsys.readouterr().out
    assert "LEDGER_ERROR" in out and "uniq-1701-zq" not in out
