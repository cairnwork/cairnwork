"""Whether a candidate's edit to one Python file touches import machinery (#1065).

Statements are compared with the base blob (AST, comments dropped, nested blocks emptied, keyed
by the enclosing def/class names, block headers and block fields). A statement is hot when its own
source names a machinery word, or names a def or class whose body reaches an import-machinery
word (``reach``: sys.modules, finders, importlib, __import__, runpy, pytest_plugins; directly or
through other same-file defs). If that word scan misses, direct calls to getattr/setattr are
allowed only with a safe literal identifier; vars is allowed only as vars(self) or vars(cls),
when those names are not rebound in the file. Their targets are intentionally not inspected:
parameters, aliases and other expressions cannot be resolved soundly here. Indirect uses and
unknown call shapes are refused. Rule (5): a call whose func is itself a call to getattr is
refused regardless of the attribute name; getattr used only to read an attribute remains subject
to the safe-literal rule above. An added or removed hot statement makes the edit machinery, so
writing, rewriting or moving machinery, calling dead base machinery, or flipping the condition
that wraps it (the key of every statement inside changes) is caught, while a fix that calls a
same-file helper is not.
Residual (#1115): an edit to other control flow inside a function that reaches machinery (say an
early return before it), and base machinery reached through aliases, registries or dunders, are
not hot; broader lexical matching raised hot statements in real signalling files, so these are
left to the blackbox and verifier checks.
"""

from __future__ import annotations

import ast
import copy
from collections import Counter

_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

Key = tuple[str, str]


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
        rebound = _reassigned_special_names(tree)
        found: Counter[Key] = Counter()
        hot: set[Key] = set()
        _visit(list(tree.body), "", words, hot_names, rebound, denylist, found, hot)
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
    rebound: set[str],
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
        if (
            _has_word(text, words)
            or _names(own) & hot_names
            or (not _has_word(text, words) and _unsafe_dynamic_access(own, rebound, denylist))
        ):
            hot.add(key)
        inner = f"{scope}/{node.name if isinstance(node, _DEFS) else text}"
        for field, block in blocks:  # the field keeps an if/else or try/else move visible
            _visit(block, f"{inner}:{field}", words, hot_names, rebound, denylist, found, hot)


def _reassigned_special_names(tree: ast.Module) -> set[str]:
    """Special vars receiver names assigned anywhere in the file."""
    rebound = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id in {"self", "cls"}
        and isinstance(node.ctx, ast.Store)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in {"self", "cls"}:
                rebound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name in {"self", "cls"}:
                    rebound.add(name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            defaults.extend(args.kw_defaults)
            for arg, default in zip([*positional, *args.kwonlyargs], defaults, strict=True):
                if arg.arg in {"self", "cls"} and default is not None:
                    rebound.add(arg.arg)
    return rebound


def _unsafe_dynamic_access(node: ast.AST, rebound: set[str], denylist: frozenset[str]) -> bool:
    names = {"getattr", "setattr", "vars"}
    calls = [part for part in ast.walk(node) if isinstance(part, ast.Call)]
    direct = {id(call.func) for call in calls if isinstance(call.func, ast.Name)}
    for part in ast.walk(node):
        named = isinstance(part, ast.Name) and part.id in names
        attributed = isinstance(part, ast.Attribute) and part.attr in names
        if (named and id(part) not in direct) or attributed:
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
        if kind == "vars":
            if (
                len(call.args) != 1
                or not isinstance(call.args[0], ast.Name)
                or call.args[0].id not in {"self", "cls"}
                or call.args[0].id in rebound
            ):
                return True
            continue
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
