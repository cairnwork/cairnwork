"""G1 step 1 (#1353 family): a generic write probe, run through the real `consume.launch` child
environment (same interpreter flags, env, cwd, sealed tree and import roots as the witness runner).

Today the product child and the referee share one uid, so the probe finds writable roots: the
isolation test is an expected failure (strict, so the day G1 lands it must be flipped, not left).
The controls prove the probe is not vacuous."""

import json
import subprocess
import sys
from pathlib import Path

import pra1353_consume as consume
import pytest
from test_1039_1353_witnesses import PINS

PROBE = Path(__file__).resolve().parent / "g1_root_probe.py"
KINDS = {"create", "chmod", "chmod+create", "rewrite"}


class _Done(Exception):
    def __init__(self, report: dict) -> None:
        self.report = report


def probe_in_launch() -> dict:
    """Run the probe where the runner would run: `launch` builds the child, we swap the script."""

    def run(argv, **kwargs):
        argv = [*argv[:4], str(PROBE), *argv[5:]]
        done = subprocess.run(argv, **kwargs)
        assert done.returncode == 0, done.stderr
        raise _Done(json.loads(done.stdout))

    try:
        consume.launch(PINS, run=run)
    except _Done as done:
        return done.report
    raise AssertionError("launch did not reach the child")


def run_probe(roots: list[Path], child_reports: Path) -> dict:
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(PROBE), *map(str, roots[:7])],
        env={"PRA1353_CHILD_REPORT_DIR": str(child_reports)},
        cwd=child_reports,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def _seven(tmp_path: Path, mode: int) -> list[Path]:
    names = ["a", "b", "c", "sealed/tests", "sealed/src", "d", "e"]
    roots = [tmp_path / name for name in names]
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    (roots[3] / "witness.py").write_text("x = 1\n")
    (roots[3] / "witness.py").chmod(0o444)
    for root in reversed(roots):
        root.chmod(mode)
    return roots


def test_control_probe_flags_a_writable_root(tmp_path):
    roots = _seven(tmp_path, 0o755)
    found = {item[0] for item in run_probe(roots, tmp_path / "a")["writable"]}
    assert {"stdlib", "purelib", "platlib", "sealed/tests", "sealed/src"} <= found


def test_control_probe_flags_a_sealed_tree_the_owner_can_chmod_back(tmp_path):
    """The seal shape consume.launch builds (0555 dirs, 0444 files) is undone by the same uid."""
    roots = _seven(tmp_path, 0o555)
    hits = {(item[0], item[1]) for item in run_probe(roots, tmp_path / "a")["writable"]}
    assert ("sealed/tests", "chmod") in hits and ("sealed/tests", "rewrite") in hits


def test_control_probe_does_not_flag_roots_the_account_cannot_write():
    """/usr/bin is root-owned: not writable, no chmod, so not reported (no false alarm)."""
    root = Path("/usr/bin")
    assert root.is_dir()
    assert probe_report_for_system_root(root) == []


def probe_report_for_system_root(root: Path) -> list[list[str]]:
    sys.path.insert(0, str(PROBE.parent))
    try:
        import g1_root_probe as probe
    finally:
        sys.path.pop(0)
    found: list[list[str]] = []
    probe.probe_root("stdlib", root, found)
    return found


@pytest.mark.xfail(
    strict=True, reason="G1 pending: the product child still shares the referee's uid (#1353)"
)
def test_every_root_refuses_writes_in_the_real_launch_environment():
    report = probe_in_launch()
    assert report["tried"] >= 20
    assert report["writable"] == []


def test_today_the_same_uid_child_can_reach_the_known_variant_shapes():
    """The red baseline for the right reasons (delete this test when G1 lands, step 2/3): a sealed
    file can be chmodded back and rewritten, sealed src and the venv take new files."""
    report = probe_in_launch()
    hits = {(item[0], item[1]) for item in report["writable"]}
    assert {("sealed/tests", "chmod"), ("sealed/tests", "rewrite")} <= hits
    assert {("sealed/src", "chmod+create"), ("purelib", "create"), ("platlib", "create")} <= hits
    assert report["tried"] >= 20
    assert {item[1] for item in report["writable"]} <= KINDS
