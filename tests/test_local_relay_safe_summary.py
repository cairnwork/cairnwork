"""Opt-in: a plan that trips the secret scan reaches the gate only as a text-free summary."""

import json

from experimental.local_relay import safe_summary
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


def _run(tmp_path, monkeypatch, text, *, masking=False, accept_passes=False):
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
            masked_accept_passes=accept_passes,
        ),
    )
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    return result, gate, rows


def _leaks(sent, original):
    """Original lines (4+ chars) found in what was sent."""
    return [ln for ln in original.splitlines() if len(ln.strip()) >= 4 and ln.strip() in sent]


def test_flag_off_keeps_block_and_exact_text(tmp_path, monkeypatch):
    result, gate, rows = _run(tmp_path, monkeypatch, "before\n" + SECRET_LINE, masking=False)
    assert result.route == "FAILED"
    assert all(s["gate_reason"] == Reason.PAUSE_SECRET_BLOCKED for s in result.stages)
    assert gate.calls == [] and gate.seen == ["before\n" + SECRET_LINE] * 3
    assert SECRET_LINE not in json.dumps(rows)


def test_flag_on_sends_a_summary_and_its_accept_is_masked_accept(tmp_path, monkeypatch):
    text = "before\n" + SECRET_LINE + "\nafter"
    result, gate, rows = _run(tmp_path, monkeypatch, text, masking=True)
    assert len(gate.calls) >= 1
    sent = gate.calls[0]
    assert _leaks(sent, text) == [] and SECRET not in sent
    assert "withheld" in sent and "api_key" in sent
    stage = result.stages[0]
    assert stage["plan_withheld"] is True and "api_key" in stage["scan_categories"]
    assert stage["gate_reason"] == "MASKED_ACCEPT"  # fail closed: a summary ACCEPT is not ACCEPT
    assert result.route != "LOCAL_FIRST"
    contents = json.dumps({"result": result.__dict__, "stages": result.stages, "rows": rows})
    assert SECRET not in contents and SECRET_LINE not in contents


def test_accept_of_a_withheld_plan_passes_only_with_the_explicit_flag(tmp_path, monkeypatch):
    text = "before\n" + SECRET_LINE + "\nafter"
    result, _gate, _rows = _run(tmp_path, monkeypatch, text, masking=True, accept_passes=True)
    assert result.route == "LOCAL_FIRST"
    assert result.stages[0]["plan_withheld"] is True


def test_flag_on_leaves_secret_free_text_unchanged(tmp_path, monkeypatch):
    text = "ordinary review text\nsecond line"
    result, gate, _rows = _run(tmp_path, monkeypatch, text, masking=True)
    assert result.route == "LOCAL_FIRST" and gate.calls == [text]
    assert result.stages[0]["plan_withheld"] is False


def test_summary_names_files_and_test_results_only():
    plan = (
        "intro words\ndiff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        f"+x = 1\n+{SECRET_LINE}\n-y = 2\nNew file docs/new.md:\nhello\n"
    )
    raw = "PASSED tests/test_a.py::test_one\nFAILED tests/test_a.py::test_two - boom " + SECRET
    raw += "\n1 failed, 1 passed in 0.1s\n"
    sent, categories = safe_summary.guard_plan(plan, raw)
    assert sent is not None and categories
    assert "files changed: 2" in sent and "diff lines: +2 -1" in sent
    assert "failed=1, passed=1" in sent
    for original in ("src/app.py", "docs/new.md", "test_a.py", "test_two", "test_one"):
        assert original not in sent  # counts and categories only: no file or test names
    assert SECRET not in sent and "boom" not in sent and "intro" not in sent and "hello" not in sent


def test_pem_with_malformed_headers_never_reaches_the_gate():
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
    for header in (
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY----",
        "-----BEGIN RSA PRIVATE KEY",
        "----BEGIN OPENSSH PRIVATE KEY-----",
        "-----  BEGIN   EC PRIVATE KEY  -----",
    ):
        plan = f"before\n{header}\n{body}\nafter\n"  # no END line at all
        sent, categories = safe_summary.guard_plan(plan, "")
        assert categories, header
        assert sent is None or (body not in sent and "before" not in sent and "after" not in sent)


def test_text_after_a_prefix_on_the_next_line_never_reaches_the_gate():
    for plan in (
        "before\nGITHUB_TOKEN=\n" + "a" * 24 + " and more words\nafter",
        "see api_key:\nplain-looking-continuation-line more words\nafter",
        f"{SECRET_LINE}\ntext that follows the hit\nafter",
    ):
        sent, categories = safe_summary.guard_plan(plan, "")
        assert categories
        assert sent is None or _leaks(sent, plan) == []


def test_key_split_over_several_lines_never_reaches_the_gate():
    key = "sk-ant-api03-" + "Ab1" * 14
    splits = (
        [key[:20], key[20:]],
        [key[:15], key[15:30], key[30:]],
        [key[:12], key[12:24], key[24:36], key[36:]],
        [key[:20], "", "  ", key[20:]],
    )
    for parts in splits:
        plan = "x\n" + "\n".join(parts) + "\ny\n"
        sent, categories = safe_summary.guard_plan(plan, "")
        assert categories
        assert sent is None or not any(p.strip() and p.strip() in sent for p in parts)


def test_pieces_that_only_match_once_joined_never_reach_the_gate():
    key = "ghp_" + "A" * 36  # neither piece matches alone: only the joined run does
    for parts in ([key[:8], key[8:]], [key[:8], key[8:20], key[20:]], [key[:8], "", key[8:]]):
        assert not any(Scanner().scan(part) for part in parts)
        plan = "intro words\n" + "\n".join(parts) + "\nouter line\n"
        sent, categories = safe_summary.guard_plan(plan, "")
        assert categories
        assert sent is None or not any(p.strip() and p.strip() in sent for p in parts)


def test_a_hit_that_only_a_single_line_scan_sees_still_withholds(monkeypatch):
    def line_only(self, text):
        return ["line_rule"] if "\n" not in text and "MARKER-LINE" in text else []

    monkeypatch.setattr(Scanner, "scan", line_only)
    sent, categories = safe_summary.guard_plan("a\nMARKER-LINE here\nb\n", "")
    assert categories == ["line_rule"] and sent is not None and "MARKER" not in sent


def test_file_and_test_names_never_appear_even_when_harmless():
    plan = f"diff --git a/ok.py b/ok.py\n+y\n{SECRET_LINE}\n"
    raw = f"FAILED tests/test_{SECRET}.py::t\nPASSED tests/test_ok.py::t\n1 failed, 1 passed\n"
    sent, _categories = safe_summary.guard_plan(plan, raw)
    assert sent is not None and SECRET not in sent
    for original in ("ok.py", "test_ok", "tests/"):
        assert original not in sent


def test_residual_scan_refuses_a_summary_that_still_matches(monkeypatch):
    """Mutation guard: without the scan of the summary itself, this leak would be sent."""
    leaky = f"files changed: 1\n{SECRET_LINE}\n"
    monkeypatch.setattr(safe_summary, "_summary", lambda *_a, **_k: leaky)
    assert safe_summary.guard_plan(SECRET_LINE, "") == (None, ["api_key", "assigned_secret"])


def test_scanner_exception_sends_a_summary_never_the_text(monkeypatch):
    def boom(self, _text):
        raise RuntimeError("scan failed " + SECRET)

    monkeypatch.setattr(Scanner, "scan", boom)
    assert safe_summary.scan_categories("anything\n") == ["scanner_error"]
    sent, categories = safe_summary.guard_plan("plain words only\n", "")
    assert (
        categories == ["scanner_error"] and sent is None
    )  # even the summary cannot be vouched for


def test_unsendable_plan_is_rejected_without_calling_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_summary, "_summary", lambda *_a, **_k: SECRET_LINE)
    result, gate, rows = _run(tmp_path, monkeypatch, SECRET_LINE, masking=True)
    assert gate.calls == [] and gate.seen == []
    assert result.stages[0]["gate_called"] is False
    assert result.stages[0]["gate_reason"] == Reason.PAUSE_SECRET_BLOCKED
    assert SECRET not in json.dumps(rows)


def test_masking_field_does_not_shift_positional_arguments():
    import dataclasses

    names = [f.name for f in dataclasses.fields(RelayConfig)]
    assert names[-2:] == ["mask_secret_lines", "masked_accept_passes"]


def test_masking_flag_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("RELAY_MASK_SECRET_LINES", "1")
    assert RelayConfig.from_env().mask_secret_lines is True
    monkeypatch.delenv("RELAY_MASK_SECRET_LINES")
    assert RelayConfig.from_env().mask_secret_lines is False
    assert RelayConfig.from_env().masked_accept_passes is False
    monkeypatch.setenv("RELAY_MASKED_ACCEPT_PASSES", "1")
    assert RelayConfig.from_env().masked_accept_passes is True
