"""Static helpers for decorators and fixture-name forwarding."""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator


def module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements running at module level, including conditional blocks."""
    for node in body:
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
        blocks += [
            part.body for part in (*getattr(node, "handlers", []), *getattr(node, "cases", []))
        ]
        for block in blocks:
            yield from module_level(block)


_PATCH = frozenset({"setattr", "delattr", "__dict__", "__setattr__", "__delattr__"})
_DYNAMIC = _PATCH | frozenset(
    "getattr vars globals locals exec eval compile __import__ importlib builtins "
    "__getattr__ __getattribute__".split()
)
_ALL = "*"


def _tokens(tree: ast.Module) -> set[str]:
    """Every Name, Attribute and string constant in ``tree`` that is a dynamic-access token."""
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add(n.attr)
            if n.attr == "modules" and isinstance(n.value, ast.Name) and n.value.id == "sys":
                found.add("sys.modules")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            found.add(n.value)
        elif isinstance(n, ast.ImportFrom) and n.module == "sys":
            found |= {"sys.modules" for a in n.names if a.name == "modules"}
        elif isinstance(n, ast.alias):
            found.add(n.name)
    return found & (_DYNAMIC | {"sys.modules"})


def _imports(tree: ast.Module) -> list[tuple[str, str, ast.stmt]]:
    """(bound name, module, statement) of every import in any scope; star imports excluded."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.asname or a.name.split(".")[0], a.name, n) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            module = ("." * n.level) + (n.module or "")
            out += [(a.asname or a.name, module, n) for a in n.names if a.name != "*"]
    return out


def _binds(tree: ast.Module, root: str, skip: ast.stmt) -> bool:
    """Is ``root`` bound anywhere in ``tree`` (any scope, any form) other than by ``skip``?"""
    for n in ast.walk(tree):
        hit = (
            (isinstance(n, ast.Name) and n.id == root and not isinstance(n.ctx, ast.Load))
            or (isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and n.name == root)
            or (isinstance(n, ast.arg) and n.arg == root)
            or (isinstance(n, ast.ExceptHandler) and n.name == root)
            or (isinstance(n, ast.MatchAs | ast.MatchStar) and n.name == root)
            or (isinstance(n, ast.MatchMapping) and n.rest == root)
            or (isinstance(n, ast.Global | ast.Nonlocal) and root in n.names)
            or (isinstance(n, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple) and n.name == root)
        )  # fmt: skip
        if hit:
            return True
    return any(name == root and stmt is not skip for name, _, stmt in _imports(tree))


def _chain_root(node: ast.expr) -> str | None:
    """The head Name of ``Name``, ``Name.attr...`` or ``Name(...)`` chains, else None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _decorator_roots(dec: ast.expr) -> list[str] | None:
    """Head root of a decorator plus the roots of its arguments, None for any other shape."""
    call = dec if isinstance(dec, ast.Call) else None
    head = _chain_root(call.func if call else dec)
    if head is None:
        return None
    roots = [head]
    for arg in (*(call.args if call else ()), *(k.value for k in (call.keywords if call else ()))):
        if isinstance(arg, ast.Constant):
            continue
        root = _chain_root(arg)
        if root is None:
            return None
        roots.append(root)
    return roots


def _candidates(tree: ast.Module, index: dict[str, set[str]] | None, voided: set[str]) -> set[str]:
    """Names bound once by a plain top-level absolute stdlib import, not shadowed, not rebound."""
    if _tokens(tree) or _ALL in voided:
        return set()
    stars = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             if any(a.name == "*" for a in n.names)]  # fmt: skip
    all_imports = _imports(tree)
    out = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Import | ast.ImportFrom):
            continue
        if isinstance(stmt, ast.ImportFrom) and (stmt.level or not stmt.module):
            continue
        for name, module, _ in (i for i in all_imports if i[2] is stmt):
            top = module.split(".")[0]
            ok = top in sys.stdlib_module_names and top not in voided
            ok = ok and not any(k.split(".")[0] == top for k in index or ())
            ok = ok and not any(line > stmt.lineno for line in stars)
            if ok and not _binds(tree, name, stmt):
                out.add(name)
    return out


def external_roots(
    tree: ast.Module, index: dict[str, set[str]] | None = None, voided: set[str] | None = None
) -> frozenset[str]:
    """Root names whose decorators cannot be fixture factories: ``_candidates`` that occur in the
    file only at their import and inside decorators made only of such roots and literals."""
    roots = _candidates(tree, index, voided or set())
    decos = [d for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef
             | ast.ClassDef) for d in n.decorator_list]  # fmt: skip
    while True:
        ok = {id(d) for d in decos if all(r in roots for r in _decorator_roots(d) or [""])}
        allowed = {id(x) for d in decos if id(d) in ok for x in ast.walk(d)}
        bad = {
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in roots and id(n) not in allowed
        }  # fmt: skip
        if not bad:
            return frozenset(roots)
        roots -= bad


def external_decorator(decorator: ast.expr, roots: frozenset[str]) -> bool:
    """True only for a decorator built from ``external_roots`` names and literals."""
    found = _decorator_roots(decorator)
    return found is not None and all(r in roots for r in found)


def voided_modules(trees: dict[str, ast.Module]) -> set[str]:
    """Stdlib modules some tree file may patch (attribute store or delete, ``setattr``,
    ``__dict__`` or ``sys.modules``); ``*`` when the target cannot be named."""
    result: set[str] = set()
    for tree in trees.values():
        names = {n: m.split(".")[0] for n, m, _ in _imports(tree)}
        tokens = _tokens(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Load):
                top = names.get(_chain_root(n) or "")
                if top:
                    result.add(top)
        if tokens & (_PATCH | {"sys.modules"}):
            result |= {t for t in names.values() if t in sys.stdlib_module_names}
            if tokens - _PATCH - {"sys.modules"}:
                result.add(_ALL)
    return result


def forwarded(tree: ast.Module) -> dict[int, set[str]]:
    """Parameters in scope for calls inside functions of a fixture-defining module."""
    if not any(getattr(node, "name", None) == "fixture" for node in module_level(tree.body)):
        return {}
    out: dict[int, set[str]] = {}
    for definition in ast.walk(tree):
        if isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            params = {
                arg.arg
                for arg in (
                    *definition.args.posonlyargs,
                    *definition.args.args,
                    *definition.args.kwonlyargs,
                    definition.args.vararg,
                    definition.args.kwarg,
                )
                if arg
            }
            for call in ast.walk(definition):
                if isinstance(call, ast.Call):
                    out.setdefault(id(call), set()).update(params)
    return out


def forwarded_names(tree: ast.Module) -> set[str]:
    """Literal fixture names supplied to wrappers that forward a name parameter."""
    functions = {
        node.name: node
        for node in module_level(tree.body)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    names: set[str] = set()
    for wrapper in functions.values():
        params = [arg.arg for arg in (*wrapper.args.posonlyargs, *wrapper.args.args)]
        params += [arg.arg for arg in wrapper.args.kwonlyargs]
        forwarded_params = {
            keyword.value.id
            for call in ast.walk(wrapper)
            if isinstance(call, ast.Call)
            for keyword in call.keywords
            if keyword.arg == "name"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id in params
        }
        for call in ast.walk(tree):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != wrapper.name
            ):
                continue
            values = dict(zip(params, call.args, strict=False))
            values.update({keyword.arg: keyword.value for keyword in call.keywords if keyword.arg})
            for param in forwarded_params:
                value = values.get(param)
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    names.add(value.value)
    return names
