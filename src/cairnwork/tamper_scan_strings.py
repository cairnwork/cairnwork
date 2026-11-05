"""Refused names built from string pieces and handed to a dynamic lookup (STRING-BUILT-v2, #821).

``getattr(cfg, "plugin" + "manager")`` names a refused thing without the word ever being a
whole identifier, so the line rules of tamper_scan.py never see it. This module watches the one
``tokenize`` pass that tamper_scan_tokens.file_views already makes over a committed file (no
second pass, no regular expression cutting strings, no new budget charge) and folds the argument
of an accessor into a string: ``getattr``, ``setattr``, ``delattr``, ``hasattr``, ``__import__``,
``import_module`` (every argument on its own), ``globals()[``, ``locals()[``, ``vars(..)[``,
``__dict__[``, ``modules[`` and ``.get(`` on the last two. An argument that folds to a refused
name by exact equality is a finding; reported only when the logical line holds an added line.

Folded forms: a string, adjacent strings, ``+``, ``SEP.join([..])``, ``%`` and ``.format`` over
constants with ``%s``/``%%``/``{}``/``{N}`` only, ``b"..".decode(..)``, and a name assigned an
earlier constant (``n = "plugin"``; only values that are a piece of a refused name, 64 at most).
Anything else makes the argument unknown: not refused. Residual, by design (a raised bar, not a
boundary; PR-2 judges at run time): ``chr``/``ord``, ``+=``, ``bytes([..]).decode``, codecs,
base64, reversing, slicing, ``replace``, f-strings (all of them, so 3.11 and 3.12 agree), a
non-constant piece, ``object.__getattribute__``, ``attrgetter``, aliases of an accessor,
``exec``/``eval``, a file Python cannot tokenize (no token stream: nothing is observed), a table
full of 64 constants, an accessor nested in a scope already open, and an accessor whose
brackets nest deeper than 32 (the scope is dropped from there on).

Linear bound (no budget of its own): each token is handled once with O(1) state work; a fold
holds at most CAP characters (the longest refused name + 1; past it the argument cannot be equal
and is dropped); the constant table holds at most 64 entries; the bracket stack at most 32; a
string token's value costs its own length, so the work is at most c * (tokens + bytes) however
the pieces are cut. ``steps`` and ``peak`` are counted for the tests; nothing reads them.
"""

from __future__ import annotations

import tokenize
from typing import Any

from cairnwork.tamper_scan_fold import INTERNALS, PIECES, REFUSED, Fold, literal

MAX_CONSTS = 64
MAX_DEPTH = 32
_MODULE_ROOTS = frozenset({"_pytest", "pluggy"})
_CALLS = frozenset({"getattr", "setattr", "delattr", "hasattr", "__import__", "import_module"})
_MODULE_CALLS = frozenset({"__import__", "import_module"})
_SUBSCRIPTED = frozenset({"__dict__", "modules"})
_CALLED = frozenset({"globals", "locals", "vars"})
_SUBSCRIPT_NAMES = frozenset(("N", name) for name in _SUBSCRIPTED)
_SKIP = frozenset({tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT})
_ENDS = frozenset({tokenize.NEWLINE, tokenize.ENDMARKER})
_OPENERS, _CLOSERS = "([{", ")]}"
_START, _NAME, _RHS, _NONE = range(4)  # where a statement is: before its first token, ...


class _Scope:
    def __init__(self, depth: int, module_call: bool, consts: dict[str, str]) -> None:
        self.depth, self.module_call, self.fold = depth, module_call, Fold(consts)
        self.stopped = False


def _kind(token_type: int) -> str:
    if token_type == tokenize.STRING:
        return "S"
    return {tokenize.NAME: "N", tokenize.OP: "O"}.get(token_type, "X")


class StringScan:
    """The observer: ``feed`` every token of one file, then read ``findings``."""

    def __init__(self) -> None:
        self.consts: dict[str, str] = {}
        self.consts_full = False  # a constant was not recorded: no propagation for the rest
        self.steps = self.peak = 0  # for the tests only
        self.keys: list[tuple[int, int]] = []  # set by file_views: row index -> git line
        self.hits: list[tuple[int, int, tuple[str, ...]]] = []
        self.pending: set[str] = set()
        self.afold = Fold(self.consts)
        self.scope: _Scope | None = None
        self.stack: list[str] = []
        self.over = 0  # brackets open past MAX_DEPTH
        self.recent: list[tuple[str, str]] = []
        self.last_closed = ""
        self.first = 0
        self.stage, self.target = _NONE, ""

    def feed(self, token: tokenize.TokenInfo) -> None:
        self.steps += 1
        if token.type in _SKIP:
            return
        if token.type in _ENDS:
            self._end_line(token.start[0])
            return
        if self.first == 0:
            self.first, self.stage = token.start[0], _START
        kind, text, value = _kind(token.type), token.string, None
        if kind == "S" and (self._watching() or self.stage == _RHS and not self.afold.dead):
            self.steps += len(text)
            value = literal(text)
        if kind == "O" and text in _OPENERS:
            self._open(text)
        elif kind == "O" and text in _CLOSERS:
            self._close(text)
        elif kind == "O" and text == "," and self.scope and self._depth() == self.scope.depth:
            self._end_arg(self.scope)
        elif fold := self._watching():
            fold.feed(kind, text, value)
        self._statement(kind, text, value)
        self.recent = [*self.recent, (kind, text)][-3:]

    def _watching(self) -> Fold | None:
        """The fold of the open scope while it still collects, else None."""
        scope = self.scope
        return (
            scope.fold if scope is not None and not scope.stopped and not scope.fold.dead else None
        )

    def _depth(self) -> int:
        return len(self.stack) + self.over

    def _opens(self, text: str) -> bool | None:
        """Whether this opener starts an accessor scope: None no, else True for a module one."""
        kind, name = self.recent[-1] if self.recent else ("", "")
        before = self.recent[-3:-1] if len(self.recent) == 3 else []
        if text == "(" and kind == "N" and name in _CALLS:
            return name in _MODULE_CALLS
        if text == "(" and (kind, name) == ("N", "get") and len(before) == 2:
            return False if before[1] == ("O", ".") and before[0] in _SUBSCRIPT_NAMES else None
        if text == "[" and ((kind, name) in _SUBSCRIPT_NAMES or self._called_result(kind, name)):
            return False
        return None

    def _called_result(self, kind: str, name: str) -> bool:
        return (kind, name) == ("O", ")") and self.last_closed in _CALLED

    def _open(self, text: str) -> None:
        previous = self.recent[-1] if self.recent else ("", "")
        opens = self._opens(text) if self.scope is None else None
        called = previous[1] if text == "(" and previous[0] == "N" else ""
        if len(self.stack) < MAX_DEPTH:
            self.stack.append(called)
        else:
            self.over += 1
        scope = self.scope
        if scope is None and opens is not None and self._depth() <= MAX_DEPTH:
            self.scope = _Scope(self._depth(), opens, self.consts)
        elif scope is not None and self._depth() > MAX_DEPTH:
            scope.stopped = True  # too deep: this scope collects nothing more
        elif scope is not None and not scope.stopped:
            scope.fold.feed("O", text, None)

    def _close(self, text: str) -> None:
        scope = self.scope
        if scope is not None and self._depth() == scope.depth:
            self._end_arg(scope)
            self.scope = None
        elif fold := self._watching():
            fold.feed("O", text, None)
        if self.over:
            self.over -= 1
            name = ""
        else:
            name = self.stack.pop() if self.stack else ""
        self.last_closed = name if text == ")" else ""

    def _end_arg(self, scope: _Scope) -> None:
        if not scope.stopped:
            value = scope.fold.value()
            rule = None if value is None else REFUSED.get(value)
            if rule is None and scope.module_call and value:
                rule = INTERNALS if value.split(".", 1)[0] in _MODULE_ROOTS else None
            if rule:
                self.pending.add(rule)
        self.steps += scope.fold.cost
        self.peak = max(self.peak, scope.fold.peak)
        scope.fold.cost = 0
        scope.fold.reset()

    def _statement(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text == ";":
            if self.stage == _RHS:
                self._finish_assign()
            self.stage = _START
        elif self.stage == _START:
            self.stage, self.target = (_NAME, text) if kind == "N" else (_NONE, "")
        elif self.stage == _NAME:
            self.stage = _RHS if (kind, text) == ("O", "=") else _NONE
            if self.stage == _RHS:
                self.afold.reset()
        elif self.stage == _RHS:
            self.afold.feed(kind, text, value)

    def _finish_assign(self) -> None:
        """``NAME = <constant>``: remember it when it is a piece of a refused name."""
        value = self.afold.value()
        self.steps += self.afold.cost
        self.peak = max(self.peak, self.afold.peak)
        self.afold.cost = 0
        self.stage = _NONE
        if value and "\n" not in value and value in PIECES:
            if self.target in self.consts or len(self.consts) < MAX_CONSTS:
                self.consts[self.target] = value
            else:
                self.consts_full = True
        else:
            self.consts.pop(self.target, None)

    def _end_line(self, row: int) -> None:
        if self.stage == _RHS:
            self._finish_assign()
        self.scope, self.over, self.last_closed = None, 0, ""
        self.stack.clear()
        self.recent = []
        if self.pending and self.first:
            self.hits.append((self.first, row, tuple(sorted(self.pending))))
        self.pending.clear()
        self.first = 0

    def findings(self, added: set[int]) -> list[tuple[int, str]]:
        """``(line, rule)``: each hit at the first added line of its logical line, if any."""
        found: list[tuple[int, str]] = []
        for first, last, rules in self.hits:
            lines = (self.keys[row - 1][0] for row in range(first, last + 1))
            line = next((n for n in lines if n in added), None)
            found += [] if line is None else [(line, rule) for rule in rules]
        return found
