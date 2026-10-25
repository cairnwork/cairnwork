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
        "((a{3})*){2}",  # (a{3})* matches the empty string
        "a{101}",
        "a{1,101}",
        "a{101,}",
        "x" * 513,
        "é" * 257,  # 514 bytes
        "^[[:alpha:][]((a{100}){100}){100}$",  # Codex round 2: [ is literal, [] closes nothing
        "[[:alpha:][]a{101}",
        "^(" + "a?" * 40 + "){100}$",  # Codex round 3: 89 bytes, 494 MB in grep
        "[]a](a{2}){3}" + "(a{100}){6}",
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
        "x" * 512,  # a regex of plain characters always passes (MAX_POSITIONS)
        r"def \w+\(",
        "class [A-Z][a-zA-Z]+",
        "TODO|FIXME",
        "^import (os|sys)$",
        r"[0-9]{1,3}(\.[0-9]{1,3}){3}",
        "(foo|bar)+baz",
        r"^\s*(async )?def [a-z_]+\(self",
        "foo.{0,40}bar",
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


@pytest.mark.parametrize(
    "pattern",
    ["((a){2}){2}", "(a{,3}){2}", "((b)+c{2}){4}", "(a{3}){2}", "(x(a{2}|b)){2}", "[]a](a{2}){3}"]
    + ["[^]a](a{2}){3}", "((ab){5}){4}"],
)
def test_a_cheap_nesting_passes(pattern):
    """Codex round 3 of PR 251: the expanded-size budget supersedes the old no-nesting rule, so a
    nesting whose expansion is small is no longer refused."""
    assert _decode_search(pattern)["pattern"] == pattern


@pytest.mark.parametrize(
    "pattern",
    [
        r"^(a+)+\1$",
        r"(a)\1",
        r"(a)(b)(c)(d)(e)(f)(g)(h)(i)\9",
        r"x\2y",
        r"(a)\1{2}",
        r"^[[:alpha:][](a+)+\1$",  # Codex round 2: the class's ] does not close the bracket
        r"[[.].]](a)\1",
        r"[\](a)\1]",  # the backslash is literal, so [\] closes and \1 is outside
    ],
)
def test_a_back_reference_is_a_bad_call(pattern):
    """Codex review of PR 251, must-fix 2: grep backtracks on a back-reference."""
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall, match="back-reference"):
        _decode_search(pattern)


@pytest.mark.parametrize(
    "pattern", [r"\\1", r"a\.1", r"[\1]", r"\0", r"(a+)+$", r"[[:alpha:]\1]", r"[x\]\\]"]
)
def test_an_escaped_backslash_a_bracket_or_a_digit_alone_is_no_back_reference(pattern):
    """Inside a bracket a backslash is a character (POSIX): [[:alpha:]\\1] is letters, \\ or 1."""
    assert _decode_search(pattern)["pattern"] == pattern


# -- bracket expressions as grep -E reads them (Codex round 2 of PR 251) ---------------------------
@pytest.mark.parametrize(
    "pattern",
    [
        "[]a]",
        "[^]a]",
        "[a-z]",
        "[[:digit:]]+",
        "[[.-.]]",
        "[[=a=]]",
        "[[.].]]",  # the collating element ] is one item
        "[[...]]",
        "[[a]",  # a [ opening no item is a character
        "[[:alpha:][]",
        "[^[:space:][:punct:]]{3}",
        "[[:alnum:][:blank:][:cntrl:][:graph:][:lower:][:print:][:upper:][:xdigit:]]",
        "[a-]x",
        "[!-]]",  # the bracket is [!-]; the second ] is a character outside it
    ],
)
def test_posix_bracket_expressions_pass(pattern):
    assert _decode_search(pattern)["pattern"] == pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "[abc",
        "a[",
        "[]",
        "[^]",
        "[[:alpha:]",
        "[[:foo:]]",  # unknown to this check: refused rather than guessed
        "[[:alpha]",
        "[[:ALPHA:]]",
        "[[:rune:]]",
        "[[:]",
        "[[.ab.]]",
        "[[.SPACE.]]",  # names are case-sensitive to grep too
        "[[=a]",
        "[[==]]",
        "[[..]]",
    ],
)
def test_a_bracket_the_check_cannot_parse_is_a_bad_call(pattern):
    """Fail closed: the check never skips text it could not delimit."""
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall, match="bracket expression"):
        _decode_search(pattern)


@pytest.mark.parametrize(
    "pattern",
    ["[[.space.]]", "[[.hyphen.]]", "[[.period.]]", "[[=space=]]", "[[.left-square-bracket.]]"]
    + ["[[.NUL.]]", "[[.tab.]]", "[[.full-stop.]]x", "[[.zero.]-[.nine.]]", "[[.space.]-a]"],
)
def test_a_named_collating_element_passes(pattern):
    """Claude review of a91cdf7 (NIT 1): grep reads BSD regcomp's names; unknown ones stay
    refused (above)."""
    assert _decode_search(pattern)["pattern"] == pattern


@pytest.mark.parametrize(
    "pattern",
    ["[[:alpha:]-z]", "[a-[:alpha:]]", "[[=a=]-z]", "[a-[=z=]]", "[z-a]", "[a-b-c]", "[a-z-9]"]
    + ["[0-9-a]", "[!--]", "[Z-a]", "[a-~]", "[ä-a]", "[ê-é]", r"[a-\]", "[]- ]", "[[.z.]-a]"]
    + ["[1-а]", "[а-B]", "[a-é]", "[é-ê]", "[а-я]", "[一-龥]"],  # not ASCII: refused (_ORDER)
)
def test_a_range_grep_rejects_is_a_bad_call(pattern):
    """Claude review of a91cdf7 (NIT 2): grep -E exits 2 ("invalid character range") on the first
    two rows, so the model gets a BadCall instead of a tool error. grep orders ASCII by its
    locale's collation (_ORDER), not by code point: [Z-a] is refused, [A-z] passes. A range with
    a non-ASCII endpoint is refused whatever grep makes of it (collation again, unpredictable)."""
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall, match="range grep rejects"):
        _decode_search(pattern)


@pytest.mark.parametrize(
    "pattern",
    ["[A-z]", "[a-a]", "[--/]", "[a-z-]", "[-a]", "[]-a]", "[^]-a]", "[[.a.]-z]"]
    + ["[a-[.z.]]", "[ -~]", "[^--0]", "[x^-a]", "[[-a]", "[éa-z]", "[一龥]"],
)
def test_a_range_grep_accepts_passes(pattern):
    assert _decode_search(pattern)["pattern"] == pattern


def test_a_back_reference_in_a_fixed_string_is_text():
    assert _decode_search(r"^(a+)+\1$", regex=False)["regex"] is False


def test_a_fixed_string_is_never_parsed_as_a_regex():
    assert _decode_search("((a{255}){255}){255}", regex=False)["regex"] is False
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall):
        _decode_search("x" * 513, regex=False)  # the length cap holds in both modes


def test_the_loop_returns_the_cap_to_the_model(tmp_path):
    from local_worker_fakes import reply

    loop, world, endpoint, _ = make_loop(
        tmp_path, [reply("search", pattern="((a{100}){100}){100}", regex=True)]
    )
    rec = episode.build_record(META, loop.run(initial_messages(TASK)))
    assert rec["turns"][0]["error_category"] == "INVALID_CALL"
    assert "too costly" in endpoint.payloads[1]["messages"][-1]["content"]
    assert "search" not in [c[0] for c in world.calls]
