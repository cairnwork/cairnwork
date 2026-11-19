"""#1450: the base Python install is recorded before PR code and re-checked at the gate."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pra1353_base_record as rec
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "pra1353_base_record.py"


def _fake_install(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    (root / "pkg").mkdir(parents=True)
    (root / "os.py").write_text("print('os')\n")
    (root / "pkg" / "mod.py").write_text("X = 1\n")
    (root / "link.py").symlink_to("os.py")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "os.pyc").write_bytes(b"cache")
    (root / "site-packages").mkdir()
    (root / "site-packages" / "extra.py").write_text("ignored\n")
    return root


def _digest(root: Path) -> str:
    return rec.tree_digest([str(root)])[0]


def test_digest_is_stable_and_ignores_caches_and_site_packages(tmp_path):
    root = _fake_install(tmp_path)
    before = _digest(root)
    (root / "__pycache__" / "new.pyc").write_bytes(b"more")
    (root / "site-packages" / "later.py").write_text("also ignored\n")
    assert _digest(root) == before


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: (r / "os.py").write_text("print('evil')\n"),
        lambda r: (r / "pkg" / "evil.py").write_text("X = 2\n"),
        lambda r: (r / "pkg" / "mod.py").unlink(),
        lambda r: ((r / "link.py").unlink(), (r / "link.py").symlink_to("pkg/mod.py")),
        lambda r: (r / "os.py").chmod(0o755),
        lambda r: (r / "pkg" / "mod.py").rename(r / "pkg" / "other.py"),
    ],
    ids=["edit", "add", "delete", "retarget-link", "mode", "rename"],
)
def test_any_change_to_the_install_changes_the_digest(tmp_path, tamper):
    root = _fake_install(tmp_path)
    before = _digest(root)
    tamper(root)
    assert _digest(root) != before


def test_special_files_fail_closed(tmp_path):
    root = _fake_install(tmp_path)
    os.mkfifo(root / "fifo")
    with pytest.raises(OSError, match="not a regular file"):
        _digest(root)


def test_compare_names_the_differing_field_and_rejects_bad_records(tmp_path):
    current = rec.snapshot([str(_fake_install(tmp_path))])
    assert rec.compare(dict(current), current) is None
    assert "digest" in str(rec.compare({**current, "digest": "0" * 64}, current))
    assert "base_prefix" in str(rec.compare({**current, "base_prefix": "/elsewhere"}, current))
    assert rec.compare({**current, "extra": 1}, current) == "record is malformed"
    assert rec.compare(["not", "a", "dict"], current) == "record is malformed"


def _run(*args):
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_record_then_verify_on_the_running_interpreter(tmp_path):
    out = tmp_path / "base.json"
    assert _run("record", str(out)).returncode == 0
    assert _run("verify", str(out)).returncode == 0
    assert _run("record", str(out)).returncode == 1  # never overwrites a record


def test_verify_fails_closed_without_or_with_a_tampered_record(tmp_path):
    assert _run("verify", str(tmp_path / "missing.json")).returncode == 1
    out = tmp_path / "base.json"
    _run("record", str(out))
    data = json.loads(out.read_text())
    data["digest"] = "0" * 64
    out.write_text(json.dumps(data))
    assert _run("verify", str(out)).returncode == 1
    out.write_text("not json")
    assert _run("verify", str(out)).returncode == 1
    assert _run("verify").returncode == 2


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n  (?=[a-z][a-z0-9_-]*:\n)", "\n" + body)
    return {p.split(":", 1)[0].strip(): p for p in parts if p.strip()}


@pytest.mark.parametrize("job", ["test", "test-macos"])
def test_ci_records_in_the_trusted_venv_step_and_verifies_in_the_gate_step(job):
    steps = _jobs((ROOT / ".github/workflows/ci.yml").read_text())[job]
    build = steps.find("- name: Trusted #1353 gate venv")
    record = steps.find("pra1353_base_record.py", build)
    sync = steps.find("uv sync --locked")
    gate_step = steps.find("- name: Sealed #1353 witness gate")
    verify = steps.find("pra1353_base_record.py", gate_step)
    gate = steps.find("tests/pra1353_gate.py", gate_step)
    assert 0 < build < record < gate_step < verify < gate < sync
    assert (
        "record" in steps[record : record + 200] and "base-python.json" in steps[record:gate_step]
    )
    assert (
        "verify" in steps[verify : verify + 120]
        and "$RUNNER_TEMP/base-python.json" in steps[verify:gate]
    )
    assert steps[verify - 160 : verify].count("pra1353-trusted/.venv/bin/python") == 1
