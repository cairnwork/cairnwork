"""Folding one accessor argument into a string, token by token (STRING-BUILT-v2, #821).

``Fold`` is the small state machine tamper_scan_strings.py feeds the tokens of one argument (or
of the right-hand side of ``NAME = ...``). It knows strings, adjacent strings, ``+``,
``SEP.join([..])``, ``%`` and ``.format`` over constants (``%s``, ``%%``, ``{}``, ``{N}`` only),
``b"..".decode(..)`` and a name of the constant table; anything else makes it dead (unknown).
It holds at most CAP characters: past that the value cannot equal a refused name and it is
dead. The names that are refused, and the rule each belongs to, are here too.
"""

from __future__ import annotations

import ast
from typing import Any

from cairnwork.tamper_scan_plugin import HOOK_NAMES

HOOK, INTERNALS, FORGE = "pytest_hook_impl", "pytest_internals", "code_object_forge"
_INTERNAL_NAMES = (
    "_pytest pluginmanager _inner_hookexec _hookexec ihook add_hookcall_monitoring"
    " enable_tracing pluggy _HookCaller HookCaller HookImpl"
).split()
_FORGE_NAMES = "FunctionType CodeType __code__ __globals__ compile co_filename".split()
REFUSED = {
    **dict.fromkeys(HOOK_NAMES, HOOK),
    **dict.fromkeys(_INTERNAL_NAMES, INTERNALS),
    **dict.fromkeys(_FORGE_NAMES, FORGE),
}
CAP = max(map(len, REFUSED)) + 1  # a fold longer than this cannot equal a refused name
PIECES = "\n".join(REFUSED)  # one string: "is this a piece of a refused name" in one test
_DIGITS = "0123456789"


def literal(text: str) -> str | bytes | None:
    """The value of one STRING token; None for an f-string, a t-string or anything unreadable."""
    quote = text[0]
    if quote in "\"'" and "\\" not in text:  # no prefix, no escape: the body as written
        return text[3:-3] if text[:3] == quote * 3 and len(text) >= 6 else text[1:-1]
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, (str, bytes)) else None


def _percent(fmt: str, items: list[str]) -> str | None:
    """``fmt % items`` for ``%s`` and ``%%`` only; None for any other spec or a wrong count."""
    out: list[str] = []
    size = used = at = 0
    while at < len(fmt):
        found = fmt.find("%", at)
        out.append(fmt[at:] if found < 0 else fmt[at:found])
        if found < 0:
            break
        spec = fmt[found + 1 : found + 2]
        if spec == "s" and used < len(items):
            out.append(items[used])
            used += 1
        elif spec == "%":
            out.append("%")
        else:
            return None
        at = found + 2
        size += len(out[-1]) + len(out[-2])
        if size > CAP:
            return None
    return "".join(out) if used == len(items) else None


def _braces(fmt: str, items: list[str]) -> str | None:
    """``fmt.format(*items)`` for ``{}``, ``{N}`` (one digit) and doubled braces only."""
    out: list[str] = []
    size = at = auto = 0
    manual = False
    while at < len(fmt):
        char = fmt[at]
        if char in "{}" and fmt[at + 1 : at + 2] == char:
            piece, at = char, at + 2
        elif char == "{":
            end = fmt.find("}", at)
            field = fmt[at + 1 : end] if end > 0 else "?"
            if field == "":
                index, auto = auto, auto + 1
            elif len(field) == 1 and field in _DIGITS:
                index, manual = int(field), True
            else:
                return None
            if index >= len(items) or (auto and manual):
                return None
            piece, at = items[index], end + 1
        elif char == "}":
            return None
        else:
            end = at + 1  # a run of plain text, cut at the next brace (one step per character)
            while end < len(fmt) and fmt[end] not in "{}":
                end += 1
            piece, at = fmt[at:end], end
        out.append(piece)
        size += len(piece)
        if size > CAP:
            return None
    return "".join(out)


class Fold:
    """One argument folded token by token; ``value()`` is the string it is, or None."""

    def __init__(self, consts: dict[str, str]) -> None:
        self.consts = consts
        self.peak = self.cost = 0
        self.reset()
        self.dispatch = {
            "A": self._term, "dot": self._dot, "pct": self._pct, "j1": self._open_call,
            "o1": self._open_call, "d1": self._open_call, "j2": self._open_items,
            "i0": self._item, "i1": self._after_item, "jend": self._join_end,
            "d2": self._decode_args,
        }  # fmt: skip

    def reset(self) -> None:
        self.val = self.acc = self.sep = ""
        self.atom: str | bytes | None = None
        self.state, self.kind = "A", ""
        self.need = self.dead = False
        self.items: list[str] = []

    def value(self) -> str | None:
        if self.dead or self.state != "A" or self.need or isinstance(self.atom, bytes):
            return None
        return self.val + (self.atom or "")

    def feed(self, kind: str, text: str, value: str | bytes | None) -> None:
        if self.dead:
            return
        self.dispatch[self.state](kind, text, value)
        size = len(self.val) + len(self.atom or "") + len(self.acc)
        self.peak = max(self.peak, size)
        if size > CAP:
            self.dead = True  # longer than any refused name: cannot be equal, stop growing

    def _term_value(self, kind: str, text: str, value: Any) -> str | None:
        if kind == "S" and isinstance(value, str):
            return value
        return self.consts.get(text) if kind == "N" else None

    def _term(self, kind: str, text: str, value: Any) -> None:
        if kind == "S" and value is not None:
            self.cost += len(text)
            if self.atom is None:
                self.atom, self.need = value, False
            elif type(self.atom) is type(value):
                self.atom += value
            else:
                self.dead = True
        elif kind == "N" and self.atom is None and text in self.consts:
            self.atom, self.need = self.consts[text], False
        elif kind == "O" and self.atom is not None and text in ("+", ".", "%"):
            self._operator(text)
        else:
            self.dead = True

    def _operator(self, text: str) -> None:
        if text == "+" and isinstance(self.atom, str):
            self.val, self.atom, self.need = self.val + self.atom, None, True
        elif text == "." or (text == "%" and isinstance(self.atom, str)):
            self.state = "dot" if text == "." else "pct"
        else:
            self.dead = True

    def _dot(self, kind: str, text: str, value: Any) -> None:
        text_atom, bytes_atom = isinstance(self.atom, str), isinstance(self.atom, bytes)
        nxt = {"join": ("j1", text_atom), "format": ("o1", text_atom), "decode": ("d1", bytes_atom)}
        state, ok = nxt.get(text if kind == "N" else "", ("", False))
        if ok:
            self.state, self.kind = state, text
        else:
            self.dead = True

    def _open_call(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text == "(":
            self.state = {"format": "i0", "decode": "d2", "join": "j2"}[self.kind]
            self.items, self.sep, self.acc = [], str(self.atom), ""
        else:
            self.dead = True

    def _open_items(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text in "[(":
            self.state = "i0"
        else:
            self.dead = True

    def _pct(self, kind: str, text: str, value: Any) -> None:
        one = self._term_value(kind, text, value)
        if one is not None:
            self.atom, self.state = _percent(str(self.atom), [one]), "A"
            self.dead = self.atom is None
        elif kind == "O" and text == "(":
            self.state, self.kind, self.items = "i0", "pct", []
        else:
            self.dead = True

    def _item(self, kind: str, text: str, value: Any) -> None:
        one = self._term_value(kind, text, value)
        if one is not None and len(self.items) < 10 and len(one) <= CAP:
            self.cost += len(text)
            if self.kind == "join":
                self.acc += (self.sep if self.items else "") + one
                self.items = [""] if not self.items else self.items  # only "is first" matters
            else:
                self.items.append(one)
            self.state = "i1"
        elif kind == "O" and text in ")]" and not (self.kind != "join" and text == "]"):
            self._close_items()
        else:
            self.dead = True

    def _after_item(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text == ",":
            self.state = "i0"
        elif kind == "O" and text in ")]" and not (self.kind != "join" and text == "]"):
            self._close_items()
        else:
            self.dead = True

    def _close_items(self) -> None:
        if self.kind == "join":
            self.state = "jend"
            return
        fmt = _percent if self.kind == "pct" else _braces
        self.atom, self.state = fmt(str(self.atom), self.items), "A"
        self.dead = self.atom is None

    def _join_end(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text == ")":
            self.atom, self.state = self.acc, "A"
        else:
            self.dead = True

    def _decode_args(self, kind: str, text: str, value: Any) -> None:
        if kind == "O" and text == ")":
            try:
                raw = self.atom if isinstance(self.atom, bytes) else b""
                self.atom, self.state = raw.decode("utf-8"), "A"
            except UnicodeDecodeError:
                self.dead = True
        elif kind not in "SN" and text not in (",", "="):
            self.dead = True
