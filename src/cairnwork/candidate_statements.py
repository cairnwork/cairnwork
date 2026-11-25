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
count only as a bare name, an import of that name, a string that is the word or a dotted path
with the word as one segment (prose, log lines and docstrings are not hits), or an
attribute reached from a receiver that is not a plain chain of names or that mentions builtins
(or a same-file alias of it). A longer identifier (``evaluate``, ``compiled``) or ``re.compile`` is
not a hit; the receiver of ``.compile`` is still rebindable, which only the later checks see.
"""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

Key = tuple[str, str]
_EXACT = frozenset(
    {"vars", "globals", "locals", "exec", "eval", "compile", "breakpoint", "marshal", "pickle"}
)


@dataclass(frozen=True)
class _Words:
    """A word table split into substring words and exact-name words, plus the file's aliases."""

    sub: tuple[bytes, ...]
    exact: tuple[str, ...]
    aliases: frozenset[str]

    @classmethod
    def make(cls, words: tuple[bytes, ...], aliases: frozenset[str]) -> _Words:
        names = [word.decode() for word in words]
        return cls(
            tuple(w for w, n in zip(words, names, strict=True) if n not in _EXACT),
            tuple(n for n in names if n in _EXACT),
            aliases,
        )

    def without(self, *dropped: bytes) -> _Words:
        return _Words(tuple(w for w in self.sub if w not in dropped), self.exact, self.aliases)


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
        aliases = _builtins_aliases(tree)
        hot_names = _hot_defs(tree, _Words.make(reach, aliases))
        found: Counter[Key] = Counter()
        hot: set[Key] = set()
        _visit(list(tree.body), "", _Words.make(words, aliases), hot_names, denylist, found, hot)
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
    """Substring words match the unparsed text as before; exact-name words match by AST form."""
    if _has_word(ast.unparse(node), words.sub):
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in words.exact:
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)):
            if _names_word(_as_text(sub.value), words.exact):
                return True
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in sub.names] + [a.asname or "" for a in sub.names]
            if any(
                w in n for n in (*names, getattr(sub, "module", None) or "") for w in words.exact
            ):
                return True
        if isinstance(sub, ast.Attribute) and sub.attr in words.exact:
            if _reaches_builtins(sub.value, words.aliases):
                return True
    return False


def _names_word(text: str, exact: tuple[str, ...]) -> bool:
    """A string names a word only as a whole (``"exec"``) or as one segment of a dotted path
    (``"builtins.exec"``); prose, log lines and docstrings that merely contain it do not."""
    parts = text.strip().split(".")
    return all(p.isidentifier() for p in parts) and any(p in exact for p in parts)


def _as_text(value: str | bytes) -> str:
    return value if isinstance(value, str) else value.decode("latin-1")


def _reaches_builtins(receiver: ast.AST, aliases: frozenset[str]) -> bool:
    """A receiver is trusted only as a plain chain of names that never mentions builtins or an
    alias of it; a call, subscript or literal in front of the attribute could return anything."""
    for part in ast.walk(receiver):
        if isinstance(part, ast.Name):
            ident = part.id
        elif isinstance(part, ast.Attribute):
            ident = part.attr
        elif isinstance(part, ast.expr_context):
            continue
        else:
            return True
        if "builtins" in ident or ident in aliases:
            return True
    return False


def _mentions_builtins(node: ast.AST, aliases: set[str]) -> bool:
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            ident = part.id
        elif isinstance(part, ast.Attribute):
            ident = part.attr
        elif isinstance(part, ast.Constant) and isinstance(part.value, str):
            ident = part.value
        else:
            continue
        if "builtins" in ident or ident in aliases:
            return True
    return False


def _bindings(tree: ast.Module) -> Iterator[tuple[list[ast.AST], ast.AST | None]]:
    """(targets, value) pairs for every form that binds a name from an expression."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            yield list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            yield [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            yield [node.target], node.iter
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            yield [node.optional_vars], node.context_expr
        elif isinstance(node, ast.arguments):
            args = [*node.posonlyargs, *node.args]
            pairs = zip(args[len(args) - len(node.defaults) :], node.defaults, strict=True)
            kw = ((a, d) for a, d in zip(node.kwonlyargs, node.kw_defaults, strict=True) if d)
            for arg, default in (*pairs, *kw):
                yield [arg], default
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                src = f"{getattr(node, 'module', None) or ''}.{alias.name}"
                yield [ast.Name(alias.asname or alias.name.split(".")[0])], ast.Constant(src)


def _builtins_aliases(tree: ast.Module) -> frozenset[str]:
    """Names bound anywhere in the file to a value that mentions builtins, or to another alias."""
    pairs = list(_bindings(tree))
    aliases: set[str] = set()
    grew = True
    while grew:
        before = len(aliases)
        for targets, value in pairs:
            if value is not None and _mentions_builtins(value, aliases):
                for target in targets:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Name):
                            aliases.add(part.id)
                        elif isinstance(part, ast.Attribute):
                            aliases.add(part.attr)
                        elif isinstance(part, ast.arg):
                            aliases.add(part.arg)
        grew = len(aliases) > before
    return frozenset(aliases)
