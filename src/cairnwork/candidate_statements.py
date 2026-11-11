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
"""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Iterator

_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

Key = tuple[str, str]


def _safe_vars_self(tree: ast.Module, statement: ast.AST) -> bool:
    """Allow only vars(receiver) in an unrebound, directly declared instance/class method."""
    from cairnwork.candidate_vars_self import safe_vars_self_statement

    return safe_vars_self_statement(tree, statement)


def machinery_changed(
    old: bytes | None,
    new: bytes,
    words: tuple[bytes, ...],
    reach: tuple[bytes, ...],
    denylist: frozenset[str],
) -> bool:
    """True when an added or removed statement is hot. ``old`` None means the file is new; an
    unparseable base trusts nothing. Raises ValueError when ``new`` cannot be parsed."""
    after = _hot_statements(new, words, reach, denylist)
    if after is None:
        raise ValueError("candidate source could not be parsed")
    before = (Counter(), set()) if old is None else _hot_statements(old, words, reach, denylist)
    if before is None:  # nothing in an unparseable base is trusted
        before = (Counter(), set())
    added = after[0] - before[0]
    removed = before[0] - after[0]
    return any(k in after[1] for k in added) or any(k in before[1] for k in removed)


def _hot_statements(
    data: bytes, words: tuple[bytes, ...], reach: tuple[bytes, ...], denylist: frozenset[str]
) -> tuple[Counter[Key], set[Key]] | None:
    try:
        tree = ast.parse(data)
        hot_names = _hot_defs(tree, reach)
        found: Counter[Key] = Counter()
        hot: set[Key] = set()
        _visit(list(tree.body), "", words, hot_names, denylist, found, hot, tree)
    except _PARSE_ERRORS:
        return None
    return found, hot


def _hot_defs(tree: ast.Module, words: tuple[bytes, ...]) -> set[str]:
    """Names of defs and classes whose body names a ``words`` entry or another hot def."""
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
    denylist: frozenset[str],
    found: Counter[Key],
    hot: set[Key],
    tree: ast.Module,
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
        scan_hit = _has_word(text, words)
        exempt = scan_hit and _exempt_dynamic_word_scan(text, own, words, denylist, tree, node)
        if (scan_hit and not exempt) or _names(own) & hot_names:
            hot.add(key)
        inner = f"{scope}/{node.name if isinstance(node, _DEFS) else text}"
        for field, block in blocks:  # the field keeps an if/else or try/else move visible
            _visit(block, f"{inner}:{field}", words, hot_names, denylist, found, hot, tree)


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
    text: str,
    node: ast.AST,
    words: tuple[bytes, ...],
    denylist: frozenset[str],
    tree: ast.Module,
    original: ast.AST,
) -> bool:
    """Exempt only word hits fully accounted for by safe direct getattr/setattr calls."""
    other_words = tuple(word for word in words if word not in {b"getattr", b"setattr", b"vars"})
    if _has_word(text, other_words):
        return False
    vars_count = sum(isinstance(part, ast.Name) and part.id == "vars" for part in ast.walk(node))
    if text.count("vars") != vars_count:
        return False
    if _unsafe_dynamic_access(node, denylist):
        vars_names = [
            part for part in ast.walk(node) if isinstance(part, ast.Name) and part.id == "vars"
        ]
        if not vars_names or not _safe_vars_self(tree, original):
            return False
        stripped = copy.deepcopy(node)
        for part in ast.walk(stripped):
            if isinstance(part, ast.Name) and part.id == "vars":
                part.id = "__safe_vars_self__"
        if _unsafe_dynamic_access(stripped, denylist):
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
