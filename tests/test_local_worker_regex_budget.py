"""The search regex budget (Codex round 3 of PR 251): regex_cap's expanded-size bound, its
no-repeat-of-the-empty-string rule, and macOS grep -E as the oracle for both."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys

import pytest

from experimental.local_worker import regex_cap

CODEX_ROUND_3 = "^(" + "a?" * 40 + "){100}$"
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="the oracle is macOS grep")


def _decode_search(pattern: str) -> dict:
    from experimental.local_worker.agent_protocol import decode_call

    args = json.dumps({"pattern": pattern, "regex": True})
    call = {"id": "c", "function": {"name": "search", "arguments": args}}
    return decode_call(call, 1, ("search",))[1]


def _refused(pattern: str, match: str) -> None:
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall, match=match):
        _decode_search(pattern)


def test_the_codex_round_3_pattern_is_refused_by_both_rules():
    assert len(CODEX_ROUND_3.encode()) == 89
    _refused(CODEX_ROUND_3, "empty string")
    # the same shape with nothing nullable is still over the size budget
    _refused("^(" + "a?" * 40 + "b){100}$", "too costly")
    _refused("^(" + "a?" * 40 + "b){12}$", "too costly")


@pytest.mark.parametrize(
    "pattern",
    ["((a{255}){255}){255}", "((a{100}){100}){100}", "^[[:alpha:][]((a{100}){100}){100}$"]
    + ["(a{100}){100}", "(a{1,100}){100}", "(" + "|".join("abcdefghij") + "){100}"]
    + ["((a|aa|aaa){100})*x", "a{100}" * 5 + "a{13}", ".{100}.{100}", r"(\w{100}){2}"]
    + ["(a{100}){5,}", "(a{50}){10,}", "[a-z]{0,100}[a-z]{0,100}", ".{1,100}.{1,100}"]
    + ["[" + "a" * 490 + "]+x"],  # over the transitions budget alone (494 x 494 > 100,000)
)
def test_a_pattern_over_budget_is_refused(pattern):
    from experimental.local_worker.agent_protocol import BadCall

    with pytest.raises(BadCall):
        _decode_search(pattern)


def test_the_budget_edge():
    """512 positions pass, 513 do not; the docstring's measurements are of these edges."""
    assert regex_cap.cost("a{100}" * 5 + "a{11}b")[0] == 512
    _refused("a{100}" * 5 + "a{12}b", "too costly")
    assert _decode_search("((a|aa|aaa){20})*x")
    _refused("((a|aa|aaa){30})*x", "too costly")  # 181 positions, but 92 tags


@pytest.mark.parametrize(
    "pattern",
    ["(a?)+", "(a*){5}", "()*", "(^)+", r"(\<)*", "(a?b?)+", "((a)?)+", "^*", "(()|a)+"]
    + ["(a{0}){3}", "(a{0,3})+", "(a?){1}", "((a*)(b*)){2}", "(a|b*|c)?", "(a?a?a?a?){2,}"],
)
def test_a_repetition_of_what_can_match_nothing_is_refused(pattern):
    _refused(pattern, "match the empty string")


@pytest.mark.parametrize("pattern", ["(|a)+", "(a|)*", "|a", "a|", "a||b", "(a|)", "(|a)"])
def test_an_empty_alternative_is_refused(pattern):
    """grep -E refuses these ("empty (sub)expression"); () alone it accepts, and so does this."""
    _refused(pattern, "empty alternative")


@pytest.mark.parametrize(
    "pattern",
    ["a**", "a+?", "a?*", "a{2}{3}", "a*{2}", "a{2}+", "*a", "{2}", "(+a)", "a|*b", "(*)"],
)
def test_a_repetition_after_another_or_of_nothing_is_refused(pattern):
    _refused(pattern, "repetition right after another")


@pytest.mark.parametrize("pattern", ["a{1,2", "a{2,1}", "a{1x}", "(a", "((a)", "a\\", "(a|b"])
def test_what_the_check_cannot_read_is_refused(pattern):
    """Fail closed; grep would refuse these too (braces not balanced, trailing backslash...)."""
    _refused(pattern, "not one this check can read")


@pytest.mark.parametrize("pattern", ["a{,3}", "a*{x}", "a{}", "a{ 1}", "a)", "x{,}"])
def test_braces_and_parentheses_grep_reads_as_text_pass(pattern):
    assert _decode_search(pattern)["pattern"] == pattern


def test_groups_nest_at_most_max_depth():
    deep = regex_cap.MAX_DEPTH
    assert _decode_search("(" * deep + "a" + ")" * deep)
    _refused("(" * (deep + 1) + "a" + ")" * (deep + 1), "nest groups")


# -- macOS grep as the oracle ----------------------------------------------------------------------
def _grep(pattern: str, path: str, cpu: int) -> tuple[int, int, float]:
    """grep -E (the wrapper's mode) under ulimit -t ``cpu``: (exit status or -signal, peak RSS in
    bytes, CPU seconds)."""
    script = 'ulimit -t "$1"; exec /usr/bin/grep -E -e "$2" -- "$3"'
    env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}
    argv = ["/bin/sh", "-c", script, "sh", str(cpu), pattern, path]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    _, status, usage = os.wait4(proc.pid, 0)
    return os.waitstatus_to_exitcode(status), usage.ru_maxrss, usage.ru_utime + usage.ru_stime


# Pieces a model might combine, with nullable groups and bounded repetitions among them.
PIECES = [
    *("a", "b", "x", "aa", ".", "a+", "a*", "(a+)+", "(a|aa)*", "a{100}", "(a{100}){100}", "(a)"),
    *(r"\1", "(", ")", "[[:alpha:][]", "[]a]", "[^b]", "[[=a=]]", "[a-z]", "[[:digit:]]+", r"\w"),
    *("[", "]", "{2}", "{100}", "{1,100}", "{0,100}", "{50,}", "{30}", "*", "?", "+", "^", "$"),
    *("|", r"\\", r"\(", "(a?)", "(a*)", "(|a)", "()", "(a?a?a?a?a?a?a?a?a?a?)", "(a|aa|aaa)"),
    *("(a?b)", "(.?a)", "([a-z]{1,5}x)", "[[:alpha:]-z]", "[[.space.]]"),
]
# The slowest accepted patterns measured (regex_cap's docstring): one atom repeated to the edge
# of the budget, then a b the input never has, so grep keeps every state on every line.
EDGES = ["a{100}" * 5 + "a{11}b", ".{100}.{66}b", "[[=a=]]{99}b", "[[:alpha:]]{99}b", r"\w{99}b"]
EDGES += ["((a|aa|aaa){20})*x", "(a|aa|aaa){20}x", "((a|aa){35})*x", "(a{50}){9}b"]
MIB_SHARE = 8  # the input is 1/8 MiB, so 2 s per MiB is 0.25 s here


def _adversarial(path) -> None:
    """1/8 MiB that the generated patterns almost match everywhere: long lines of a, of
    accented a, and the short mixed lines of the earlier property."""
    dense = ("a" * 4095 + "\n") * 16
    accents = ("aáàâäãåāăąǎ" * 186 + "\n") * 8  # 8 lines of 2,046 characters, 3.9 KB each
    mixed = "a" * 40 + "b\n" + "ab" * 50 + "\n[[[]]]::..--\\1\\1\n"
    path.write_text(dense + accents + mixed)
    assert len(path.read_bytes()) <= 2**20 // MIB_SHARE


GROW = [
    *("a", "b", "aa", ".", "a+", "(a+)+", "(a|aa)*", "a{100}", "[^b]", "[[=a=]]", "[a-z]", r"\w"),
    *("(a?b)", "(.?a)", "([a-z]{1,5}x)", "[[:alpha:]]{99}", ".{100}", "(a|aa|aaa){5}", "a{1,50}"),
]


def _grown(rng: random.Random) -> str:
    """A pattern at the budget's edge, then a b it never matches: random parts (each grouped
    and bounded, or not) added while the check accepts the result, until 20 in a row fail."""
    pattern, misses = "", 0
    while misses < 20:
        part, bound = rng.choice(GROW), rng.choice(["", "", "{100}", "{1,9}", "{20}", "+"])
        grown = pattern + (f"({part}){bound}" if bound else part)
        try:
            regex_cap.cost(grown + "b")
        except regex_cap.RegexRefused:
            misses += 1
            continue
        pattern, misses = grown, 0
    return pattern + "b"


@darwin
def test_every_accepted_pattern_is_cheap_for_the_real_grep(tmp_path):
    """Property: of generated patterns, each one the check accepts runs on macOS grep -E in under
    64 MiB and under 2 CPU seconds per MiB of adversarial input (on the M5 the budgets were
    measured on; a slower host gets 1.5 times what the slowest edge pattern takes on it); the
    Codex patterns are among the refused."""
    from experimental.local_worker.agent_protocol import BadCall

    path = tmp_path / "adversarial.txt"
    _adversarial(path)
    rng = random.Random(251)
    patterns = {"".join(rng.choice(PIECES) for _ in range(rng.randint(1, 5))) for _ in range(700)}
    patterns |= {_grown(rng) for _ in range(24)} | set(EDGES)
    controls = {CODEX_ROUND_3, r"^[[:alpha:][](a+)+\1$", "^[[:alpha:][]((a{100}){100}){100}$"}
    limit = max(2 / MIB_SHARE, 1.5 * _grep(EDGES[0], str(path), 2)[2])
    accepted, refused = [], set()
    for pattern in sorted(patterns | controls):
        try:
            _decode_search(pattern)
        except BadCall:
            refused.add(pattern)
            continue
        accepted.append(pattern)
        status, rss, cpu = _grep(pattern, str(path), 2)
        assert status in (0, 1), (pattern, status)  # matched or not; no error, no signal
        assert rss < 64 * 2**20, (pattern, rss)
        assert cpu < limit, (pattern, cpu, limit)
    assert controls <= refused and not set(EDGES) & refused
    nullable = [p for p in refused if "(a?)" in p or "(|a)" in p or "()" in p]
    assert len(accepted) > 150 and len(refused) > 150 and len(nullable) > 20


@darwin
def test_the_oracle_bites_on_the_codex_patterns(tmp_path):
    """The controls for the property: the Codex patterns are costly on the same grep."""
    path = tmp_path / "br.txt"
    path.write_text("a" * 40 + "b\n")
    assert _grep(r"^[[:alpha:][](a+)+\1$", str(path), 1)[0] == -24  # SIGXCPU
    path.write_text("b\n")
    status, rss, _ = _grep(CODEX_ROUND_3, str(path), 1)
    assert status == -24 or rss > 64 * 2**20, (status, rss)


@darwin
def test_grep_reads_every_collating_name_the_check_accepts(tmp_path):
    """Claude review of a91cdf7 (NIT 1): each of regex_cap's names is one grep accepts, as the
    character the check takes it for; an unknown name is an error to grep too."""
    path = tmp_path / "chars.txt"
    path.write_text("~\n")
    for name, char in regex_cap._NAMES.items():
        for kind in ".=":
            pattern = f"[[{kind}{name}{kind}]]"
            if char.isprintable():
                path.write_text(f"{char}\n")
                assert _grep(pattern, str(path), 2)[0] == 0, pattern
            else:
                assert _grep(pattern, str(path), 2)[0] in (0, 1), pattern
    assert _grep("[[.SPACE.]]", str(path), 2)[0] == 2


@darwin
def test_the_range_check_agrees_with_grep(tmp_path):
    """Claude review of a91cdf7 (NIT 2): for sampled ASCII ranges, the check refuses exactly
    those grep rejects (exit 2, "invalid character range"); it accepts no range grep rejects."""
    from experimental.local_worker.agent_protocol import BadCall

    path = tmp_path / "x.txt"
    path.write_text("x\n")
    rng = random.Random(88)
    chars = [chr(c) for c in range(0x20, 0x7F) if chr(c) not in "^[]"]
    pairs = {(rng.choice(chars), rng.choice(chars)) for _ in range(250)}
    pairs |= {("Z", "a"), ("A", "z"), ("!", "-"), ("-", "/"), ("\t", " "), (" ", "\t")}
    other = {("z", "é"), ("é", "z"), ("1", "а"), ("а", "я"), ("é", "ê"), ("一", "龥")}
    disagree = []
    for lo, hi in sorted(pairs | other):
        pattern = f"[{lo}-{hi}]"
        try:
            _decode_search(pattern)
            ours = True
        except BadCall:
            ours = False
        if ours != (_grep(pattern, str(path), 2)[0] != 2) and (ours or (lo, hi) in pairs):
            disagree.append(pattern)
    assert not disagree


@darwin
def test_grep_reads_a_brace_with_no_digit_first_as_text(tmp_path):
    """The check takes {,3} for text, as macOS grep -E does (GNU grep would read a bound)."""
    path = tmp_path / "b.txt"
    path.write_text("a{,3}\n")
    out = subprocess.run(
        ["/usr/bin/grep", "-E", "-o", "-e", "a{,3}", str(path)], capture_output=True, text=True
    )
    assert out.stdout == "a{,3}\n"
