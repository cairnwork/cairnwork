"""Fail-closed checks for the isolated #1353 witness consumer.

Mutant dispositions from the round-1 record:
- M10 is equivalent: the consumer's exact 56-sequence gate and row identity check reject
  missing, extra, or mismatched rows if the runner count check is removed.
- M11 is harmless: C8 full 40-hex normalization affects drill detail only; consumer
  expectations assert the stable mismatch code and keep the raw production result.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pra1353_consume as consume
import pytest
from pra1353_runner import _match_recipe
from test_1039_1353_witnesses import PINS


def _report(seqs: Iterable[int] = range(56)):
    return {"rows": [{"seq": seq} for seq in seqs], "modules": []}


def _runner(report=None, *, code=0, stdout=None, error=""):
    content = json.dumps(report if report is not None else _report()) if stdout is None else stdout

    def call(args, **kwargs):
        return subprocess.CompletedProcess(args, code, content, error)

    return call


def test_pin_mismatch_fails_before_runner(monkeypatch):
    calls = []

    def never_run(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("runner started despite pin mismatch")

    with pytest.raises(ValueError, match="pin mismatch"):
        consume.launch({"tests/pra1353_runner.py": "0" * 64}, run=never_run)
    assert calls == []


def test_pin_mismatch_mutation_is_detected(monkeypatch):
    monkeypatch.setattr(consume, "check_pins", lambda _pins: None)
    with pytest.raises(FileNotFoundError):
        consume.launch({"bad": "pin"}, run=_runner())


def test_runner_nonzero_exit_fails_consumer():
    with pytest.raises(ValueError, match="runner exited"):
        consume.launch(PINS, run=_runner(code=2, error="boom"))


@pytest.mark.parametrize("stdout", ["noise\n{}", "{}\nnoise"])
def test_multi_line_stdout_fails_closed(stdout):
    with pytest.raises(ValueError):
        consume.decode_report(subprocess.CompletedProcess([], 0, stdout, ""))


@pytest.mark.parametrize(
    "completed",
    [
        subprocess.CompletedProcess([], 2, "", "runner failed\n::warning::x\n##[error]y\n"),
        subprocess.CompletedProcess([], 0, "noise\n::warning::x\n##[error]y\n", ""),
    ],
)
def test_runner_controlled_diagnostics_cannot_emit_workflow_commands(completed):
    with pytest.raises(ValueError) as caught:
        consume.decode_report(completed)
    assert all(
        not line.lstrip().startswith(("::", "##[")) for line in str(caught.value).splitlines()
    )


def test_runner_timeout_fails_consumer():
    def timeout(args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(args, timeout=900)

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


def test_runner_matches_each_row_by_its_class_and_probe_identity():
    recipes = {
        ("C8", "head"): {"seq": 48, "class_id": "C8", "probe": "head"},
        ("C8", "base"): {"seq": 49, "class_id": "C8", "probe": "base"},
    }
    assert _match_recipe({"class_id": "C8", "probe": "head"}, recipes)["seq"] == 48
    with pytest.raises(RuntimeError, match="unknown witness row identity"):
        _match_recipe({"class_id": "C8", "probe": "missing"}, recipes)


def test_site_pth_cannot_preload_a_pinned_runner_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("CW1353_CANARY", "caller-env")
    env_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(env_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    config = env_dir / "pyvenv.cfg"
    config.write_text(
        config.read_text().replace(
            "include-system-site-packages = false", "include-system-site-packages = true"
        )
    )
    interpreter = env_dir / "bin" / "python"
    if not interpreter.exists():
        interpreter = env_dir / "Scripts" / "python.exe"
    site_dirs = list((env_dir / "lib").glob("python*/site-packages"))
    site_dir = site_dirs[0]
    (site_dir / "bridge.pth").write_text(consume.sysconfig.get_paths()["purelib"] + "\n")
    (site_dir / "pra1353_probe.py").write_text(
        "def core_rows(): return [{'seq': -1, 'observed': 'FORGED'}]\n"
    )
    (site_dir / "attack.pth").write_text(
        "import sys; "
        f"sys.path.insert(0, {str(site_dir)!r}); "
        "__import__('pra1353_probe') if sys.argv[0].endswith('pra1353_runner.py') else None\n"
    )

    def stand_in(args: Any, **kwargs: Any) -> Any:
        assert args[1:4] == ["-I", "-S", "-B"]
        probe = (
            "import json,os,sys; print(json.dumps({"
            "'probe_loaded':'pra1353_probe' in sys.modules,"
            "'customize_loaded':'sitecustomize' in sys.modules,"
            "'caller_env':os.environ.get('CW1353_CANARY')}))"
        )
        completed = subprocess.run([*args[:4], "-c", probe], **kwargs)
        assert completed.returncode == 0
        result = json.loads(completed.stdout)
        assert result == {
            "probe_loaded": False,
            "customize_loaded": False,
            "caller_env": None,
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(_report()), completed.stderr)

    report = consume.launch(PINS, interpreter=interpreter, run=stand_in)
    assert len(report["rows"]) == 56
    assert all(row["seq"] >= 0 for row in report["rows"])
    assert not any(
        item["name"] == "pra1353_probe" and item["file"] == str(site_dir / "pra1353_probe.py")
        for item in report["modules"]
    )


@pytest.mark.parametrize(
    ("module", "pins", "roots"),
    [
        (
            {
                "name": "pra1353_probe",
                "file": "/tmp/unpinned/pra1353_probe.py",
                "origin": "/tmp/unpinned/pra1353_probe.py",
                "loader": "SourceFileLoader",
            },
            PINS,
            [Path("/tmp")],
        ),
        (
            {
                "name": "rogue",
                "file": str(consume.ROOT / "tests/rogue.py"),
                "origin": str(consume.ROOT / "tests/rogue.py"),
                "loader": "SourceFileLoader",
            },
            PINS,
            [consume.ROOT],
        ),
        (
            {
                "name": "outside",
                "file": "/tmp/untrusted/module.py",
                "origin": "/tmp/untrusted/module.py",
                "loader": "SourceFileLoader",
            },
            PINS,
            [consume.ROOT / "src", consume.ROOT / "tests"],
        ),
    ],
)
def test_module_origin_audit_fails_closed(module, pins, roots):
    with pytest.raises(ValueError):
        consume._validate_module_origins([module], pins, {}, consume.ROOT, roots, roots)
