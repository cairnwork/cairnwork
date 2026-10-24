"""The search regex cost cap (review of PR 251, item 4, and its delta review, item 3): checked
in Python before the wrapper runs, a BadCall the model can repair."""

from __future__ import annotations

import json

import pytest
from local_worker_fakes import META, TASK, make_loop

from experimental.local_worker import episode
from experimental.local_worker.agent import initial_messages


# -- the regex cost cap (review of PR 251, 4) ------------------------------------------------------
def _decode_search(pattern: str, regex: bool = True) -> dict:
    from experimental.local_worker.agent_protocol import decode_call

    args = json.dumps({"pattern": pattern, "regex": regex})
    call = {"id": "c", "function": {"name": "search", "arguments": args}}
    return decode_call(call, 1, ("search",))[1]


@pytest.mark.parametrize(
    "pattern",
    [
        "((a{255}){255}){255}",
        "((a{100}){100}){100}",
        "(a{3}){2}",
        "((a{3})*){2}",
        "(x(a{2}|b)){2}",
        "a{101}",
        "a{1,101}",
        "a{101,}",
        "x" * 513,
        "é" * 257,  # 514 bytes
    ],
)
def test_a_costly_regex_is_a_bad_call(pattern):
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall):
        _decode_search(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "a{100}",
        "(ab){3}c{4}",
        "(a|b){2}(c){3}",
        "[{(]x{2}",
        r"\(a{3}\){3}",
        "[[:alpha:]]{3}",
        "x" * 512,
    ],
)
def test_ordinary_regexes_pass(pattern):
    assert _decode_search(pattern)["pattern"] == pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "(x = {}){2}",  # {} is no bound: TRE reads it literally
        "f({,}){2}",
        "(a{x}){2}",
        "([{}()]){5}",  # braces and parens inside [...] are characters, not structure
        "[(]a{2}[)]{3}",
        "[]{(]+(b){2}",  # a leading ] is part of the bracket expression
        "[^]{}]{3}",
        "(a[{2}]){3}",  # a bound-like text inside [...] is no bound, so this is no nesting
        "[a{101}]",  # nor a count over the cap
        "(a[]{2}]){3}",  # with a leading ], the bracket runs to the second ]
        "(a[^]{2}]){3}",
    ],
)
def test_braces_that_are_no_bound_and_brackets_pass(pattern):
    """Review of PR 251 delta (3): the bracket skip and the literal braces, pinned."""
    assert _decode_search(pattern)["pattern"] == pattern


@pytest.mark.parametrize("pattern", ["((a){2}){2}", "(a{,3}){2}", "((b)+c{2}){4}"])
def test_any_nesting_is_refused_with_a_way_out(pattern):
    """Kept over-rejection (documented): even a cheap nesting is refused, and the BadCall tells
    the model how to rewrite it."""
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall, match="write the inner part out"):
        _decode_search(pattern)


def test_a_fixed_string_is_never_parsed_as_a_regex():
    assert _decode_search("((a{255}){255}){255}", regex=False)["regex"] is False
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall):
        _decode_search("x" * 513, regex=False)  # the length cap holds in both modes


def test_the_loop_returns_the_cap_to_the_model(tmp_path):
    from local_worker_fakes import reply

    loop, world, endpoint, _ = make_loop(
        tmp_path, [reply("search", pattern="(a{2}){2}", regex=True)]
    )
    rec = episode.build_record(META, loop.run(initial_messages(TASK)))
    assert rec["turns"][0]["error_category"] == "INVALID_CALL"
    assert "nest" in endpoint.payloads[1]["messages"][-1]["content"]
    assert "search" not in [c[0] for c in world.calls]
