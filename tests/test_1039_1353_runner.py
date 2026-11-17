"""Fail-closed checks for the isolated #1353 witness consumer."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable

import pra1353_consume as consume
import pytest
from test_1039_1353_witnesses import PINS


def _report(seqs: Iterable[int] = range(56)):
    return {"rows": [{"seq": seq} for seq in seqs], "loaded_test_modules": []}


def _runner(report=None, *, code=0, stdout=None, error=""):
    content = json.dumps(report if report is not None else _report()) if stdout is None else stdout

    def call(args, **kwargs):
        return subprocess.CompletedProcess(args, code, content, error)

    return call


def test_pin_mismatch_fails_before_runner(monkeypatch):
    calls = []

    def never_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner started despite pin mismatch")

    with pytest.raises(ValueError, match="pin mismatch"):
        consume.launch({"tests/pra1353_runner.py": "0" * 64}, run=never_run)
    assert calls == []


def test_pin_mismatch_mutation_is_detected(monkeypatch):
    monkeypatch.setattr(consume, "check_pins", lambda _pins: None)
    assert consume.launch({"bad": "pin"}, run=_runner())["rows"]


def test_runner_nonzero_exit_fails_consumer():
    with pytest.raises(ValueError, match="runner exited"):
        consume.launch(PINS, run=_runner(code=2, error="boom"))


def test_runner_timeout_fails_consumer():
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, timeout=240)

    with pytest.raises(subprocess.TimeoutExpired):
        consume.launch(PINS, run=timeout)


def test_garbage_output_fails_consumer():
    with pytest.raises(ValueError, match="not JSON"):
        consume.launch(PINS, run=_runner(stdout="not-json"))


def test_missing_seq_fails_consumer():
    report = _report(range(55))
    with pytest.raises(ValueError, match="row count"):
        consume.launch(PINS, run=_runner(report))


def test_duplicate_seq_fails_consumer():
    report = _report(list(range(55)) + [54])
    with pytest.raises(ValueError, match="sequence set"):
        consume.launch(PINS, run=_runner(report))


def test_extra_row_fails_consumer():
    with pytest.raises(ValueError, match="row count"):
        consume.launch(PINS, run=_runner(_report(range(57))))


@pytest.mark.parametrize(
    "seqs",
    [list(range(55)), list(range(55)) + [54], list(range(56)) + [56]],
    ids=["missing", "duplicate", "extra"],
)
def test_sequence_gate_mutation_would_accept_bad_exact_set(monkeypatch, seqs):
    monkeypatch.setattr(consume, "validate_rows", lambda _rows: None)
    report = consume.launch(PINS, run=_runner(_report(seqs)))
    actual = [row["seq"] for row in report["rows"]]
    assert len(actual) == len(seqs)
    assert set(actual) == set(seqs)


def test_runner_ignores_shadow_packages_and_planted_witness(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "cairnwork").mkdir()
    (shadow / "cairnwork/__init__.py").write_text("raise RuntimeError('shadow package imported')")
    (shadow / "tests").mkdir()
    (shadow / "tests/_w1353_probe.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).parent.parent.joinpath('planted-imported').write_text('PLANTED-1353')\n"
        "observed = 'PLANTED-1353'\n"
        "def core_rows(): return [{'observed': observed}]"
    )
    report = consume.launch(
        PINS,
        cwd_override=shadow,
        extra_env={"PYTHONPATH": str(shadow)},
    )
    assert len(report["rows"]) == 56
    assert "PLANTED-1353" not in json.dumps(report)
    assert not (shadow / "planted-imported").exists()
    assert "_w1353_probe" not in report["loaded_test_modules"]
    assert all(not name.startswith("_w1353_") for name in report["loaded_test_modules"])
