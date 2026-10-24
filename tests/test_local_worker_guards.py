"""The repeat guard and the invalid-call limit of the local worker loop (#88 robustness).
Defaults are Nori's ADDENDUM_05: hint off, stop at the 5th identical call with an unchanged tree,
6 invalid calls in a row; a structurally broken reply still aborts."""

from __future__ import annotations

import json

import pytest
from local_worker_fakes import META, TASK, make_loop, reply

from experimental.local_worker import episode
from experimental.local_worker.agent import Limits, initial_messages
from experimental.local_worker.episode_schema import SchemaError, validate_record

READ = reply("read_file", path="src/app.py")
OTHER = reply("read_file", path="tests/test_app.py")
WRITE = reply("write_file", path="src/new.py", content="n = 1\n")
NO_CALL = reply()
BAD = reply("no_such_tool", path="x")  # a BadCall: unknown tool
HINT_MODE = {"repeat_hint_at": 3, "repeat_stop_at": 6, "repeat_requires_unchanged_tree": False}


def bad_json() -> dict:
    r = reply("read_file", path="x")
    r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"path": '
    return r


INVALID = [
    BAD,
    bad_json(),
    reply("read_file"),  # a missing argument
    reply("read_file", path="src/app.py", mode="r"),  # an extra argument
    reply("finish", status="MAYBE"),
    reply("search", pattern="a\nb"),
]


def broken() -> dict:
    """A reply whose tool_calls breaks the structure (a ProtocolError)."""
    msg = {"role": "assistant", "content": None, "tool_calls": {"not": "a list"}}
    return {"model": "served-model", "choices": [{"message": msg}], "usage": None}


def run(tmp_path, replies, **limits):
    loop, world, endpoint, _ = make_loop(tmp_path, replies, **limits)
    out = loop.run(initial_messages(TASK))
    rec = episode.build_record(META, out)
    validate_record(rec)
    return rec, world, endpoint


def ran(world, action="read") -> int:
    return [c[0] for c in world.calls].count(action)


def categories(rec) -> list[str]:
    return [t["error_category"] for t in rec["turns"]]


def sent_text(endpoint) -> str:
    return json.dumps(endpoint.payloads)


def test_the_defaults_are_addendum_05():
    lim = Limits(context_cap=1)
    assert (lim.repeat_hint_at, lim.repeat_stop_at, lim.repeat_requires_unchanged_tree) == (
        None,
        5,
        True,
    )
    assert (lim.invalid_call_limit, lim.context_window) == (6, 65536)


# -- (A) the repeat guard, defaults ---------------------------------------------------------------
def test_the_fifth_identical_call_ends_the_episode_and_nothing_is_hinted(tmp_path):
    rec, world, endpoint = run(tmp_path, [READ] * 9)
    assert rec["verification"]["exit_reason"] == "REPEAT_LIMIT"
    assert rec["termination"]["limit_hit"] == "repeat_stop_at"
    assert rec["total_turns"] == 5 and len(endpoint.payloads) == 5
    assert categories(rec) == ["NONE"] * 4 + ["REPEATED"] and ran(world) == 4
    assert rec["turns"][4]["tool_ran"] is False and rec["turns"][4]["exit_code"] is None
    assert "Not run" not in sent_text(endpoint)  # the model never saw anything new
    assert rec["termination"]["cleanup"] == "ok"


def test_four_identical_calls_are_all_run(tmp_path):
    rec, world, endpoint = run(tmp_path, [READ] * 4)
    assert rec["verification"]["exit_reason"] == "COMPLETED" and ran(world) == 4
    assert "REPEATED" not in categories(rec) and "Not run" not in sent_text(endpoint)


@pytest.mark.parametrize("between", [OTHER, NO_CALL, BAD])
def test_any_other_turn_in_between_resets_the_count(tmp_path, between):
    rec, world, _ = run(tmp_path, [READ] * 4 + [between] + [READ] * 4)
    assert rec["verification"]["exit_reason"] == "COMPLETED"
    assert ran(world) == 8 + (between is OTHER) and "REPEATED" not in categories(rec)


def test_identity_is_the_name_and_the_bytes_of_the_arguments(tmp_path):
    spaced = reply("read_file", path="src/app.py")
    spaced["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"path": "src/app.py"}, indent=1
    )
    rec, world, _ = run(tmp_path, [READ] * 4 + [spaced] + [READ] * 4)
    assert ran(world) == 9  # the same call, other bytes: not a repeat
    rec, world, _ = run(tmp_path / "b", [READ] * 4 + [reply("list_dir", path="src/app.py")])
    assert ran(world, "list") == 1


def test_a_call_that_changed_the_tree_starts_the_count_again(tmp_path):
    rec, world, _ = run(tmp_path, [WRITE] * 9)
    # The first write changed the tree, so the second starts the count: writes 2-5 count 1-4
    # and the sixth is the fifth identical call on an unchanged tree.
    assert rec["verification"]["exit_reason"] == "REPEAT_LIMIT"
    assert ran(world, "write") == 5 and rec["total_turns"] == 6


def test_without_the_tree_rule_every_identical_call_counts(tmp_path):
    rec, world, _ = run(tmp_path, [WRITE] * 9, repeat_requires_unchanged_tree=False)
    assert ran(world, "write") == 4 and rec["total_turns"] == 5


# -- (A) the hint mode: implemented, configurable, off by default ----------------------------------
def test_with_the_hint_on_the_third_identical_call_is_not_run_and_the_model_is_told(tmp_path):
    rec, world, endpoint = run(tmp_path, [READ, READ, READ], **HINT_MODE)
    assert ran(world) == 2
    third = rec["turns"][2]
    assert third["error_category"] == "REPEATED" and third["tool_ran"] is False
    assert third["exit_code"] is None and third["exec_identity"] is None
    hint = endpoint.payloads[3]["messages"][-1]
    assert hint["role"] == "tool" and hint["content"].startswith("Not run")
    assert "would not change" in hint["content"] and "read_file, search" in hint["content"]
    assert rec["verification"]["exit_reason"] == "COMPLETED"


def test_with_the_hint_on_the_stop_still_comes(tmp_path):
    rec, world, endpoint = run(tmp_path, [READ] * 9, **HINT_MODE)
    assert rec["verification"]["exit_reason"] == "REPEAT_LIMIT" and rec["total_turns"] == 6
    assert categories(rec) == ["NONE", "NONE"] + ["REPEATED"] * 4 and ran(world) == 2


@pytest.mark.parametrize(
    "limits",
    [
        {"repeat_hint_at": 1},
        {"repeat_hint_at": 5},  # not below the default stop
        {"repeat_stop_at": 1},
        {"invalid_call_limit": 0},
        {"context_window": 4096},  # not above max_completion_tokens
    ],
)
def test_guard_numbers_are_checked(tmp_path, limits):
    with pytest.raises(ValueError):
        make_loop(tmp_path, [], **limits)


# -- (C) invalid calls ---------------------------------------------------------------------------
def test_six_invalid_calls_in_a_row_end_the_episode(tmp_path):
    rec, world, endpoint = run(tmp_path, [*INVALID, READ])
    assert rec["verification"]["exit_reason"] == "INVALID_TOOL_CALL_LIMIT"
    assert rec["termination"]["limit_hit"] == "invalid_call_limit"
    assert rec["termination"]["abort_cause"] is None and rec["total_turns"] == 6
    assert categories(rec) == ["INVALID_CALL"] * 6 and ran(world) == 0
    assert all(
        t["tool_args"] == {} and t["action_type"] == "invalid_tool_call" for t in rec["turns"]
    )
    # each error went back to the model as the reply to its call
    replies = [p["messages"][-1] for p in endpoint.payloads[1:]]
    assert [m["role"] for m in replies] == ["tool"] * 5
    assert "unknown tool" in replies[0]["content"] and "JSON" in replies[1]["content"]


def test_five_invalid_calls_do_not_end_it(tmp_path):
    rec, _, _ = run(tmp_path, INVALID[:5])
    assert rec["verification"]["exit_reason"] == "COMPLETED" and rec["total_turns"] == 6


@pytest.mark.parametrize("between", [READ, NO_CALL])
def test_a_valid_turn_resets_the_invalid_count(tmp_path, between):
    rec, _, _ = run(tmp_path, [BAD] * 5 + [between] + [BAD] * 5)
    assert rec["verification"]["exit_reason"] == "COMPLETED"


def test_the_invalid_limit_is_a_parameter(tmp_path):
    rec, _, _ = run(tmp_path, [BAD, BAD, READ], invalid_call_limit=2)
    assert rec["verification"]["exit_reason"] == "INVALID_TOOL_CALL_LIMIT"
    assert rec["total_turns"] == 2


def test_a_structurally_broken_reply_still_aborts(tmp_path):
    rec, _, _ = run(tmp_path, [BAD, BAD, broken(), READ])
    assert rec["verification"]["exit_reason"] == "ERROR_ABORT"
    assert rec["termination"]["abort_cause"] == "protocol_error"
    assert categories(rec) == ["INVALID_CALL", "INVALID_CALL", "PROTOCOL_ERROR"]


def test_endpoint_failures_still_abort(tmp_path):
    from experimental.local_worker.endpoint import EndpointError

    rec, _, _ = run(tmp_path, [READ, EndpointError("down")])
    assert rec["verification"]["exit_reason"] == "ERROR_ABORT"
    assert rec["termination"]["abort_cause"] == "endpoint_error"


# -- the schema: the new exit reasons go with their own limit_hit ----------------------------------
@pytest.mark.parametrize(
    ("exit_reason", "hit"),
    [
        ("REPEAT_LIMIT", "context_window"),
        ("REPEAT_LIMIT", None),
        ("TURN_LIMIT", "repeat_stop_at"),
        ("CONTEXT_LIMIT", "max_steps"),
        ("INVALID_TOOL_CALL_LIMIT", "repeat_stop_at"),
    ],
)
def test_a_guard_exit_needs_its_own_limit_hit(tmp_path, exit_reason, hit):
    rec, _, _ = run(tmp_path, [READ] * 9)
    rec["verification"]["exit_reason"] = rec["cut_900s"]["exit_reason"] = exit_reason
    rec["termination"]["limit_hit"] = hit
    with pytest.raises(SchemaError, match="limit_hit"):
        validate_record(rec)
