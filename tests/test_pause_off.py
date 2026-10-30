"""PAUSE (#476) AC1: without a planner configuration, ``python -m experimental.pause.cli run`` is
the bare ``local_worker run``: the same episode line (less the volatile fields that schema.py
lists), the same local model requests, no cloud request and no PAUSE receipt."""

import socket

import pytest
from local_worker_fakes import cli_harness, lines, reply

from experimental.local_worker import cli as worker_cli
from experimental.pause import cli as pause_cli
from experimental.pause import schema

READ = reply("read_file", path="src/app.py")
READ_T = reply("read_file", path="tests/test_app.py")
LIST = reply("list_dir", path=".")
EDIT = reply("edit_file", path="src/app.py", target="x", replacement="y")
WRITE = reply("write_file", path="src/new.py", content="z = 2\n")
TESTS = reply("run_tests")
DONE = reply("finish", status="DONE")
SPEC = reply("finish", status="NEEDS_SPEC")
SCRIPTS = [  # ten tasks' worth of scripted local-model replies
    [DONE],
    [READ, DONE],
    [READ, EDIT, DONE],
    [LIST, READ_T, WRITE, TESTS, DONE],
    [READ, READ_T, SPEC],
    [EDIT, TESTS, EDIT, DONE],
    [reply("no_such_tool"), READ, DONE],
    [READ, reply(say="thinking only"), WRITE, DONE],
    [TESTS, READ, EDIT, TESTS, DONE],
    [LIST, LIST, READ, READ_T, EDIT, DONE],
]


def strip(value):
    """The line without schema.EPISODE_VOLATILE_FIELDS, at any depth."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in schema.EPISODE_VOLATILE_FIELDS}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


@pytest.fixture
def through_pause(tmp_path, monkeypatch):
    """(run, out, connects): run(replies, entry) goes through the bare CLI or PAUSE's."""
    run, out = cli_harness(tmp_path, monkeypatch)
    real = worker_cli.main
    connects: list = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a: connects.append(a))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connects.append(a))

    def go(replies, entry, *extra):
        via = (lambda argv: pause_cli.main(argv, worker_main=real)) if entry == "pause" else real
        monkeypatch.setattr(worker_cli, "main", via)
        return run(replies, "run", *extra)

    return go, out, connects


@pytest.mark.parametrize("script", range(len(SCRIPTS)))
def test_flag_off_is_the_bare_run(through_pause, capsys, script):
    go, out, connects = through_pause
    assert go(SCRIPTS[script], "bare") == 0
    bare_stdout = capsys.readouterr().out
    assert go(SCRIPTS[script], "pause") == 0  # same out dir: the manifest must match too
    pause_stdout = capsys.readouterr().out
    bare, paused = lines(out)
    assert bare["episode_id"] != paused["episode_id"]
    assert strip(paused) == strip(bare)
    assert (
        bare_stdout.split()[:2]
        == pause_stdout.split()[:2]
        == ["LOCAL_WORKER_EPISODE"] + [bare["verification"]["exit_reason"]]
    )
    assert connects == []  # no cloud request (and the fake local model needs no socket)
    assert not list(out.rglob(schema.RECEIPTS_FILE)) and not list(out.rglob("*pause*"))


def test_the_worker_gets_the_arguments_unchanged(monkeypatch):
    seen = []
    argv = ["run", "--task", "t.json", "--checkout", "c", "--write-path", "src", "--plan", "p"]
    assert pause_cli.main(argv, worker_main=lambda a: seen.append(a) or 0) == 0
    assert seen == [argv]


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--planner-config", "cfg.json", "--task", "t.json"],
        ["run", "--task", "t.json", "--planner-config=cfg.json"],
        ["run", "--planner-config"],
    ],
)
def test_a_planner_configuration_is_refused_until_the_planner_is_wired(capsys, argv):
    seen = []
    assert pause_cli.main(argv, worker_main=lambda a: seen.append(a) or 0) == 2
    assert seen == [] and "PAUSE_REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["resume", "--task", "t.json"], ["--task", "t.json"]])
def test_only_run_is_offered(capsys, argv):
    seen = []
    assert pause_cli.main(argv, worker_main=lambda a: seen.append(a) or 0) == 2
    assert seen == [] and "usage" in capsys.readouterr().err
