r"""The search regex cost cap (#88; reviews of PR 251): what grep -E may be asked to run.

The wrapper runs macOS /usr/bin/grep -E (TRE) under a CPU-time limit and no memory limit (none can
be set on Darwin 27: see the wrapper's header), so this check alone bounds what grep allocates.
It parses the pattern as grep -E reads it and computes upper bounds for the automaton TRE builds
(a position automaton), with bounded repetitions expanded as TRE expands them: x{m,n} is m copies
of x and then n-m optional ones (counted as a flat run x?x?..., which costs at least as much as
TRE's nested (x(x)?)?), x{m,} is m copies and then x*, and x{0} still counts x once. So each
bound multiplies its operand's cost by max(m, n, 1), or by m + 1 for {m,}; ? * + add only the
loop. Three numbers, each refused over its budget:
  positions    one per literal, ^ or $; 3 for ``.``; 5 for \w and the like; 4 + its items for a
               bracket (a class test is slower than a literal; the weights follow CPU time);
  transitions  pairs of positions one may follow the other (what Codex round 3 of PR 251 hit);
  tagged       positions times (1 + tags), a tag per group, alternative and repetition copy:
               TRE carries every tag through every step, so tags multiply the matching time.
Second rule: no repetition (? * + or a bound) of anything that can match the empty string
((a?)+, (a*){5}, (b*|a)+, ()*), none right after another (a**, a{2}{3}), none with nothing to
repeat, and no empty alternative (a|, (|a); grep refuses those itself). Codex round 3:
^(a?a?...a?){100}$ (40 a?, 89 bytes) passed the old nesting rule and reached 494 MB before
SIGXCPU, as each a? may follow every earlier one through empty matches (about 8 million
transitions); both rules refuse it now.

Budgets, from /usr/bin/grep -E on Darwin 27 (M5 Mac mini, en_US.UTF-8): peak RSS and CPU seconds
for 1 MiB of adversarial input (4,095-character lines of a, of accented a, or a mix) on which
the pattern almost matches everywhere and never does (the slowest case: grep keeps every state):
  a{100}x5 a{11}b (512 positions, the longest accepted run)          1.8 MiB, 1.48 s
  (a{50}){9}b (451 positions, 9 tags), the slowest accepted seen      1.8 MiB, 1.55 s
  .{100}.{70}b (511)                                                 1.8 MiB, 1.26 s
  [[=a=]]{100}[[=a=]]{2}b, the same with [[:alpha:]] or \w (511)   1.6 MiB, 0.94-1.03 s
  ((a|aa|aaa){20})*x (121 positions, 62 tags; tagged 7,623)          1.7 MiB, 0.92 s
  the slowest of 810 random accepted patterns                        1.6 MiB, 1.02 s
  refused: (a{100}){100} (10,000 positions) 7.5 MiB, 2.1 s; a{900}b 2.3 s; (a{1,100}){100}
  (505k transitions) 97 MiB; (a|b|...|z single letters x100){100} (990k) 136 MiB;
  ((a|aa|aaa){100})*x (tagged 182k) 3.6 s; the Codex round-3 pattern 494 MB.
Memory stays far below 64 MiB at these budgets (TRE needs about 200 bytes per transition, so
100,000 would be some 20 MiB); time is what binds, kept under 2 s per MiB searched. A larger
input (a repository of many MiB) is left to the wrapper's CPU limit, which stops grep with a
message the model reads.

No memory watchdog in the wrapper: polling grep's RSS with ps from the shell would see a fast
allocation only after the fact (TRE compiles in one call), would need the wrapper to stop exec'ing
grep and relay its output and status itself, and would guard nothing these budgets let through.

Kept from the earlier cap: back-references (\1 to \9; TRE backtracks on them) are refused; a
count is at most MAX_REPEAT; brackets follow the POSIX ERE rules macOS grep applies (bracket_end),
now with grep's own range order and collating-element names; groups nest at most MAX_DEPTH deep.
Fail closed: what this parser cannot read (an unclosed group or bracket, a trailing backslash, a
{ and a digit that form no bound) is refused, never skipped. Braces that form no bound ({}, {x},
{ 1}, and {,3}, which macOS grep reads as text) are characters, as for grep. agent_protocol keeps
the 512-byte cap.
"""

from __future__ import annotations

import re

MAX_REPEAT = 100
MAX_DEPTH = 32
# Budgets measured with /usr/bin/grep -E on Darwin 27 (see the module docstring).
MAX_POSITIONS = 512  # = MAX_PATTERN_BYTES: a regex of plain characters always passes
MAX_TRANSITIONS = 100_000
MAX_TAGGED = 8000

BACKREF = (
    "search regex may not use a back-reference such as \\1: match the text itself, or search with"
    " a fixed string"
)
BRACKET = (
    "search regex has a bracket expression this check cannot parse: close every [ with ], and"
    " inside it use only the classes [:alpha:], [:digit:] and the like, and [.x.] or [=x=] of"
    " one character"
)
COSTLY = (
    "search regex is too costly for grep once its repetitions are expanded: repeat fewer times,"
    " nest fewer {m,n} or groups inside one another, or search with a fixed string"
)
NULLABLE = (
    "search regex may not repeat a part that can match the empty string, such as (a?)+, (a*){5}"
    " or (b*|a)*: make the repeated part match at least one character"
)
STACKED = (
    "search regex may not put a repetition right after another (a**, a+?, a{2}{3}) or where"
    " nothing precedes it: group the part first, or escape the character as \\*"
)
UNPARSABLE = (
    "search regex is not one this check can read: close every ( with ), end no pattern with a"
    " lone \\, and write a { followed by a digit only as a bound such as {2} or {1,3}"
)
RANGE = (
    "search regex has a range grep rejects: a range runs from a lower to a higher ASCII"
    " character (as [a-z] or [0-9]; list non-ASCII characters one by one), never from or to a"
    " class such as [:alpha:] or [=a=], and is not followed by another -; put a literal - first"
    " or last"
)
EMPTY_ALTERNATIVE = (
    "search regex may not have an empty alternative such as a| or (|a) (grep refuses it): write"
    " (a)? instead"
)
DEPTH = f"search regex may nest groups at most {MAX_DEPTH} deep"
COUNT = f"search regex repetition counts must be at most {MAX_REPEAT}"

_BOUND = re.compile(r"\{([0-9]+)(?:(,)([0-9]*))?\}")
_CLASSES = frozenset(
    ("alnum", "alpha", "blank", "cntrl", "digit", "graph", "lower", "print", "punct", "space")
    + ("upper", "xdigit")
)
_DIGITS = "0123456789"
# The order of ASCII range endpoints for macOS grep -E under en_US.UTF-8, measured on Darwin 27
# (all 8,649 printable pairs, then ^ [ ] and tab). A range with any other endpoint is refused:
# grep orders non-ASCII characters by collation, not code point ([1-а] and [а-B] are both errors,
# and so were 289 of 600 sampled non-ASCII pairs in code-point order).
_ORDER = "\t _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789" + "".join(
    c + c.upper() for c in "abcdefghijklmnopqrstuvwxyz"
)
# The collating-element names grep reads in [.name.] and [=name=]: BSD regcomp's cname table,
# each name checked against /usr/bin/grep -E on Darwin 27 (any other name of more than one
# character is refused).
_RUNS = (
    (0, "NUL SOH STX ETX EOT ENQ ACK BEL BS HT LF VT FF CR SO SI DLE DC1 DC2 DC3 DC4 NAK SYN ETB"),
    (24, "CAN EM SUB ESC IS4 IS3 IS2 IS1 space exclamation-mark quotation-mark number-sign"),
    (36, "dollar-sign percent-sign ampersand apostrophe left-parenthesis right-parenthesis"),
    (42, "asterisk plus-sign comma hyphen period slash zero one two three four five six seven"),
    (56, "eight nine colon semicolon less-than-sign equals-sign greater-than-sign question-mark"),
    (64, "commercial-at"),
    (91, "left-square-bracket backslash right-square-bracket circumflex underscore grave-accent"),
    (123, "left-brace vertical-line right-brace tilde DEL"),
)
_ALIASES = (
    "alert=BEL backspace=BS tab=HT newline=LF vertical-tab=VT form-feed=FF carriage-return=CR"
    " FS=IS4 GS=IS3 RS=IS2 US=IS1 hyphen-minus=hyphen full-stop=period solidus=slash"
    " reverse-solidus=backslash circumflex-accent=circumflex low-line=underscore"
    " left-curly-bracket=left-brace right-curly-bracket=right-brace"
)
_NAMES = {name: chr(code + k) for code, run in _RUNS for k, name in enumerate(run.split())}
_NAMES |= {alias: _NAMES[name] for alias, name in (p.split("=") for p in _ALIASES.split())}

# (positions, nullable, first, last, transitions): first/last count the positions a match can
# start/end on; every count is an upper bound.
Cost = tuple[int, bool, int, int, int, int]
EMPTY: Cost = (0, True, 0, 0, 0, 0)
_CHAR: Cost = (1, False, 1, 1, 0, 0)
_DOT: Cost = (3, False, 3, 3, 0, 0)  # any character but newline
_CLASS_ESCAPE: Cost = (5, False, 5, 5, 0, 0)  # \w \W \s \S \d \D: a class, as [[:alnum:]_]
_ASSERTION: Cost = (1, True, 1, 1, 0, 0)  # ^ $ \< \> \b ... match the empty string


class RegexRefused(ValueError):
    """A pattern grep -E must not be asked to run; the message says how to rewrite it."""


def _item(pattern: str, i: int) -> tuple[int, str | None]:
    """One item of a bracket at ``i``: (index past it, the character it stands for, or None for
    a [:class:] or [=x=], which cannot end a range)."""
    kind = pattern[i + 1 : i + 2] if pattern[i] == "[" else ""
    if kind == ":":
        close = pattern.find(":]", i + 2)
        if close < 0 or pattern[i + 2 : close] not in _CLASSES:
            raise RegexRefused(BRACKET)
        return close + 2, None
    if kind in ("=", "."):
        close = pattern.find(kind + "]", i + 2)
        name = pattern[i + 2 : close] if close >= 0 else ""
        char = name if len(name) == 1 else _NAMES.get(name)
        if char is None:
            raise RegexRefused(BRACKET)
        return close + 2, char if kind == "." else None
    return i + 1, pattern[i]  # any other character, a backslash or a [ opening no item included


def _ordered(lo: str | None, hi: str | None) -> bool:
    """Whether grep -E accepts the range lo-hi (en_US.UTF-8, the wrapper's locale), for ASCII
    endpoints; any other range is refused, as its order is the locale's collation."""
    keys = [_ORDER.find(c) for c in (lo, hi) if c]
    return len(keys) == 2 and min(keys) >= 0 and keys[0] <= keys[1]


def bracket_end(pattern: str, at: int) -> tuple[int, int]:
    """(index just past the bracket expression whose [ is at ``at``, its number of items), as
    grep -E reads it; a range grep rejects is refused here."""
    i = at + 1
    i += pattern[i : i + 1] == "^"
    items = 0
    first = True
    while i < len(pattern):
        if pattern[i] == "]" and not first:  # a ] right after [ or [^ is a character
            return i + 1, items
        i, lo = _item(pattern, i)
        first = False
        items += 1
        if pattern[i : i + 1] != "-" or pattern[i + 1 : i + 2] in ("]", ""):
            continue
        i, hi = _item(pattern, i + 1)
        if not _ordered(lo, hi) or (pattern[i : i + 1] == "-" and pattern[i + 1 : i + 2] != "]"):
            raise RegexRefused(RANGE)
    raise RegexRefused(BRACKET)


def _cat(x: Cost, y: Cost) -> Cost:
    p1, n1, f1, l1, t1, g1 = x
    p2, n2, f2, l2, t2, g2 = y
    first, last = f1 + (f2 if n1 else 0), l2 + (l1 if n2 else 0)
    return (p1 + p2, n1 and n2, first, last, t1 + t2 + l1 * f2, g1 + g2)


def _alt(x: Cost, y: Cost) -> Cost:
    return (x[0] + y[0], x[1] or y[1], x[2] + y[2], x[3] + y[3], x[4] + y[4], x[5] + y[5] + 1)


def _loop(x: Cost, nullable: bool) -> Cost:  # x* (nullable) or x+: every end may restart x
    return (x[0], nullable, x[2], x[3], x[4] + x[3] * x[2], x[5] + 1)


def _repeat(x: Cost, lo: int, hi: int | None) -> Cost:
    """x{lo,hi} as TRE expands it (hi None: unbounded); x{0} still parses x, so it counts once."""
    if (lo, hi) == (1, None):
        return _loop(x, x[1])
    out = EMPTY
    for _ in range(lo):
        out = _cat(out, x)
    if hi is None:
        return _cat(out, _loop(x, True))
    optional = (x[0], True, x[2], x[3], x[4], x[5] + 1)
    for _ in range(max(hi, 1) - lo):
        out = _cat(out, optional)
    return out


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.s = pattern
        self.i = 0
        self.depth = 0

    def peek(self, ahead: int = 0) -> str:
        return self.s[self.i + ahead : self.i + ahead + 1]

    def alternation(self) -> Cost:
        start = self.i
        out = self.branch()
        empty = self.i == start
        while self.peek() == "|":
            self.i += 1
            start = self.i
            out = _budget(_alt(out, self.branch()))
            if empty or self.i == start:  # grep: "empty (sub)expression"; () alone is fine
                raise RegexRefused(EMPTY_ALTERNATIVE)
        return out

    def branch(self) -> Cost:
        out = EMPTY
        while self.peek() not in ("", "|") and not (self.peek() == ")" and self.depth):
            out = _budget(_cat(out, self.piece()))
        return out

    def piece(self) -> Cost:
        x = self.atom()
        bound = self.quantifier()
        if bound is None:
            return x
        if x[1]:
            raise RegexRefused(NULLABLE)
        if self.quantifier() is not None:
            raise RegexRefused(STACKED)
        return _budget(_repeat(x, *bound))

    def quantifier(self) -> tuple[int, int | None] | None:
        c = self.peek()
        if c and c in "*+?":
            self.i += 1
            return {"*": (0, None), "+": (1, None), "?": (0, 1)}[c]
        if c != "{" or not self.peek(1) or self.peek(1) not in _DIGITS:
            return None  # { not followed by a digit is a character to grep
        m = _BOUND.match(self.s, self.i)
        if m is None:
            raise RegexRefused(UNPARSABLE)
        lo = int(m[1])
        hi = lo if m[2] is None else (int(m[3]) if m[3] else None)
        if lo > MAX_REPEAT or (hi is not None and hi > MAX_REPEAT):
            raise RegexRefused(COUNT)
        if hi is not None and hi < lo:
            raise RegexRefused(UNPARSABLE)
        self.i = m.end()
        return lo, hi

    def atom(self) -> Cost:
        c, nxt = self.peek(), self.peek(1)
        if c == "(":
            if self.depth >= MAX_DEPTH:
                raise RegexRefused(DEPTH)
            self.i += 1
            self.depth += 1
            p, n, f, last, t, g = self.alternation()
            if self.peek() != ")":
                raise RegexRefused(UNPARSABLE)
            self.i += 1
            self.depth -= 1
            return (p, n, f, last, t, g + 1)  # a group is a submatch: TRE tags it
        if c == "[":
            self.i, items = bracket_end(self.s, self.i)
            n = items + 4  # a literal per item (+1 for [^...]), and a class test costs more
            return (n, False, n, n, 0, 0)
        if c in ("*", "+", "?") or (c == "{" and nxt and nxt in _DIGITS):
            raise RegexRefused(STACKED)
        self.i += 1
        if c == "\\":
            return self.escape(nxt)
        if c in ("^", "$"):
            return _ASSERTION
        return _DOT if c == "." else _CHAR

    def escape(self, c: str) -> Cost:
        if not c:
            raise RegexRefused(UNPARSABLE)
        if c in "123456789":
            raise RegexRefused(BACKREF)
        self.i += 1
        if c in "wWsSdD":
            return _CLASS_ESCAPE
        if c.isalnum() or c in "<>`'":
            return _ASSERTION  # \b \< ... or an escape this check does not know: maybe empty
        return _CHAR


def _budget(x: Cost) -> Cost:
    if x[0] > MAX_POSITIONS or x[4] > MAX_TRANSITIONS or x[0] * (1 + x[5]) > MAX_TAGGED:
        raise RegexRefused(COSTLY)
    return x


def cost(pattern: str) -> Cost:
    """The automaton-size bound of ``pattern`` (RegexRefused if it is over budget or refused)."""
    parser = _Parser(pattern)
    out = parser.alternation()
    if parser.i != len(pattern):  # cannot happen: a ) at depth 0 is a character
        raise RegexRefused(UNPARSABLE)
    return out


def check_regex(pattern: str) -> None:
    cost(pattern)
