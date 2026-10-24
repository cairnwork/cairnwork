"""read_file ranges and pages (#88 robustness, L0 dry run 3): sliced in Python after the wrapper's
read; on by default (ADDENDUM_06: 400 lines or 16000 bytes, whichever comes first)."""

from __future__ import annotations

import json

import pytest
from local_worker_fakes import META, TASK, World, cli_harness, make_loop, reply

from experimental.local_worker import agent_protocol, episode, redact, tools
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


def test_a_last_line_without_newline_is_said_so_in_the_note():
    out = page(call(b"a\nb"), None, None, 5, None, 20_000)
    assert out.output == "a\nb\n[lines 1-2 of 2; end of file (no newline at end of file)]"
    out = page(call(b"a\nb"), 1, 1, 5, None, 20_000)  # not shown: not mentioned
    assert out.output == "a\n[lines 1-1 of 2; call read_file with start_line=2 to continue]"
    out = page(call(b"a\nb\n"), None, None, 5, None, 20_000)
    assert out.output == "a\nb\n[lines 1-2 of 2; end of file]"


@pytest.mark.parametrize(("start", "end"), [(None, None), (1, 5), (3, None)])
def test_an_empty_file_says_it_is_empty(start, end):
    out = page(call(b""), start, end, 400, 16_000, 20_000)
    assert out.output == "[the file is empty]" and out.stdout_truncated is False


def test_a_start_past_the_end_is_truncated():
    assert page(call(), 11, None, 4, None, 20_000).stdout_truncated is True


def test_a_range_from_a_later_line_to_the_end_is_truncated():
    """Review of PR 251 delta (4): the model saw lines 3-10 only."""
    out = page(call(), 3, None, None, None, 20_000)
    assert out.output.endswith("[lines 3-10 of 10; end of file]") and out.stdout_truncated is True


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
        {"read_page_lines": None},  # the default byte cap needs paging on
        {"max_output_bytes": 1},
    ],
)
def test_paging_numbers_are_checked(limits):
    with pytest.raises(ValueError):
        Limits(context_cap=1, **limits)


def test_the_defaults_are_addendum_06():
    lim = Limits(context_cap=1)
    assert (lim.read_page_lines, lim.read_page_bytes) == (400, 16000)
    assert lim.max_output_bytes == 20_000 == tools.DEFAULT_MAX_OUTPUT_BYTES
    assert Limits(context_cap=1, read_page_lines=None, read_page_bytes=None).read_page_lines is None


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
    off = {"read_page_lines": None, "read_page_bytes": None}
    rec, _, endpoint = run(tmp_path, [reply("read_file", path="src/app.py")], **off)
    assert seen(endpoint, 1) == "[exit 0]\n" + TEXT
    assert rec["turns"][0]["stdout_truncated"] is False


def test_by_default_a_read_is_paged_at_400_lines(tmp_path):
    world = World({"big.py": "".join(f"x{k}\n" for k in range(1, 1001))})
    loop, _, endpoint, _ = make_loop(tmp_path, [reply("read_file", path="big.py")], world)
    loop.run(initial_messages(TASK))
    text = seen(endpoint, 1)
    assert text.startswith("[exit 0, output truncated]\nx1\n") and "x400\n" in text
    assert "x401\n" not in text
    assert text.endswith("\n[lines 1-400 of 1000; call read_file with start_line=401 to continue]")


def test_by_default_a_page_stops_at_16000_bytes(tmp_path):
    world = World({"wide.py": "".join("y" * 99 + "\n" for _ in range(300))})  # 100-byte lines
    loop, _, endpoint, _ = make_loop(tmp_path, [reply("read_file", path="wide.py")], world)
    loop.run(initial_messages(TASK))
    assert seen(endpoint, 1).endswith(
        "[lines 1-160 of 300; call read_file with start_line=161 to continue]"
    )


def test_a_small_file_is_shown_whole_with_its_length(tmp_path):
    rec, _, endpoint = run(tmp_path, [reply("read_file", path="src/app.py")])
    assert seen(endpoint, 1) == "[exit 0]\n" + TEXT + "[lines 1-10 of 10; end of file]"
    assert rec["turns"][0]["stdout_truncated"] is False


def test_the_output_cap_is_the_frozen_limit(tmp_path):
    world = World()
    world.pytest_output = b"z" * 5000
    loop, _, endpoint, _ = make_loop(tmp_path, [reply("run_tests")], world, max_output_bytes=1000)
    loop.run(initial_messages(TASK))
    assert loop.tools.max_output_bytes == 1000
    assert "truncated" in seen(endpoint, 1) and len(seen(endpoint, 1)) < 1200


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
    assert (lim["read_page_lines"], lim["read_page_bytes"]) == (400, 16000)
    assert lim["max_output_bytes"] == 20_000
    (tmp_path / "b").mkdir()
    run_cli, out = cli_harness(tmp_path / "b", monkeypatch)
    off = ["--read-page-lines", "off", "--read-page-bytes", "off", "--max-output-bytes", "8000"]
    assert run_cli([], "run", *off) == 0
    lim = json.loads((out / "manifest.json").read_text())["limits"]
    assert (lim["read_page_lines"], lim["read_page_bytes"], lim["max_output_bytes"]) == (
        None,
        None,
        8000,
    )
    assert run_cli([], "run", "--read-page-lines", "off") == 2  # the byte cap needs lines


# -- line numbers as grep -n counts them (review of PR 251 delta, 1) -------------------------------
@pytest.mark.parametrize(
    "raw",
    [b"a\x0cb\nc\n", b"a\rb\nc\n", "a b\nc\n".encode(), "a\x85b\x1cx\nc\n".encode()],
)
def test_only_a_newline_ends_a_line(raw):
    out = page(call(raw), 2, 2, None, None, 20_000)
    assert out.output == "c\n[lines 2-2 of 2; end of file]"


def test_a_crlf_line_keeps_its_cr():
    out = page(call(b"a\r\nb\r\n"), 2, None, None, None, 20_000)
    assert out.output == "b\r\n[lines 2-2 of 2; end of file]"


def test_a_search_hit_line_number_is_a_start_line(tmp_path):
    world = World({"f.py": "a\x0cb\nneedle\n"})
    replies = [reply("search", pattern="needle"), reply("read_file", path="f.py", start_line=2)]
    loop, world, endpoint, _ = make_loop(tmp_path, replies, world)
    loop.run(initial_messages(TASK))
    assert "f.py:2:needle" in seen(endpoint, 1)
    assert seen(endpoint, 2) == "[exit 0, output truncated]\nneedle\n[lines 2-2 of 2; end of file]"


@pytest.mark.sandbox
def test_grep_numbers_lines_by_newline_only(tmp_path):
    """The real wrapper's search: a form feed does not start a line, as split_lines has it."""
    from local_worker_fakes import TAG, make_runs, run_wrapper

    runs = make_runs(tmp_path)
    (runs / TAG / "checkout" / "f.py").write_bytes(b"a\x0cb\nneedle\n")
    proc = run_wrapper(runs, "search", str(runs / TAG), "fixed", "needle", "f.py")
    assert proc.stdout == b"f.py:2:needle\n"


def test_the_byte_cap_counts_raw_bytes_not_characters():
    # 9 lines of 1000 two-byte characters (2001 bytes each): 7 lines (14007 bytes) fit in 16000,
    # 8 (16008) do not. Counting characters (1001 each) would take all 9 and leave _clip to cut
    # the middle of the page under a note that still claims whole lines (review of #251).
    raw = ("é" * 1000 + "\n").encode() * 9
    out = page(call(raw), None, None, 400, 16_000, 20_000)  # the ADDENDUM_06 page
    assert out.output.endswith("[lines 1-7 of 9; call read_file with start_line=8 to continue]")
    assert out.output.count("\n") == 7 and "bytes truncated" not in out.output


def test_a_clipped_line_is_cut_at_utf8_boundaries():
    """Codex review of PR 251, must-fix 3: the exact input, default paging (400 lines, 16000
    bytes, 20000 output): no replacement character, and the note counts the bytes left out."""
    raw = ("€" * 6000 + "\n").encode()
    lim = Limits(context_cap=1)
    out = page(
        call(raw), None, None, lim.read_page_lines, lim.read_page_bytes, lim.max_output_bytes
    )
    assert "\ufffd" not in out.output and out.stdout_truncated
    head, rest = out.output.split("\n[... ", 1)
    dropped, rest = rest.split(" bytes truncated ...]\n", 1)
    tail, note = rest.rsplit("\n", 1)
    assert note == "[lines 1-1 of 1; end of file]" and set(head) == set(tail) == {"€"}
    assert len(head.encode()) + int(dropped) + len(tail.encode()) + 1 == len(raw)  # +1: the "\n"
    assert len(head.encode()) + len(tail.encode()) + 1 <= 16_000 == lim.read_page_bytes


def test_clip_never_splits_a_character_at_any_limit():
    from experimental.local_worker.toolcall import _clip

    data = ("aé€😀" * 5).encode()  # 1-, 2-, 3- and 4-byte characters
    for limit in range(2, len(data)):
        text, clipped = _clip(data, limit)
        assert clipped and "\ufffd" not in text, limit
        head, rest = text.split("\n[... ", 1)
        dropped, tail = rest.split(" bytes truncated ...]\n", 1)
        kept = len(head.encode()) + len(tail.encode())
        assert kept <= limit and kept + int(dropped) == len(data), limit
        assert data.startswith(head.encode()) and data.endswith(tail.encode())
    assert _clip(b"\x80" * 10, 4)[0].count("\ufffd") == 0  # invalid UTF-8: cut, still bounded
