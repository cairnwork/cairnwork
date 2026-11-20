"""Opt-in secret-line masking stays inside the experimental relay."""

import json

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.ledger import JsonlLedger
from experimental.local_relay.relay import Acceptance, Attempt, run_relay
from experimental.pause.interfaces import GateReply
from experimental.pause.schema import Reason
from experimental.pause.secrets_scan import Scanner

SECRET = "ghp_" + "A" * 30
SECRET_LINE = f"GITHUB_TOKEN={SECRET} trailing-sensitive-text"


class ScanningGate:
    def __init__(self):
        self.scanner = Scanner()
        self.calls = []
        self.seen = []

    def set_stage(self, _stage):
        pass

    def review(self, request):
        self.seen.append(request.plan)
        matches = self.scanner.scan(request.plan)
        if matches:
            return GateReply(None, "haiku", "medium", Reason.PAUSE_SECRET_BLOCKED)
        self.calls.append(request.plan)
        return GateReply("ACCEPT", "haiku", "medium", None)


def _run(tmp_path, monkeypatch, text, *, masking=False):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    monkeypatch.setenv("RELAY_MASK_SECRET_LINES", "1" if masking else "0")
    gate = ScanningGate()
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlLedger(ledger_path)
    result = run_relay(
        "task",
        lambda _context: Attempt("candidate", "local"),
        lambda _candidate: Acceptance(True, "accepted"),
        gate,
        lambda _task: Attempt("fallback", "cloud"),
        ledger,
        RelayConfig.from_env() | {}
        if False
        else RelayConfig(
            enabled=True,
            plan_for=lambda _candidate: text,
            mask_secret_lines=masking,
        ),
    )
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    return result, gate, rows


def test_masking_off_preserves_block_and_exact_text(tmp_path, monkeypatch):
    result, gate, rows = _run(tmp_path, monkeypatch, "before\n" + SECRET_LINE, masking=False)
    assert result.route == "FAILED"
    assert all(s["gate_reason"] == Reason.PAUSE_SECRET_BLOCKED for s in result.stages)
    assert gate.calls == [] and gate.seen == ["before\n" + SECRET_LINE] * 3
    assert SECRET_LINE not in json.dumps(rows)


def test_masking_on_sends_placeholder_and_preserves_other_lines(tmp_path, monkeypatch):
    text = "before\n" + SECRET_LINE + "\nafter"
    result, gate, _rows = _run(tmp_path, monkeypatch, text, masking=True)
    assert result.route == "LOCAL_FIRST"
    assert gate.calls == ["before\n[line masked: api_key, assigned_secret]\nafter"]
    assert SECRET not in gate.calls[0] and "trailing-sensitive-text" not in gate.calls[0]


def test_masking_on_never_persists_original_secret(tmp_path, monkeypatch):
    result, gate, rows = _run(tmp_path, monkeypatch, "before\n" + SECRET_LINE, masking=True)
    contents = json.dumps({"result": result.__dict__, "stages": result.stages, "rows": rows})
    assert SECRET not in contents and SECRET_LINE not in contents
    assert gate.calls == ["before\n[line masked: api_key, assigned_secret]"]
    stage = result.stages[0]
    assert stage["masked_line_count"] == 1
    assert stage["masked_categories"] == ["api_key", "assigned_secret"]


def test_masking_on_fails_closed_when_residual_scan_matches(tmp_path, monkeypatch):
    residual = "before\nGITHUB_TOKEN=\n" + "a" * 24
    result, gate, _rows = _run(tmp_path, monkeypatch, residual, masking=True)
    assert gate.calls == []
    assert all(s["gate_reason"] == Reason.PAUSE_SECRET_BLOCKED for s in result.stages)
    assert all(s["gate_called"] is False for s in result.stages)


def test_masking_on_leaves_secret_free_text_unchanged(tmp_path, monkeypatch):
    text = "ordinary review text\nsecond line"
    result, gate, _rows = _run(tmp_path, monkeypatch, text, masking=True)
    assert result.route == "LOCAL_FIRST" and gate.calls == [text]
