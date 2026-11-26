"""#1671 (review of #1698): the hand-over is the same bytes for every run, the guard writes a
collection record in every run, and only the parent decides what it means. No sandbox here: the
guard runs as plain pytest with the descriptor hand-over or the environment hand-over (what an
account run uses)."""

import inspect
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from cairnwork import collect_stop, confined, report_channel, report_guard, verifiers
from cairnwork.pipe_copy import MAX_REPORT_BYTES
from cairnwork.verifier_spec import VerifierSpec

NONCE = "ab" * 16
QUIET = VerifierSpec("q", ("tests/test_x.py",), collect_only=True, announce=False)
LOUD = VerifierSpec("l", ("tests/test_x.py",), collect_only=True)


def _project(tmp_path, body):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard = tmp_path / "guard"
    guard.mkdir(exist_ok=True)
    report_guard.install_plugin(guard)
    return guard


def _argv(*extra):
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
            "-p", report_guard.MODULE, "tests/test_x.py", *extra]  # fmt: skip


@contextmanager
def _guarded(tmp_path, body, *extra):
    """pytest under the guard with the descriptor hand-over: (exit code, channel), still open."""
    guard = _project(tmp_path, body)
    channel = report_channel.open_channel(tmp_path)
    env = {"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": str(guard), **channel.env()}  # fmt: skip
    try:
        done = subprocess.run(_argv(*extra), cwd=tmp_path, env=env, capture_output=True,
                              pass_fds=channel.pass_fds)  # fmt: skip
        yield done.returncode, channel
    finally:
        channel.close()


def _raw(fd):
    return os.pread(fd, MAX_REPORT_BYTES + 1, 0)


# ---- the hand-over is one shape --------------------------------------------------------------


def test_open_channel_has_no_quiet_parameter_and_hands_over_the_same_bytes(tmp_path):
    assert list(inspect.signature(report_channel.open_channel).parameters) == ["private"]
    first, second = report_channel.open_channel(tmp_path), report_channel.open_channel(tmp_path)
    try:
        for channel in (first, second):
            assert re.fullmatch(r"\d+,\d+,\d+", channel.env()[report_guard.FDS_ENV])
            assert len(channel.pass_fds) == 3
            assert re.fullmatch(rb"[0-9a-f]{32}", os.read(channel.pass_fds[0], 100))
    finally:
        first.close()
        second.close()


def test_the_account_hand_over_has_the_same_shape_in_every_run(tmp_path, monkeypatch):
    seen = []
    for index in range(2):
        private = tmp_path / f"p{index}"
        private.mkdir()
        _, _, _, env, path, nonce = verifiers._prepare_run(private, private / "ini", b"")
        seen.append((env[report_guard.PATH_ENV], env[report_guard.NONCE_ENV], path, nonce))
    for given_path, given_nonce, path, nonce in seen:
        assert re.fullmatch(r"[0-9a-f]{32}", given_nonce) and given_nonce == nonce
        assert given_path == str(path) and path.name == "verdict.json"


def test_the_plugin_names_the_same_suffix_as_the_parent():
    source = report_guard._PLUGIN_SOURCE
    assert f'_COLLECTED_SUFFIX = "{report_guard.COLLECTED_SUFFIX}"' in source
    assert "_stopper" not in source and "cairnwork_report_stop" not in source


# ---- the collection record ------------------------------------------------------------------

STATUS = {  # body of test_x.py, extra arguments, the exit status pytest itself ends with
    "collected": ("def test_a():\n    pass\n", (), 0),
    "nothing": ("x = 1\n", (), 5),
    "error": ("import no_such_module_1671\n", (), 2),
    "error-continued": (
        "import no_such_module_1671\n",
        ("--continue-on-collection-errors", "tests/test_y.py"),
        1,
    ),
}


@pytest.mark.parametrize("shape", STATUS)
def test_the_collection_record_carries_the_status_pytest_ends_with(tmp_path, shape):
    body, extra, expected = STATUS[shape]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_y.py").write_text("def test_y():\n    pass\n")
    with _guarded(tmp_path, body, *extra) as (code, channel):
        collected = channel.read_collected()
        assert collected.recorded and collected.collection_finished and not collected.outcomes
        assert (collected.exitstatus, code) == (expected, expected)
        assert collected.testsfailed == (1 if "error" in shape else 0)


def test_the_collection_record_and_the_final_record_are_each_read_as_their_own_kind(tmp_path):
    with _guarded(tmp_path, "def test_a():\n    pass\n") as (code, channel):
        stage, final = _raw(channel.pass_fds[2]), _raw(channel.pass_fds[1])
        assert code == 0 and json.loads(stage)["stage"] == "collected"
        assert "stage" not in json.loads(final)
        assert report_guard.parse_verdict(final, channel.nonce).ran
        assert report_guard.parse_verdict(stage, channel.nonce, collected=True).recorded
        # a stage record is never the final one; a final record is never a stage record
        assert not report_guard.parse_verdict(stage, channel.nonce).recorded
        assert not report_guard.parse_verdict(final, channel.nonce, collected=True).recorded
        assert not report_guard.parse_verdict(stage, "0" * 32, collected=True).recorded
        forged = {**json.loads(stage), "outcomes": {"t.py::a": "passed"}}
        assert not report_guard.parse_verdict(
            json.dumps(forged).encode(), channel.nonce, collected=True
        ).recorded
        for stage_value in ("final", None, 1):
            odd = {**json.loads(stage), "stage": stage_value}
            raw = json.dumps(odd).encode()
            assert not report_guard.parse_verdict(raw, channel.nonce, collected=True).recorded


# ---- the parent's watcher --------------------------------------------------------------------


def test_the_watcher_takes_only_a_collection_record_with_its_own_nonce(tmp_path):
    with _guarded(tmp_path, "def test_a():\n    pass\n") as (_, genuine):
        stage, final = _raw(genuine.pass_fds[2]), _raw(genuine.pass_fds[1])
    channel = report_channel.open_channel(tmp_path)
    try:
        fd = channel.pass_fds[2]
        watcher = collect_stop.watcher(QUIET, channel, tmp_path / "v.json", channel.nonce)
        assert watcher and not watcher() and watcher.record() is None  # nothing written yet
        for raw in (stage, final, b'{"nonce": "x"', b" " * (MAX_REPORT_BYTES + 5)):
            os.ftruncate(fd, 0)
            os.pwrite(fd, raw, 0)  # another nonce, a final record, a partial one, a huge one
            assert not watcher() and watcher.record() is None
        mine = json.dumps({**json.loads(stage), "nonce": channel.nonce}).encode()
        os.ftruncate(fd, 0)
        os.pwrite(fd, mine[:50], 0)
        assert not watcher()  # still being written
        os.pwrite(fd, mine, 0)
        assert watcher()
        first = watcher.record()
        assert first is not None and first.exitstatus == 0
        os.ftruncate(fd, 0)  # whatever the child does later, the verdict keeps the first read
        again = watcher.record()
        assert watcher() and again is not None and again.recorded
    finally:
        channel.close()


def test_a_run_that_is_not_quiet_has_no_watcher(tmp_path):
    channel = report_channel.open_channel(tmp_path)
    try:
        assert collect_stop.watcher(LOUD, channel, tmp_path / "v.json", NONCE) is None
        assert (
            collect_stop.watcher(VerifierSpec("r", ("t",)), channel, tmp_path / "v", NONCE) is None
        )
    finally:
        channel.close()


def test_a_reader_that_fails_never_stops_a_run():
    def fail():
        raise RuntimeError("boom")

    assert not collect_stop.CollectStop(fail)()


# ---- ending the run: the confined wait -----------------------------------------------------


def _sleeper(tmp_path, **kwargs):
    env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    argv = [sys.executable, "-c", "import time; time.sleep(120)"]
    start = time.monotonic()
    out = confined.execute(
        argv, tmp_path, env, 100, tmp_path / "log", scope_roots=(tmp_path.parent,), **kwargs
    )
    return out, time.monotonic() - start


def test_a_stop_ends_the_group_early_and_is_not_a_timeout(tmp_path):
    (code, timed_out, survivors), seconds = _sleeper(tmp_path, stop=lambda: True)
    assert not timed_out and code == -9 and int(survivors) == 0 and seconds < 60


@pytest.mark.parametrize(("reaped", "expected"), [(0, 0), (1, 1), (3, 3)])
def test_the_main_process_is_handed_to_the_reap_not_subtracted_here(tmp_path, reaped, expected):
    """Review of #1698: confined never subtracts from the reap's number (1 can mean "cannot
    tell"); it tells the reap which process it ends on purpose and takes the answer as it is."""
    seen = []
    out, _ = _sleeper(tmp_path, reap=lambda main=0: seen.append(main) or reaped, stop=lambda: True)
    assert seen == [1] and int(out[2]) == expected


def test_without_a_stop_the_reaped_count_is_unchanged(tmp_path):
    env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    out = confined.execute([sys.executable, "-c", "pass"], tmp_path, env, 60, tmp_path / "log",
                           reap=lambda main=0: 1, stop=lambda: False,
                           scope_roots=(tmp_path.parent,))  # fmt: skip
    assert out[0] == 0 and not out[1] and int(out[2]) == 1


def test_a_stop_that_never_fires_leaves_the_timeout_alone(tmp_path):
    env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    roots = (tmp_path.parent,)
    code, timed_out, _ = confined.execute(argv, tmp_path, env, 1, tmp_path / "log",
                                          stop=lambda: False, scope_roots=roots)  # fmt: skip
    assert timed_out and code is None


# ---- the account hand-over: a named collection file, end to end without the account ----------


def test_the_account_hand_over_writes_a_collection_file_and_the_parent_stops_on_it(tmp_path):
    guard = _project(tmp_path, "import time\n\ndef test_a():\n    time.sleep(120)\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = run_dir / "verdict.json"
    env = {"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(guard),
           report_guard.PATH_ENV: str(path), report_guard.NONCE_ENV: NONCE,
           confined.RUN_TOKEN_VAR: uuid.uuid4().hex}  # fmt: skip
    watcher = collect_stop.watcher(QUIET, None, path, NONCE)
    assert watcher is not None
    start = time.monotonic()
    code, timed_out, survivors = confined.execute(
        _argv(), tmp_path, env, 100, tmp_path / "log", stop=watcher, scope_roots=(tmp_path.parent,)
    )
    assert not timed_out and int(survivors) == 0 and time.monotonic() - start < 60
    named = run_dir / ("verdict.json" + report_guard.COLLECTED_SUFFIX)
    assert stat.S_IMODE(named.stat().st_mode) == 0o644 and not path.exists()
    got = watcher.record()
    assert got is not None and got.collected == ("tests/test_x.py::test_a",) and not got.outcomes
    assert report_guard.read_verdict(path, NONCE).recorded is False  # no final record was written
    assert not report_guard.read_verdict(named, NONCE).recorded  # a stage file is not a final one
    assert report_guard.read_verdict(named, NONCE, collected=True).recorded


def test_a_planted_collection_file_does_not_break_the_run(tmp_path):
    """The guard's write is O_EXCL: a file already there is not followed or replaced."""
    guard = _project(tmp_path, "def test_a():\n    pass\n")
    path = tmp_path / "verdict.json"
    (tmp_path / "other").write_text("keep")
    os.symlink(tmp_path / "other", str(path) + report_guard.COLLECTED_SUFFIX)
    env = {"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(guard),
           report_guard.PATH_ENV: str(path), report_guard.NONCE_ENV: NONCE}  # fmt: skip
    done = subprocess.run(_argv(), cwd=tmp_path, env=env, capture_output=True)
    assert done.returncode == 0 and (tmp_path / "other").read_text() == "keep"
    assert report_guard.read_verdict(path, NONCE).ran
    assert not report_guard.read_verdict(
        Path(str(path) + report_guard.COLLECTED_SUFFIX), NONCE, collected=True
    ).recorded


def test_the_run_directory_name_is_opaque_and_recovery_still_finds_it():
    from cairnwork import proc_cwd, recovery
    from cairnwork.run_tags import RUN_TAGS

    attempt = "a0123456789abcdef"
    names = {verifiers.opaque_clone_id(attempt) for _ in range(50)}
    assert len(names) == 50
    for name in names:
        assert re.fullmatch(rf"{attempt}-x-v\d{{16}}", name) and "x" in RUN_TAGS
    assert re.fullmatch(r"a[0-9a-f]{16}-x-v\d{16}", verifiers.opaque_clone_id(""))
    assert recovery._RUN_TAGS == RUN_TAGS and proc_cwd.RUN_TAGS == RUN_TAGS


# ---- the account's reap: an unknown count is never turned into "nothing escaped" -------------


@pytest.mark.parametrize(
    ("before", "left", "main", "expected"),
    [
        (1, 0, 1, 0),
        (3, 0, 1, 2),
        (0, 0, 1, 0),
        (-1, 0, 1, 1),
        (1, 1, 1, 2),
        (1, -1, 1, 2),
        (1, 0, 0, 1),
    ],
)
def test_reap_takes_the_known_main_process_off_only_a_known_count(
    monkeypatch, before, left, main, expected
):
    import cairnwork.verifier_account as va

    account = va.VerifierAccount(user="nobody")
    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", lambda *a, **k: before)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda *a, **k: left)
    assert account.reap(main) == expected


def test_reap_unknown_with_a_running_main_process_stays_a_survivor(monkeypatch):
    import cairnwork.verifier_account as va

    account = va.VerifierAccount(user="nobody")

    def boom(*a, **k):
        raise subprocess.SubprocessError("ps")

    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", boom)
    assert account.reap(1) >= 1
