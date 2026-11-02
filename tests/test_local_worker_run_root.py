"""#677: an episode deletes its own run root after its line, and nothing else.

Every test works in its own tmp_path: the runs directory is ``tmp_path/runs`` (passed as --runs),
the cwd is a directory holding only a SENTINEL file, which must survive. No real runs directory,
no wrapper, no process is touched: the CLI runs with the fake tools and endpoint of
local_worker_fakes."""

from __future__ import annotations

import os

import pytest
from local_worker_fakes import cli_harness, lines, reply

from experimental.local_worker import corpus_io, run_root_cleanup
from experimental.local_worker.run_root_cleanup import CLAIM, claim, eligible

DONE = [reply("read_file", path="src/app.py"), reply("finish", status="DONE")]
A, B, C = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture(autouse=True)
def sentinel_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "SENTINEL").write_text("keep\n")
    monkeypatch.chdir(cwd)
    yield
    assert (cwd / "SENTINEL").read_text() == "keep\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    run, out = cli_harness(tmp_path, monkeypatch)
    runs = (tmp_path / "runs").resolve()
    runs.mkdir()
    return run, out, runs


def _fill(root):
    """A run root as the harness lays it out, plus what a run leaves behind."""
    for d in ("checkout/src", "tmp/home", "q", "venv/bin"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "venv" / "bin" / "python").write_bytes(b"\0" * 4096)
    (root / "tmp" / "home" / "cache.bin").write_bytes(b"x")
    (root / "checkout" / "src" / "app.py").write_text("x = 1\n")


def test_an_episode_deletes_its_own_run_root_after_its_line(env):
    run, out, runs = env
    _fill(runs / A)
    assert run(DONE, "run", "--runs", str(runs), root=A) == 0
    assert lines(out)[0]["checkout_id"] == A
    assert not os.path.lexists(runs / A)


def test_small_result_files_are_kept_and_nothing_else(env):
    run, out, runs = env
    _fill(runs / A)
    (runs / A / "result.json").write_text("{}\n")
    (runs / A / "rows.jsonl").write_text("{}\n")
    (runs / A / "notes.md").write_text("# n\n")
    (runs / A / "big.bin").write_bytes(b"\0" * 1024)
    (runs / A / "nested.json").mkdir()  # a directory, whatever its name, goes
    assert run(DONE, "run", "--runs", str(runs), root=A) == 0
    assert sorted(p.name for p in (runs / A).iterdir()) == ["notes.md", "result.json", "rows.jsonl"]


def test_keep_run_root_leaves_the_root_and_releases_the_claim(env):
    run, out, runs = env
    _fill(runs / A)
    assert run(DONE, "run", "--runs", str(runs), "--keep-run-root", root=A) == 0
    assert (runs / A / "venv" / "bin" / "python").exists()
    assert not os.path.lexists(runs / A / CLAIM)


def test_a_live_episodes_root_and_other_roots_are_left_alone(env):
    run, out, runs = env
    for tag in (A, B, C):
        _fill(runs / tag)
    live = claim(runs / B, runs, "ep_live")  # what a running episode holds from its start
    assert live is not None
    assert run(DONE, "run", "--runs", str(runs), root=A) == 0
    assert not os.path.lexists(runs / A)
    for tag in (B, C):
        assert (runs / tag / "venv" / "bin" / "python").exists()
        assert (runs / tag / "checkout" / "src" / "app.py").exists()
    assert (runs / B / CLAIM).read_text() == "ep_live\n"
    # A second episode in the live root is refused at start and changes nothing there.
    assert run(DONE, "run", "--runs", str(runs), "--seed", "1", root=B) == 2
    assert (runs / B / CLAIM).read_text() == "ep_live\n"
    assert (runs / B / "venv" / "bin" / "python").exists()
    # Removal goes by the claim's owner: another episode id cannot delete the live root.
    stranger = run_root_cleanup.Claim(runs / B, "ep_other")
    assert run_root_cleanup.remove(stranger, runs).startswith("kept")
    assert (runs / B / "venv" / "bin" / "python").exists()


def test_an_episode_without_a_line_keeps_its_root_and_releases_it(env, monkeypatch):
    run, out, runs = env
    _fill(runs / A)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(corpus_io, "append_episode", fail)
    with pytest.raises(OSError, match="disk full"):
        run(DONE, "run", "--runs", str(runs), root=A)
    assert (runs / A / "venv" / "bin" / "python").exists()
    assert not os.path.lexists(runs / A / CLAIM)  # free again for a retry


def test_a_root_outside_runs_is_never_deleted(env, tmp_path):
    run, out, runs = env
    _fill(runs / A)
    other_runs = (tmp_path / "other-runs").resolve()
    other_runs.mkdir()
    assert run(DONE, "run", "--runs", str(other_runs), root=A) == 0  # A is not under other-runs
    assert (runs / A / "venv" / "bin" / "python").exists()
    assert not os.path.lexists(runs / A / CLAIM)
    assert run(DONE, "run", "--seed", "1", root=B) == 0  # the default runs directory: not ours
    assert (runs / B / "checkout").is_dir()


def test_a_linked_root_or_runs_directory_is_never_deleted(env, tmp_path):
    run, out, runs = env
    target = (tmp_path / "elsewhere" / A).resolve()
    _fill(target)
    (runs / A).symlink_to(target)  # the root is a link: it resolves outside runs
    assert run(DONE, "run", "--runs", str(runs), root=A) == 0
    assert (runs / A).is_symlink()
    assert (target / "venv" / "bin" / "python").exists()
    _fill(runs / B)
    link_runs = tmp_path / "runs-link"
    link_runs.symlink_to(runs)  # a runs directory named through a link is not canonical
    assert run(DONE, "run", "--runs", str(link_runs), "--seed", "1", root=B) == 0
    assert (runs / B / "venv" / "bin" / "python").exists()


def test_a_link_inside_the_root_goes_but_not_its_target(env, tmp_path):
    run, out, runs = env
    _fill(runs / A)
    outside = tmp_path / "outside"
    (outside / "d").mkdir(parents=True)
    (outside / "d" / "f.txt").write_text("stay\n")
    (outside / "keep.json").write_text("{}\n")
    (runs / A / "dir-link").symlink_to(outside / "d")
    (runs / A / "file-link.json").symlink_to(outside / "keep.json")  # a link is no result file
    (runs / A / "checkout" / "src" / "inner-link").symlink_to(outside / "d")
    assert run(DONE, "run", "--runs", str(runs), root=A) == 0
    assert not os.path.lexists(runs / A)
    assert (outside / "d" / "f.txt").read_text() == "stay\n"
    assert (outside / "keep.json").read_text() == "{}\n"


def test_only_a_real_hex_named_directory_directly_in_runs_is_eligible(tmp_path):
    runs = (tmp_path / "runs").resolve()
    (runs / A / B).mkdir(parents=True)
    (runs / ("g" * 32)).mkdir()
    (runs / ("a" * 31)).mkdir()
    (runs / C).write_text("a file\n")
    (runs / ("d" * 32)).symlink_to(runs / A)
    assert eligible(runs / A, runs)
    assert not eligible(runs / A / B, runs)  # not directly inside runs
    assert not eligible(runs / ("g" * 32), runs)
    assert not eligible(runs / ("a" * 31), runs)
    assert not eligible(runs / C, runs)  # not a directory
    assert not eligible(runs / ("d" * 32), runs)  # a link
    assert not eligible(runs / ("e" * 32), runs)  # missing
    assert claim(runs / ("d" * 32), runs, "ep") is None
    link_runs = tmp_path / "runs-link"
    link_runs.symlink_to(runs)
    assert not eligible(link_runs / A, link_runs)  # runs (and so the root) not canonical
    assert not os.path.lexists(runs / A / CLAIM)


def test_a_claim_replaced_by_a_link_is_not_this_episodes(tmp_path):
    runs = (tmp_path / "runs").resolve()
    _fill(runs / A)
    c = claim(runs / A, runs, "ep_1")
    assert c is not None
    elsewhere = tmp_path / "claim-copy"
    elsewhere.write_text("ep_1\n")
    (runs / A / CLAIM).unlink()
    (runs / A / CLAIM).symlink_to(elsewhere)
    assert run_root_cleanup.remove(c, runs).startswith("kept")
    assert (runs / A / "venv" / "bin" / "python").exists()
