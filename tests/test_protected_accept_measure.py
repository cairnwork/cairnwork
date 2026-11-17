from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from experimental.protected_accept import measure as measure_module
from experimental.protected_accept.corpus import ENTRIES


def test_classification_and_missing_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert measure_module._classify({"kind": "tamper"}, "PROTECTED_PASS") == "VERIFIED_WRONG"
    assert measure_module._classify({"kind": "tamper"}, "INCOMPLETE") == "tamper_blocked_incomplete"
    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: None)
    result = measure_module.measure([], tmp_path / "out.json", account=None)
    assert result["environment"] == "MISSING_ENV"
    assert "per_class_outcomes" in result


def test_tool_defect_is_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Backend:
        name = "test"

    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_ordinary", lambda *args: ("FAILED", 0.1))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated harness failure")

    monkeypatch.setattr(measure_module, "run_protected_once", broken)
    entry = next(item for item in ENTRIES if item["cls"] == "C1")
    result = measure_module.measure([entry], tmp_path / "out.json", account=cast(Any, object()))
    assert result["rows"][0]["classification"] == "TOOL_DEFECT"


def test_reuse_lock_measures_second_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Backend:
        name = "test"

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_ordinary", lambda *args: ("FAILED", 0.1))

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        run_id = kwargs.get("run_id", "")
        store = kwargs.get("store")
        calls.append((run_id, store))
        return type("Verdict", (), {"status": "REFUSED", "code": "DECLARATION_REUSED"})()

    monkeypatch.setattr(measure_module, "run_protected_once", fake_run)
    entry = next(item for item in ENTRIES if item.get("reuse_lock"))
    result = measure_module.measure([entry], tmp_path / "out.json", account=object())
    assert len(calls) == 2 and calls[0][1] is calls[1][1]
    assert result["rows"][0]["protected_code"] == "DECLARATION_REUSED"


def test_timeout_passes_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Backend:
        name = "test"

    seen: list[int | None] = []
    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_ordinary", lambda *args: ("FAILED", 0.1))

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("timeout"))
        return type("Verdict", (), {"status": "INCOMPLETE", "code": "TIMEOUT"})()

    monkeypatch.setattr(measure_module, "run_protected_once", fake_run)
    entry = next(item for item in ENTRIES if item.get("timeout"))
    result = measure_module.measure([entry], tmp_path / "out.json", account=object())
    assert seen == [10]
    assert result["rows"][0]["protected_code"] == "TIMEOUT"


def test_missing_account_skips_protected_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Backend:
        name = "test"

    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_ordinary", lambda *args: ("FAILED", 0.1))
    monkeypatch.setattr(
        measure_module, "run_protected_once", lambda *args, **kwargs: pytest.fail("must skip")
    )
    result = measure_module.measure([ENTRIES[0]], tmp_path / "out.json", account=None)
    assert result["rows"][0]["protected_code"] == "NEEDS_VERIFIER_ACCOUNT"
    assert result["rows"][0]["core_status"] == "FAILED"


def test_resalt_corpus_classifies_reused_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Backend:
        name = "test"

    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_ordinary", lambda *args: ("FAILED", 0.1))
    monkeypatch.setattr(
        measure_module,
        "run_protected_once",
        lambda *args, **kwargs: type(
            "Verdict", (), {"status": "REFUSED", "code": "ATTEMPT_REUSED", "checks": ()}
        )(),
    )
    entry = next(item for item in ENTRIES if item.get("resalt"))
    assert entry["cls"] == "C8" and entry["variant"] == 2
    result = measure_module.measure([entry], tmp_path / "resalt.json", account=object())
    assert result["rows"][0]["protected_code"] == "ATTEMPT_REUSED"
    assert result["rows"][0]["classification"] == "tamper_blocked"


def test_safe_removes_controls_and_neutralizes_directives() -> None:
    result = measure_module._safe("::error::x\n##[group]y\x1b[31m")
    assert "\n" not in result and "\x1b" not in result
    assert not result.startswith(("::", "##["))
    hidden = measure_module._safe("\u2028::warning::x\u202e")
    assert not hidden.startswith(("::", "##[")) and "\u202e" not in hidden


def test_ordinary_reads_core_status_and_uses_allowed_verifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        seen.append(cmd)
        return type("Done", (), {"stdout": '{"core_status": "VERIFIED", "status": "VERIFIED"}'})()

    monkeypatch.setattr(measure_module.subprocess, "run", fake_run)
    status, _ = measure_module._ordinary(tmp_path, tmp_path, "a" * 40, "b" * 40, "clamp", "py")
    assert status == "VERIFIED"
    assert "python3 -m pytest -q tests/test_visible.py" in seen[0]


def test_measure_state_accepts_work_and_account_python_is_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Backend:
        name = "test"

    class Account:
        python = "/account/venv/bin/python"

    captured: dict[str, Any] = {}

    def fake_one(entry, index, parent, state, account, backend, py):
        captured.update(accepting=(state / "ACCEPTING").exists(), py=py)
        return {"known_residual": False, "kind": "honest", "cls": "", "classification": "ok"}, None

    monkeypatch.setattr(measure_module.cairnwork_sandbox, "select", lambda: Backend())
    monkeypatch.setattr(measure_module, "_measure_one", fake_one)
    measure_module.measure([ENTRIES[0]], tmp_path / "out.json", account=Account())
    assert captured == {"accepting": True, "py": "/account/venv/bin/python"}
