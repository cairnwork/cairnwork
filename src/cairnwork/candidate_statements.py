"""Whether a candidate's edit to one Python file touches import machinery (#1065).

Statements are compared with the base blob (AST, comments dropped, nested blocks emptied, keyed
by the enclosing def/class names and block headers). A statement is hot when its own source names
a machinery word, or names a def or class whose body reaches one (directly or through other
same-file defs). An added or removed hot statement makes the edit machinery, so writing, rewriting
or moving machinery, calling dead base machinery, or flipping the condition that wraps it (the
key of every statement inside changes) is caught, while a fix elsewhere in a file that uses
getattr is not.
Residual (#1115): an edit to other control flow inside a function that reaches machinery (say an
early return before it) is not hot. Counting those raised the share of hot statements in real
signalling files (cairnwork, _pytest, click, hypothesis, packaging) from 6-13% to 20-29%, so it
is left to the blackbox and verifier checks.
"""

from __future__ import annotations

import ast
import copy
from collections import Counter

_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

Key = tuple[str, str]


def machinery_changed(old: bytes | None, new: bytes, words: tuple[bytes, ...]) -> bool:
    """True when an added or removed statement is hot. ``old`` None means the file is new; an
    unparseable base trusts nothing. Raises ValueError when ``new`` cannot be parsed."""
    after = _hot_statements(new, words)
    if after is None:
        raise ValueError("candidate source could not be parsed")
    before = (Counter(), set()) if old is None else _hot_statements(old, words)
    if before is None:  # nothing in an unparseable base is trusted
        before = (Counter(), set())
    added = after[0] - before[0]
    removed = before[0] - after[0]
    return any(k in after[1] for k in added) or any(k in before[1] for k in removed)


def _hot_statements(data: bytes, words: tuple[bytes, ...]) -> tuple[Counter[Key], set[Key]] | None:
    try:
        tree = ast.parse(data)
        hot_names = _hot_defs(tree, words)
        found: Counter[Key] = Counter()
        hot: set[Key] = set()
        _visit(list(tree.body), "", words, hot_names, found, hot)
    except _PARSE_ERRORS:
        return None
    return found, hot


def _hot_defs(tree: ast.Module, words: tuple[bytes, ...]) -> set[str]:
    """Names of defs and classes whose body names a machinery word or another hot def."""
    refs: dict[str, set[str]] = {}
    hot: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DEFS):
            refs.setdefault(node.name, set()).update(_names(node))
            if _has_word(ast.unparse(node), words):
                hot.add(node.name)
    grew = True
    while grew:  # reach machinery through same-file calls
        before = len(hot)
        hot.update(name for name, used in refs.items() if used & hot)
        grew = len(hot) > before
    return hot


def _visit(
    nodes: list[ast.AST],
    scope: str,
    words: tuple[bytes, ...],
    hot_names: set[str],
    found: Counter[Key],
    hot: set[Key],
) -> None:
    for node in nodes:
        own, blocks = copy.copy(node), []
        for field in _BLOCKS:
            if isinstance(value := getattr(node, field, None), list):
                blocks.append(value)
                setattr(own, field, [])
        text = ast.unparse(own)
        key = (scope, text)
        found[key] += 1
        if _has_word(text, words) or _names(own) & hot_names:
            hot.add(key)
        inner = f"{scope}/{node.name if isinstance(node, _DEFS) else text}"
        for block in blocks:
            _visit(block, inner, words, hot_names, found, hot)


def _names(node: ast.AST) -> set[str]:
    """Identifiers a node uses: names and attribute names (``self.helper`` names ``helper``)."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _has_word(text: str, words: tuple[bytes, ...]) -> bool:
    raw = text.encode("utf-8", "surrogatepass")
    return any(word in raw for word in words)
