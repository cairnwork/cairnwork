"""The search regex cost cap (review of PR 251, item 4, and its delta review, item 3): checked
in Python before the wrapper runs, a BadCall the model can repair."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys

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
        "^[[:alpha:][]((a{100}){100}){100}$",  # Codex round 2: [ is literal, [] closes nothing
        "[[:alpha:][]a{101}",
        "[]a](a{2}){3}",
        "[^]a](a{2}){3}",
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
        "[[.space.]]",
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


# Pieces a model might combine; the dangerous ones must never survive the check.
PIECES = [
    *("a", "b", "a+", "a*", "(a+)+", "(a|aa)*", "a{100}", "(a{100}){100}", "(a)", r"\1", "(", ")"),
    *("[[:alpha:][]", "[]a]", "[^]a]", "[[.-.]]", "[[=a=]]", "[a-z]", "[[:digit:]]+", r"[\1]"),
    *("[", "]", "{2}", "{100}", "*", "^", "$", "|", r"[[:alpha:]\1]", "[[a]", r"\\", r"\(", "[\\]"),
]
ADVERSARIAL = "a" * 40 + "b\n" + "a" * 200 + "\n[[[]]]::..--\\1\\1\n" + "ab" * 50 + "\n"


def _grep_cost(pattern: str, path: str, cpu: int) -> tuple[int, int]:
    """grep -E (the wrapper's mode) under ulimit -t ``cpu``: (exit status or -signal, peak RSS)."""
    script = 'ulimit -t "$1"; exec /usr/bin/grep -E -e "$2" -- "$3"'
    env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}
    argv = ["/bin/sh", "-c", script, "sh", str(cpu), pattern, path]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    _, status, usage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return proc.returncode, usage.ru_maxrss  # bytes on macOS


@pytest.mark.skipif(sys.platform != "darwin", reason="the oracle is macOS grep, as the wrapper's")
def test_every_accepted_pattern_is_cheap_for_the_real_grep(tmp_path):
    """Property: of generated patterns, each one the check accepts finishes on macOS grep -E under
    a 2-second CPU limit, in bounded memory, on an adversarial input."""
    from experimental.local_worker.agent_protocol import BadCall

    path = tmp_path / "adversarial.txt"
    path.write_text(ADVERSARIAL)
    rng = random.Random(251)
    patterns = {"".join(rng.choice(PIECES) for _ in range(rng.randint(1, 5))) for _ in range(600)}
    patterns |= {r"^[[:alpha:][](a+)+\1$", "^[[:alpha:][]((a{100}){100}){100}$"}
    accepted, refused = [], 0
    for pattern in sorted(patterns):
        try:
            _decode_search(pattern)
        except BadCall:
            refused += 1
            continue
        accepted.append(pattern)
        status, rss = _grep_cost(pattern, str(path), 2)
        assert status >= 0, (pattern, status)  # no SIGXCPU, no other signal
        assert rss < 256 * 2**20, (pattern, rss)
    assert len(accepted) > 100 and refused > 100, (len(accepted), refused)


@pytest.mark.skipif(sys.platform != "darwin", reason="the oracle is macOS grep, as the wrapper's")
def test_the_oracle_bites_on_the_codex_pattern(tmp_path):
    """The control for the property: the first Codex pattern runs out of CPU on the same grep."""
    path = tmp_path / "br.txt"
    path.write_text("a" * 40 + "b\n")
    assert _grep_cost(r"^[[:alpha:][](a+)+\1$", str(path), 1)[0] == -24  # SIGXCPU


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
        tmp_path, [reply("search", pattern="(a{2}){2}", regex=True)]
    )
    rec = episode.build_record(META, loop.run(initial_messages(TASK)))
    assert rec["turns"][0]["error_category"] == "INVALID_CALL"
    assert "nest" in endpoint.payloads[1]["messages"][-1]["content"]
    assert "search" not in [c[0] for c in world.calls]
