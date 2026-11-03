"""A tool argument that does not encode as UTF-8 (a lone surrogate decoded from a JSON escape)
is a repairable invalid_tool_call, never an internal_error abort (Issue 675 item 1)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from local_worker_fakes import META, TASK, make_loop, reply

from experimental.local_worker import episode
from experimental.local_worker.agent import initial_messages
from experimental.local_worker.agent_protocol import BadCall, decode_call
from experimental.local_worker.episode_schema import validate_record

LONE = "bad\ud800text"
ALL_TOOLS = ("list_dir", "read_file", "write_file", "edit_file", "run_tests", "finish", "search")

# every string argument of every tool, each with a lone surrogate in turn
CASES = [
    ("list_dir", {"path": "src"}, "path"),
    ("read_file", {"path": "src/app.py"}, "path"),
    ("write_file", {"path": "src/app.py", "content": "x = 2\n"}, "path"),
    ("write_file", {"path": "src/app.py", "content": "x = 2\n"}, "content"),
    ("edit_file", {"path": "src/app.py", "target": "x = 1", "replacement": "x = 2"}, "path"),
    ("edit_file", {"path": "src/app.py", "target": "x = 1", "replacement": "x = 2"}, "target"),
    ("edit_file", {"path": "src/app.py", "target": "x = 1", "replacement": "x = 2"}, "replacement"),
    ("search", {"pattern": "x", "path": "src"}, "pattern"),
    ("search", {"pattern": "x", "path": "src"}, "path"),
    ("finish", {"status": "DONE", "summary": "ok"}, "summary"),
    ("finish", {"status": "DONE"}, "status"),
]


def _call(name: str, args: dict) -> dict:
    return {"function": {"name": name, "arguments": json.dumps(args)}}


def _run(tmp_path: Any, replies: list):
    loop, world, _, _ = make_loop(tmp_path, replies)
    out = loop.run(initial_messages(TASK))
    rec = episode.build_record(META, out)
    validate_record(rec)
    return rec, world


@pytest.mark.parametrize(("name", "args", "key"), CASES)
def test_a_non_utf8_argument_is_a_bad_call(name, args, key):
    call = _call(name, {**args, key: LONE})
    assert "\\ud800" in call["function"]["arguments"]  # it arrives as a JSON escape
    with pytest.raises(BadCall, match="not valid UTF-8"):
        decode_call(call, 1, ALL_TOOLS)


@pytest.mark.parametrize(("name", "args", "key"), CASES)
def test_a_non_utf8_argument_ends_no_episode_as_internal_error(tmp_path, name, args, key):
    bad = reply(name, **{**args, key: LONE})
    rec, world = _run(tmp_path, [bad, reply("read_file", path="src/app.py")])
    first = rec["turns"][0]
    assert (first["action_type"], first["error_category"]) == ("invalid_tool_call", "INVALID_CALL")
    assert first["tool_ran"] is False
    assert [t["action_type"] for t in rec["turns"]] == ["invalid_tool_call", "read_file", "finish"]
    assert rec["verification"]["exit_reason"] == "COMPLETED"
    assert world.files["src/app.py"] == "x = 1\n"  # nothing executed
    assert "ud800" not in json.dumps(rec).lower()


def test_a_normal_utf8_argument_still_works(tmp_path):
    text = "x = '日本語 ✓ 😀'\n"
    write = reply("write_file", path="src/app.py", content=text)
    rec, world = _run(tmp_path, [write, reply("read_file", path="src/app.py")])
    assert world.files["src/app.py"] == text
    assert [t["action_type"] for t in rec["turns"]][:2] == ["write_file", "read_file"]
    assert rec["verification"]["exit_reason"] == "COMPLETED"
