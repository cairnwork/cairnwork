"""Age based reclamation keeps evidence, live roots and paths outside canonical runs untouched."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from local_worker_fakes import cli_harness, lines, reply

from experimental.local_worker import run_root_reclaim
from experimental.local_worker.run_root_cleanup import CLAIM

OLD = 1_000_000.0
NOW = OLD + 100 * 3600
TAG = "a" * 32
DONE = [reply("read_file", path="src/app.py"), reply("finish", status="DONE")]


def _root(runs: Path, tag: str = TAG) -> Path:
    root = runs / tag
    for name in ("checkout", "venv", "tmp", "q", ".cache", "__pycache__", ".pytest_cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "payload").write_bytes(b"rebuild" * 32)
    for name in ("result.json", "events.jsonl", "notes.md", "worker.log", "transcript", "receipt"):
        (root / name).write_text(f"keep {name}\n")
    _age(root)
    return root


def _age(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        os.utime(path, (OLD, OLD), follow_symlinks=False)


def _clean(runs: Path, **kwargs):
    return run_root_reclaim.reclaim_stale(
        runs, 48, now=NOW, in_use=kwargs.pop("in_use", lambda _root: False), **kwargs
    )


def _no_lsof(*_args, **_kwargs):
    return SimpleNamespace(returncode=1, stdout="", stderr="")


def test_old_root_reclaims_only_rebuildable_dirs_and_keeps_evidence(tmp_path):
    runs = (tmp_path / "runs").resolve()
    runs.mkdir()
    root = _root(runs)
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()
    }
    report = _clean(runs)
    assert report.reclaimed == 1
    assert report.bytes_reclaimed > 0
    rebuildable = ("checkout", "venv", "tmp", "q", ".cache", "__pycache__", ".pytest_cache")
    assert all(not (root / name).exists() for name in rebuildable)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()} == hashes


def test_new_root_is_untouched(tmp_path):
    runs = (tmp_path / "runs").resolve()
    root = _root(runs)
    os.utime(root / "checkout" / "payload", (NOW, NOW))
    report = _clean(runs)
    assert report.roots[0].outcome == "recent"
    assert (root / "checkout" / "payload").exists()


def test_claimed_root_is_untouched(tmp_path):
    runs = (tmp_path / "runs").resolve()
    root = _root(runs)
    (root / CLAIM).write_text("active\n")
    _age(root)
    assert _clean(runs).roots[0].outcome == "claimed"
    assert (root / "checkout").exists()


def test_in_use_or_unavailable_lsof_fails_closed(tmp_path, monkeypatch):
    runs = (tmp_path / "runs").resolve()
    root = _root(runs)
    assert _clean(runs, in_use=lambda _path: True).roots[0].outcome == "in_use"
    assert (root / "checkout").exists()

    monkeypatch.setattr(
        run_root_reclaim.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="123\n", stderr=""),
    )
    assert run_root_reclaim.reclaim_stale(runs, 48, now=NOW).roots[0].outcome == "in_use"

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(run_root_reclaim.subprocess, "run", missing)
    assert run_root_reclaim.reclaim_stale(runs, 48, now=NOW).roots[0].outcome == "in_use"
    assert (root / "checkout").exists()

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("lsof", 10)

    monkeypatch.setattr(run_root_reclaim.subprocess, "run", timeout)
    assert run_root_reclaim.reclaim_stale(runs, 48, now=NOW).roots[0].outcome == "in_use"
    assert (root / "checkout").exists()


def test_dry_run_reports_estimate_without_deleting(tmp_path):
    runs = (tmp_path / "runs").resolve()
    root = _root(runs)
    report = run_root_reclaim.reclaim_stale(
        runs, 48, now=NOW, in_use=lambda _path: False, dry_run=True
    )
    assert report.roots[0].outcome == "would_reclaim"
    assert report.bytes_reclaimed > 0
    assert (root / "checkout").exists()


def test_invalid_roots_and_noncanonical_runs_are_untouched(tmp_path):
    runs = (tmp_path / "runs").resolve()
    runs.mkdir()
    target = tmp_path / "outside" / "target"
    (target / "checkout").mkdir(parents=True)
    payload = target / "checkout" / "payload"
    payload.write_bytes(b"outside stays byte for byte")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    link = runs / ("b" * 32)
    link.symlink_to(target)
    bad = runs / "not-a-root"
    bad.mkdir()
    fresh = runs / TAG
    (fresh / "checkout").mkdir(parents=True)
    (fresh / "checkout" / "payload").write_text("new")
    linked_runs = tmp_path / "runs-link"
    linked_runs.symlink_to(runs)
    report = _clean(linked_runs)
    assert report.roots == ()
    assert (fresh / "checkout" / "payload").exists()
    report = _clean(runs)
    assert report.reclaimed == 0
    assert link.is_symlink()
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == digest
    assert bad.is_dir()


def test_cli_reclaims_old_roots_but_excludes_this_episode(tmp_path, monkeypatch, capsys):
    run, out = cli_harness(tmp_path, monkeypatch)
    runs = (tmp_path / "runs").resolve()
    old = _root(runs, "b" * 32)
    monkeypatch.setattr(run_root_reclaim.subprocess, "run", _no_lsof)
    monkeypatch.setattr(run_root_reclaim.time, "time", lambda: NOW)
    assert run(DONE, "run", "--runs", str(runs), root="c" * 32) == 0
    assert not (old / "checkout").exists()
    current = runs / ("c" * 32)
    assert (current / "checkout").is_dir()
    assert len(lines(out)) == 1
    assert "LOCAL_WORKER_RECLAIM 1 " in capsys.readouterr().err


def test_cli_zero_disables_reclaim(tmp_path, monkeypatch):
    run, _out = cli_harness(tmp_path, monkeypatch)
    runs = (tmp_path / "runs").resolve()
    old = _root(runs, "b" * 32)
    monkeypatch.setattr(run_root_reclaim.subprocess, "run", _no_lsof)
    monkeypatch.setattr(run_root_reclaim.time, "time", lambda: NOW)
    assert (
        run(
            DONE,
            "run",
            "--runs",
            str(runs),
            "--reclaim-older-than-hours",
            "0",
            root="c" * 32,
        )
        == 0
    )
    assert (old / "checkout").exists()
