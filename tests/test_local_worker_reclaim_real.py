"""Reclaim with the real lsof (nothing patched) and the boundary cases a review found."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_local_worker_reclaim import NOW, OLD, _age, _root

from experimental.local_worker import run_root_reclaim as rr


def _runs(tmp_path: Path) -> Path:
    runs = (tmp_path / "runs").resolve()
    runs.mkdir()
    return runs


def test_real_lsof_reclaims_an_idle_old_root(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    report = rr.reclaim_stale(runs, 48, now=NOW)  # default in_use: the real lsof
    assert report.roots[0].outcome == "reclaimed"
    assert not (root / "checkout").exists()
    assert (root / "result.json").read_text() == "keep result.json\n"


def test_real_lsof_blocks_a_root_a_process_is_using(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    child = subprocess.Popen(["sleep", "30"], cwd=root / "checkout")
    try:
        assert rr.reclaim_stale(runs, 48, now=NOW).roots[0].outcome == "in_use"
        assert (root / "checkout" / "payload").exists()
    finally:
        child.kill()
        child.wait()


def test_the_episodes_own_root_is_excluded_even_when_old(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    report = rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda _r: False, exclude=root)
    assert report.roots[0].outcome == "excluded"
    assert (root / "checkout" / "payload").exists()


def test_other_top_level_entries_stay(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    for name in ("evidence", "logs"):
        (root / name).mkdir()
        (root / name / "x.bin").write_bytes(b"evidence")
    _age(root)
    rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda _r: False)
    assert (root / "evidence" / "x.bin").read_bytes() == b"evidence"
    assert (root / "logs" / "x.bin").exists()


def test_a_file_named_like_a_rebuildable_dir_stays(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    import shutil

    shutil.rmtree(root / "checkout")
    (root / "checkout").write_text("a file")
    _age(root)
    rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda _r: False)
    assert (root / "checkout").read_text() == "a file"


def test_age_threshold_is_inclusive_at_exactly_the_cutoff(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    edge = OLD + 48 * 3600
    assert rr.reclaim_stale(runs, 48, now=edge - 1, in_use=lambda _r: False).roots[0].outcome == (
        "recent"
    )
    assert (root / "checkout").exists()
    assert rr.reclaim_stale(runs, 48, now=edge, in_use=lambda _r: False).roots[0].outcome == (
        "reclaimed"
    )


def test_a_link_inside_a_reclaimed_dir_never_reaches_outside(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    os.symlink(outside, root / "q" / "lnk")
    _age(root)
    rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda _r: False)
    assert (outside / "keep").read_text() == "keep"
    assert not (root / "q").exists()


def test_a_root_with_nothing_rebuildable_never_calls_lsof(tmp_path):
    runs = _runs(tmp_path)
    root = _root(runs)
    rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda _r: False)
    calls = []
    report = rr.reclaim_stale(runs, 48, now=NOW, in_use=lambda r: calls.append(r) or False)
    assert report.roots[0].outcome == "nothing"
    assert calls == [] and (root / "result.json").exists()
