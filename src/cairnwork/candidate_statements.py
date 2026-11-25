"""Whether a candidate's edit to one Python file touches import machinery (#1065).

Statements are compared with the base blob (AST, comments dropped, nested blocks emptied, keyed
by the enclosing def/class names, block headers and block fields). A statement is hot when its own
source names a machinery word, or names a def or class whose body reaches an import-machinery
word (``reach``: sys.modules, finders, importlib, __import__, runpy, pytest_plugins; directly or
through other same-file defs). The word scan is main's; a statement whose only machinery words are
getattr/setattr, every occurrence being a direct call that passes the literal-name rules, is
exempted. vars is never exempted (see #1155). An added or removed hot statement makes the edit
machinery, so writing, rewriting or moving machinery, calling dead base machinery, or flipping the
condition that wraps it (the key of every statement inside changes) is caught, while a fix that
calls a same-file helper is not.
Residual (#1115): an edit to other control flow inside a function that reaches machinery (say an
early return before it), and base machinery reached through aliases, registries or dunders, are
not hot; broader lexical matching raised hot statements in real signalling files, so these are
left to the blackbox and verifier checks.
Exact names (#1779): vars, globals, locals, exec, eval, compile, breakpoint, marshal and pickle
are matched as whole words, not as substrings. An identifier (name, attribute, def, argument,
import name) is split into snake_case and camelCase words and hits when any word is one of them
(``subprocess_exec``, ``compile_command``); ``evaluate``, ``executor`` and ``compiled`` do not. An
attribute with such a word hits whatever its receiver is, except the fixed pairs in ``SAFE_ATTRS``
(``re.compile``) when the file imports the module plainly, never rebinds that name, and the change
adds no module of that name. A string hits when a component split on any non-identifier character
is one of the words. Every other word keeps the substring match.
"""

from __future__ import annotations

import ast
import copy
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

Key = tuple[str, str]
_EXACT = frozenset(
    {"vars", "globals", "locals", "exec", "eval", "compile", "breakpoint", "marshal", "pickle"}
)


# Attribute reads that stay honest on a plain ``import <module>`` (#1779); widening needs numbers.
SAFE_ATTRS = {"re": "compile", "regex": "compile"}
_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


@dataclass(frozen=True)
class _Words:
    """A word table split into substring words and whole-word words, plus the names that may
    read one safe attribute in this file (name -> attribute)."""

    sub: tuple[bytes, ...]
    exact: frozenset[str]
    safe: Mapping[str, str]

    @classmethod
    def make(cls, words: tuple[bytes, ...], safe: Mapping[str, str]) -> _Words:
        names = [word.decode() for word in words]
        return cls(
            tuple(w for w, n in zip(words, names, strict=True) if n not in _EXACT),
            frozenset(n for n in names if n in _EXACT),
            safe,
        )

    def without(self, *dropped: bytes) -> _Words:
        return _Words(tuple(w for w in self.sub if w not in dropped), self.exact, self.safe)


def machinery_changed(
    old: bytes | None,
    new: bytes,
    words: tuple[bytes, ...],
    reach: tuple[bytes, ...],
    denylist: frozenset[str],
    shadowed: frozenset[str] = frozenset(),
) -> bool:
    """True when an added or removed statement is hot. ``old`` None means the file is new; an
    unparseable base trusts nothing. ``shadowed`` names the safe-attribute modules the change set
    adds a file for. Raises ValueError when ``new`` cannot be parsed."""
    after = _hot_statements(new, words, reach, denylist, shadowed)
    if after is None:
        raise ValueError("candidate source could not be parsed")
    before = (
        (Counter(), set())
        if old is None
        else _hot_statements(old, words, reach, denylist, shadowed)
    )
    if before is None:  # nothing in an unparseable base is trusted
        before = (Counter(), set())
    added = after[0] - before[0]
    removed = before[0] - after[0]
    return any(k in after[1] for k in added) or any(k in before[1] for k in removed)


def _hot_statements(
    data: bytes,
    words: tuple[bytes, ...],
    reach: tuple[bytes, ...],
    denylist: frozenset[str],
    shadowed: frozenset[str],
) -> tuple[Counter[Key], set[Key]] | None:
    try:
        tree = ast.parse(data)
        safe = _safe_roots(tree, shadowed)
        hot_names = _hot_defs(tree, _Words.make(reach, safe))
        found: Counter[Key] = Counter()
        hot: set[Key] = set()
        _visit(list(tree.body), "", _Words.make(words, safe), hot_names, denylist, found, hot)
    except _PARSE_ERRORS:
        return None
    return found, hot


def _hot_defs(tree: ast.Module, words: _Words) -> set[str]:
    """Names of defs and classes whose body names a ``words`` entry or another hot def."""
    refs: dict[str, set[str]] = {}
    hot: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DEFS):
            refs.setdefault(node.name, set()).update(_names(node))
            if _hit(node, words):
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
    words: _Words,
    hot_names: set[str],
    denylist: frozenset[str],
    found: Counter[Key],
    hot: set[Key],
) -> None:
    for node in nodes:
        own, blocks = copy.copy(node), []
        for field in _BLOCKS:
            if isinstance(value := getattr(node, field, None), list):
                blocks.append((field, value))
                setattr(own, field, [])
        text = ast.unparse(own)
        key = (scope, text)
        found[key] += 1
        scan_hit = _hit(own, words)
        exempt = scan_hit and _exempt_dynamic_word_scan(text, own, words, denylist)
        if (scan_hit and not exempt) or _names(own) & hot_names:
            hot.add(key)
        inner = f"{scope}/{node.name if isinstance(node, _DEFS) else text}"
        for field, block in blocks:  # the field keeps an if/else or try/else move visible
            _visit(block, f"{inner}:{field}", words, hot_names, denylist, found, hot)


def _unsafe_dynamic_access(node: ast.AST, denylist: frozenset[str]) -> bool:
    names = {"getattr", "setattr"}
    calls = [part for part in ast.walk(node) if isinstance(part, ast.Call)]
    direct = {id(call.func) for call in calls if isinstance(call.func, ast.Name)}
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and part.id in names | {"vars"}:
            if part.id == "vars" or id(part) not in direct:
                return True
        if isinstance(part, ast.Attribute) and part.attr in names | {"vars"}:
            return True
        if any(name in names | {"vars"} for name in _bound_identifiers(part)):
            return True
        if isinstance(part, ast.Attribute) and _is_direct_getattr(part.value):
            if part.attr.startswith("_") or part.attr in denylist:
                return True
        if isinstance(part, ast.Subscript) and _is_direct_getattr(part.value):
            return True
    for call in calls:
        if (
            isinstance(call.func, ast.Call)
            and isinstance(call.func.func, ast.Name)
            and call.func.func.id == "getattr"
        ):
            return True
        if not isinstance(call.func, ast.Name) or call.func.id not in names:
            continue
        kind = call.func.id
        if call.keywords or any(isinstance(arg, ast.Starred) for arg in call.args):
            return True
        if kind == "setattr" and len(call.args) != 3:
            return True
        if kind == "getattr" and len(call.args) not in {2, 3}:
            return True
        name = call.args[1]
        if (
            not isinstance(name, ast.Constant)
            or not isinstance(name.value, str)
            or not name.value.isidentifier()
            or name.value.startswith("_")
            or name.value in denylist
        ):
            return True
    return False


def _exempt_dynamic_word_scan(
    text: str, node: ast.AST, words: _Words, denylist: frozenset[str]
) -> bool:
    """Exempt only word hits fully accounted for by safe direct getattr/setattr calls."""
    if _hit(node, words.without(b"getattr", b"setattr")) or _unsafe_dynamic_access(node, denylist):
        return False
    calls = [part for part in ast.walk(node) if isinstance(part, ast.Call)]
    direct = sum(
        isinstance(call.func, ast.Name) and call.func.id in {"getattr", "setattr"} for call in calls
    )
    return text.count("getattr") + text.count("setattr") == direct


def _is_direct_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
    )


def _bound_identifiers(node: ast.AST) -> Iterator[str]:
    """Yield identifier strings defined or bound by one AST node."""
    if isinstance(node, _DEFS):
        yield node.name
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield from alias.name.split(".")
            if alias.asname is not None:
                yield alias.asname
    elif isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, ast.keyword) and node.arg is not None:
        yield node.arg
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
        yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        yield node.rest
    elif isinstance(node, ast.ExceptHandler) and node.name is not None:
        yield node.name
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        yield from node.names


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


def _hit(node: ast.AST, words: _Words) -> bool:
    """Substring words match the unparsed text as before; whole-word words match by AST form."""
    if _has_word(ast.unparse(node), words.sub):
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant):
            if isinstance(sub.value, (str, bytes)) and _string_hit(sub.value, words.exact):
                return True
        elif isinstance(sub, ast.Attribute):
            if _word_hit(sub.attr, words.exact) and not _safe_read(sub, words.safe):
                return True
        elif any(_word_hit(name, words.exact) for name in _identifiers(sub)):
            return True
    return False


def _word_hit(identifier: str, exact: frozenset[str]) -> bool:
    return any(word.lower() in exact for word in _WORD_RE.findall(identifier))


def _string_hit(value: str | bytes, exact: frozenset[str]) -> bool:
    """A string hits when a component split on any non-identifier character is a word (``"exec"``,
    ``"a.pickle"``, ``"marshal:loads"``, ``"x\\exec"``, ``"json;marshal"``, ``"json,pickle"``);
    capitalised prose (``"Compile and evaluate"``) does not."""
    text = value if isinstance(value, str) else value.decode("latin-1")
    return any(part in exact for part in re.split(r"\W+", text))


def _identifiers(node: ast.AST) -> Iterator[str]:
    """Identifiers a node introduces or reads, besides attribute names."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.ImportFrom):
        yield from (node.module or "").split(".")
        yield from _bound_identifiers(node)
    elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        yield node.name
    elif isinstance(node, ast.MatchClass):
        yield from node.kwd_attrs  # case object(exec=e) reads the attribute
        yield from _bound_identifiers(node)
    else:
        yield from _bound_identifiers(node)


def _safe_read(node: ast.Attribute, safe: Mapping[str, str]) -> bool:
    return (
        isinstance(node.ctx, ast.Load)  # a store or delete rewrites the module's attribute
        and isinstance(node.value, ast.Name)
        and safe.get(node.value.id) == node.attr
    )


def rebound_safe_modules(tree: ast.AST) -> frozenset[str]:
    """Safe-attribute modules this tree may rewrite through a handle: a store or delete of an
    attribute named like the module (``me.re = x``) or a string equal to its name
    (``setattr(me, "re", x)``)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return frozenset(found & SAFE_ATTRS.keys())


def _safe_roots(tree: ast.Module, shadowed: frozenset[str]) -> dict[str, str]:
    """Names bound only by a plain ``import re`` / ``import regex`` (alias allowed) that nothing in
    the file rebinds, mapped to the one attribute they may read; empty after a star import."""
    shadowed = shadowed | rebound_safe_modules(tree)
    bound: dict[str, str] = {}
    other: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in SAFE_ATTRS and alias.name not in shadowed:
                    bound[alias.asname or alias.name] = SAFE_ATTRS[alias.name]
                else:
                    other.update(alias.name.split("."))
                    other.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return {}
        else:
            other.update(_bound_identifiers(node))
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                other.add(node.id)
    return {name: attr for name, attr in bound.items() if name not in other}
