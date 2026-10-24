"""read_file ranges and pages (#88 robustness, L0 dry run 3): sliced in Python after the wrapper's
read, off by default (Limits.read_page_lines / read_page_bytes)."""

from __future__ import annotations

import json

import pytest
from local_worker_fakes import META, TASK, World, cli_harness, make_loop, reply

from experimental.local_worker import agent_protocol, episode, redact
from experimental.local_worker.agent import Limits, initial_messages
from experimental.local_worker.agent_protocol import BadCall, decode_call
from experimental.local_worker.episode_schema import SchemaError, validate_record
from experimental.local_worker.reading import page
from experimental.local_worker.toolcall import ToolCall

TEXT = "".join(f"line {k}\n" for k in range(1, 11))  # 10 lines of 7 bytes (line 10: 8)


def call(raw: bytes = TEXT.encode(), rc: int | None = 0, complete: bool = True) -> ToolCall:
    return ToolCall(
        turn_index=0,
        action_type="read_file",
        tool_args={},
        exit_code=rc,
        output=raw.decode(),
        stdout_bytes=len(raw),
        stdout_truncated=False,
        error_category="NONE",
        duration_ms=1.0,
        output_sha256="0" * 64,
        exec_identity="x",
        isolation="seatbelt-only",
        started_at="t",
        raw=raw,
        capture_complete=complete,
    )


# -- page() ----------------------------------------------------------------------------------------
def test_no_range_and_paging_off_is_passed_through_unchanged():
    c = call()
    assert page(c, None, None, None, None, 20_000) is c


def test_paging_shows_the_first_page_and_says_how_to_continue():
    out = page(call(), None, None, 4, None, 20_000)
    assert out.output == "line 1\nline 2\nline 3\nline 4\n" + (
        "[lines 1-4 of 10; call read_file with start_line=5 to continue]"
    )
    assert out.stdout_truncated and out.stdout_bytes == len(TEXT)  # the wrapper's whole output


def test_the_next_page_and_the_last():
    assert page(call(), 5, None, 4, None, 20_000).output.startswith("line 5\n")
    last = page(call(), 9, None, 4, None, 20_000).output
    assert last == "line 9\nline 10\n[lines 9-10 of 10; end of file]"


def test_a_range_is_served_with_paging_off_too():
    out = page(call(), 3, 4, None, None, 20_000)
    assert (
        out.output
        == "line 3\nline 4\n[lines 3-4 of 10; call read_file with start_line=5 to continue]"
    )
    whole = page(call(), 1, 99, None, None, 20_000)  # an end past the file is clamped
    assert whole.output.endswith("[lines 1-10 of 10; end of file]") and not whole.stdout_truncated


def test_a_range_is_capped_by_the_page():
    out = page(call(), 2, 9, 3, None, 20_000)
    assert out.output.endswith("[lines 2-4 of 10; call read_file with start_line=5 to continue]")


def test_the_byte_cap_takes_whole_lines_whichever_is_smaller():
    out = page(call(), None, None, 400, 15, 20_000)  # 2 lines are 14 bytes, 3 are 21
    assert (
        out.output
        == "line 1\nline 2\n[lines 1-2 of 10; call read_file with start_line=3 to continue]"
    )
    out = page(call(), None, None, 1, 16_000, 20_000)
    assert out.output.startswith("line 1\n[lines 1-1 of 10;")


def test_one_line_over_the_byte_cap_is_shown_clipped():
    raw = ("x" * 100 + "\nnext\n").encode()
    out = page(call(raw), None, None, 400, 40, 20_000)
    assert "truncated" in out.output and out.output.endswith("start_line=2 to continue]")


def test_a_start_past_the_end_says_so():
    out = page(call(), 11, None, 4, None, 20_000)
    assert out.output == "[start_line 11 is past the end: the file has 10 lines]"


@pytest.mark.parametrize("c", [call(rc=64), call(rc=None), call(complete=False)])
def test_a_failed_or_capped_read_is_not_paged(c):
    assert page(c, 2, 3, 4, 100, 20_000) is c


def test_a_last_line_without_newline_gets_one_before_the_note():
    out = page(call(b"a\nb"), None, None, 5, None, 20_000)
    assert out.output == "a\nb\n[lines 1-2 of 2; end of file]"


# -- protocol, limits, redaction and schema --------------------------------------------------------
def read_call(**args) -> dict:
    return {"id": "c", "function": {"name": "read_file", "arguments": json.dumps(args)}}


@pytest.mark.parametrize(
    "args",
    [
        {"path": "a", "start_line": 0},
        {"path": "a", "start_line": True},
        {"path": "a", "end_line": 2.0},
        {"path": "a", "start_line": 5, "end_line": 4},
        {"path": "a", "start_line": "1"},
        {"path": "a", "end_line": 0},
    ],
)
def test_a_bad_range_is_a_bad_call(args):
    with pytest.raises(BadCall):
        decode_call(read_call(**args), 1)


def test_a_good_range_decodes():
    assert decode_call(read_call(path="a", start_line=3, end_line=3), 1)[1]["end_line"] == 3
    assert decode_call(read_call(path="a", end_line=7), 1)[1] == {"path": "a", "end_line": 7}


def test_the_schema_offers_the_range_in_every_mode():
    for search in (True, False):
        [read] = [
            t for t in agent_protocol.tool_schemas(search) if t["function"]["name"] == "read_file"
        ]
        props = read["function"]["parameters"]["properties"]
        assert set(props) == {"path", "start_line", "end_line"}


@pytest.mark.parametrize(
    "limits",
    [
        {"read_page_lines": 0},
        {"read_page_lines": 4, "read_page_bytes": 0},
        {"read_page_bytes": 100},
    ],
)
def test_paging_numbers_are_checked(limits):
    with pytest.raises(ValueError):
        Limits(context_cap=1, **limits)


def test_paging_is_off_by_default():
    lim = Limits(context_cap=1)
    assert (lim.read_page_lines, lim.read_page_bytes) == (None, None)


def test_the_range_is_published_as_ints():
    args = {"path": "src", "start_line": 3, "end_line": 9}
    assert redact.public_args("read_file", args, 0, frozenset({"src"})) == args


# -- the loop --------------------------------------------------------------------------------------
def run(tmp_path, replies, **limits):
    world = World({"src/app.py": TEXT, "tests/test_app.py": "def test(): 0\n"})
    loop, world, endpoint, _ = make_loop(tmp_path, replies, world, **limits)
    rec = episode.build_record(META, loop.run(initial_messages(TASK)))
    validate_record(rec)
    return rec, world, endpoint


def seen(endpoint, k: int) -> str:
    return endpoint.payloads[k]["messages"][-1]["content"]


def test_the_loop_pages_a_read_and_records_the_range(tmp_path):
    replies = [
        reply("read_file", path="src/app.py"),
        reply("read_file", path="src/app.py", start_line=5),
    ]
    rec, world, endpoint = run(tmp_path, replies, read_page_lines=4, read_page_bytes=16_000)
    assert seen(endpoint, 1).endswith(
        "[lines 1-4 of 10; call read_file with start_line=5 to continue]"
    )
    assert seen(endpoint, 2).startswith("[exit 0, output truncated]\nline 5\n")
    first, second = rec["turns"][:2]
    assert first["tool_args"] == {"path": "src/app.py"} and first["stdout_truncated"] is True
    assert second["tool_args"] == {"path": "src/app.py", "start_line": 5}
    assert first["stdout_bytes"] == len(TEXT)
    assert [c[1] for c in world.calls if c[0] == "read"] == [["src/app.py"]] * 2  # the same argv


def test_with_paging_off_a_read_is_what_it_was(tmp_path):
    rec, _, endpoint = run(tmp_path, [reply("read_file", path="src/app.py")])
    assert seen(endpoint, 1) == "[exit 0]\n" + TEXT
    assert rec["turns"][0]["stdout_truncated"] is False


def test_a_bad_range_in_the_loop_is_an_invalid_call(tmp_path):
    rec, world, _ = run(tmp_path, [reply("read_file", path="src/app.py", start_line=0)])
    assert (
        rec["turns"][0]["error_category"] == "INVALID_CALL" and rec["turns"][0]["tool_args"] == {}
    )
    assert "read" not in [c[0] for c in world.calls]


def test_the_schema_refuses_a_line_number_below_one(tmp_path):
    rec, _, _ = run(tmp_path, [reply("read_file", path="src/app.py", start_line=2)])
    rec["turns"][0]["tool_args"]["start_line"] = 0
    with pytest.raises(SchemaError, match="line number"):
        validate_record(rec)


# -- the manifest ----------------------------------------------------------------------------------
def test_the_manifest_freezes_the_paging_numbers(tmp_path, monkeypatch):
    run_cli, out = cli_harness(tmp_path, monkeypatch)
    assert run_cli([], "run") == 0
    lim = json.loads((out / "manifest.json").read_text())["limits"]
    assert (lim["read_page_lines"], lim["read_page_bytes"]) == (None, None)
    (tmp_path / "b").mkdir()
    run_cli, out = cli_harness(tmp_path / "b", monkeypatch)
    assert run_cli([], "run", "--read-page-lines", "400", "--read-page-bytes", "16000") == 0
    lim = json.loads((out / "manifest.json").read_text())["limits"]
    assert (lim["read_page_lines"], lim["read_page_bytes"]) == (400, 16000)
    assert run_cli([], "run", "--read-page-bytes", "16000") == 2  # bytes need lines: refused
