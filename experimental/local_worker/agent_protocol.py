"""What the local worker's model sees and may call (#88): the system prompt, the tool schemas, and
the decoding of a tool call. ``run_tests`` takes no free-form arguments (Plan v2 point 2): the loop
runs ``pytest -q --tb=short -- <targets>`` over the task's declared public targets, or the one the
model picks by ``target_index``. ``search`` (read-only, a fixed string or a regex) is offered only
with Settings.search_tool (default on): ``tool_schemas``/``system_prompt`` give the frozen set.
``read_file`` takes an optional line range (start_line, end_line: 1-based, inclusive) in every
mode; reading.py slices the wrapper's output (and pages it with Limits.read_page_lines).
"""

from __future__ import annotations

import json
from typing import Any

from experimental.local_worker.tools import ToolCall

RUN_TESTS_ARGS = ("-q", "--tb=short", "--")
NUDGE = "No tool call received. Reply with exactly one tool call, or call finish."
_PROMPT = (
    "You are fixing one task in a Python repository. You act only through the tools list_dir, "
    "read_file, {search}write_file, edit_file, run_tests and finish; paths are relative to the "
    "repository root. Call exactly one tool per reply. run_tests runs the task's public tests "
    "(all of them, or the one named by target_index). When your change is complete, call finish "
    "with status DONE. If the task cannot be done because its specification lacks a fact (a "
    "name, a contract or the expected behaviour), call finish with status NEEDS_SPEC and say "
    "what is missing."
)
SYSTEM_PROMPT = _PROMPT.format(search="")


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    params = {"type": "object", "properties": props, "required": required}
    spec = {"name": name, "description": desc, "parameters": params}
    return {"type": "function", "function": spec}


_S = {"type": "string"}
_LINE = {"type": "integer", "minimum": 1}
TOOL_SCHEMAS = [
    _fn("list_dir", "List a directory of the repository.", {"path": _S}, ["path"]),
    _fn(
        "read_file",
        "Print a file of the repository, or its lines start_line to end_line (1-based, inclusive).",
        {"path": _S, "start_line": _LINE, "end_line": _LINE},
        ["path"],
    ),
    _fn("write_file", "Create or replace.", {"path": _S, "content": _S}, ["path", "content"]),
    _fn(
        "edit_file",
        "Replace the single exact occurrence of target in a file.",
        {"path": _S, "target": _S, "replacement": _S},
        ["path", "target", "replacement"],
    ),
    _fn(
        "run_tests",
        "Run the task's public tests: all of them, or one by index.",
        {"target_index": {"type": "integer"}},
        [],
    ),
    _fn(
        "finish",
        "End the task.",
        {"status": {"type": "string", "enum": ["DONE", "NEEDS_SPEC"]}, "summary": _S},
        ["status"],
    ),
]
SEARCH_SCHEMA = _fn(
    "search",
    "Find lines of the repository's files (read-only): a fixed string, or a regex if regex is true;"
    " under path (a file or directory, default the root).",
    {"pattern": _S, "path": _S, "regex": {"type": "boolean"}},
    ["pattern"],
)
BASE_TOOLS = tuple(t["function"]["name"] for t in TOOL_SCHEMAS)
TOOLS_ABSENT = ("search",)  # without Settings.search_tool (Plan v1 design item 1: declared)
_REQUIRED = {
    "list_dir": ("path",),
    "read_file": ("path",),
    "write_file": ("path", "content"),
    "edit_file": ("path", "target", "replacement"),
    "run_tests": (),
    "finish": ("status",),
    "search": ("pattern",),
}


ALLOWED_ARGS = {
    t["function"]["name"]: set(t["function"]["parameters"]["properties"])
    for t in [*TOOL_SCHEMAS, SEARCH_SCHEMA]
}


def tool_schemas(search: bool) -> list[dict]:
    """The tools the model is offered: search (before write_file) only when enabled."""
    return [*TOOL_SCHEMAS[:2], SEARCH_SCHEMA, *TOOL_SCHEMAS[2:]] if search else TOOL_SCHEMAS


def tool_names(search: bool) -> tuple[str, ...]:
    return tuple(t["function"]["name"] for t in tool_schemas(search))


def system_prompt(search: bool) -> str:
    return _PROMPT.format(search="search, ") if search else SYSTEM_PROMPT


class BadCall(ValueError):
    """A tool call the loop cannot run but the model can repair (unknown tool name, arguments
    that are not a JSON object, a missing, ill-typed or extra argument): the model is told why,
    the turn is recorded as ``invalid_tool_call`` (INVALID_CALL) with no arguments at all, and
    nothing executes. An extra argument is model text: neither its name nor its value is kept
    in the line (Claude review of #239, NB6: it no longer aborts the episode)."""


class ProtocolError(ValueError):
    """A reply that breaks the tool-call structure itself (tool_calls that is not a list of
    objects, a call or function that is not an object, a tool name or id that is not a string,
    arguments that are neither a JSON string nor an object): the episode stops with a recorded
    ERROR_ABORT (abort_cause protocol_error); nothing executes and no argument is published.
    Nori counts it as a model outcome, so it is not recoverable like a BadCall (ADDENDUM_05)."""


def _arguments(fn: dict) -> Any:
    raw = fn.get("arguments")
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ProtocolError("arguments are neither a JSON string nor an object")
    try:
        return json.loads(raw)
    except ValueError:
        raise BadCall("arguments are not valid JSON") from None


def decode_call(call: Any, n_targets: int, tools: tuple[str, ...] = BASE_TOOLS) -> tuple[str, dict]:
    """(tool name, arguments) of one call among ``tools`` (by default all but search); raises
    ProtocolError or BadCall (see above)."""
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        raise ProtocolError("a tool call must be an object with a function object")
    if call.get("id") is not None and not isinstance(call["id"], str):
        raise ProtocolError("a tool call id must be a string")
    fn = call["function"]
    name = fn.get("name")
    if not isinstance(name, str):
        raise ProtocolError("a tool name must be a string")
    if name not in tools or name not in _REQUIRED:
        raise BadCall("unknown tool")
    args = _arguments(fn)
    if not isinstance(args, dict):
        raise BadCall("arguments must be a JSON object")
    extra = set(args) - ALLOWED_ARGS[name]
    if extra:  # their names and values are model text: neither is kept in the public line
        raise BadCall(f"{name} got {len(extra)} argument(s) it does not take")
    for key in _REQUIRED[name]:
        if not isinstance(args.get(key), str):
            raise BadCall(f"{name} needs a string {key!r}")
    if name == "finish" and args["status"] not in ("DONE", "NEEDS_SPEC"):
        raise BadCall("finish status must be DONE or NEEDS_SPEC")
    if name == "finish" and not isinstance(args.get("summary", ""), str):
        raise BadCall("finish summary must be a string")
    if name == "search":
        _search_args(args)
    if name == "read_file":
        _range_args(args)
    index = args.get("target_index")
    if name == "run_tests" and index is not None:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < n_targets:
            raise BadCall(f"target_index must be an integer in [0, {n_targets})")
    return name, args


def _range_args(args: dict) -> None:
    start, end = args.get("start_line"), args.get("end_line")
    for value in (start, end):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise BadCall("start_line and end_line must be integers")
    if (start is not None and start < 1) or (end is not None and end < max(start or 1, 1)):
        raise BadCall("the line range needs 1 <= start_line <= end_line")


def _search_args(args: dict) -> None:
    if not args["pattern"] or "\n" in args["pattern"] or "\0" in args["pattern"]:
        raise BadCall("search pattern must be one non-empty line")
    if not isinstance(args.get("path", "."), str):
        raise BadCall("search path must be a string")
    if not isinstance(args.get("regex", False), bool):
        raise BadCall("search regex must be true or false")


def call_key(call: dict) -> tuple[str, str]:
    """(tool name, raw arguments) of a decoded call: what the repeat guard compares, byte for byte
    (arguments sent as an object are compared as their sorted JSON)."""
    fn = call["function"]
    raw = fn.get("arguments")
    return fn["name"], raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)


def render(call: ToolCall) -> str:
    rc = "timeout" if call.exit_code is None else str(call.exit_code)
    note = ", output truncated" if call.stdout_truncated else ""
    return f"[exit {rc}{note}]\n{call.output}"
