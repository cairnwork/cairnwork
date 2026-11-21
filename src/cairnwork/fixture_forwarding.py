"""Static helpers for decorators and fixture-name forwarding."""

from __future__ import annotations

import ast
import re
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
    "__getattr__ __getattribute__ __builtins__".split()
)
_ALL = "*"
# Names that can reach or rewrite any module: one anywhere in the tree voids every stdlib module.
_WIDE = _PATCH | frozenset({"sys.modules", "vars", "globals", "locals", "__builtins__", "builtins"})
# Positive allowlist: the only stdlib modules whose import cannot load or run another file or
# execute text. Any other stdlib import (importlib, pydoc, runpy, timeit, os, unittest ...) in a
# file makes that file fall back to main, and in any tree file voids every stdlib module.
_INERT = frozenset(
    "__future__ abc bisect collections contextlib copy dataclasses datetime decimal enum "
    "fractions functools heapq itertools json math numbers operator pathlib random re string "
    "sys textwrap time types typing uuid warnings weakref".split()
)
_MODULE_STR = re.compile(r"[A-Za-z_][\w]*(\.[A-Za-z_]\w*)*")


def _tokens(tree: ast.Module) -> set[str]:
    """Dynamic-access evidence in ``tree``: patch or lookup names, ``.modules`` on any base, an
    import of a stdlib module outside ``_INERT``, a string naming a stdlib module (dotted too)."""
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add("sys.modules" if n.attr == "modules" else n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            found.add(n.value)
            if _MODULE_STR.fullmatch(n.value) and n.value.split(".")[0] in sys.stdlib_module_names:
                found.add("str-module")
        elif isinstance(n, ast.ImportFrom | ast.Import):
            mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            mods += [a.name for a in n.names]
            found |= {
                "exec-import"
                for m in mods
                if m.split(".")[0] in sys.stdlib_module_names and m.split(".")[0] not in _INERT
            }
            found |= {"sys.modules" for a in n.names if a.name == "modules"}
    return found & (_DYNAMIC | {"sys.modules", "exec-import", "str-module"})


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


def _binds(tree: ast.Module, root: str) -> bool:
    """Is ``root`` bound in any form but exactly one import statement alias?"""
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
    return sum(name == root for name, _, _ in _imports(tree)) != 1


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
            if ok and not _binds(tree, name):
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
    ``__dict__`` or ``sys.modules``); ``*`` when the target cannot be named: a store through a
    longer chain or a non-stdlib root, an import outside the allowlist, a stdlib module named
    by string."""
    result: set[str] = set()
    for tree in trees.values():
        names = {n: m.split(".")[0] for n, m, _ in _imports(tree)}
        tokens = _tokens(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Load):
                top = names.get(_chain_root(n) or "")
                if top in sys.stdlib_module_names and isinstance(n.value, ast.Name):
                    result.add(top)
                elif isinstance(n.value, ast.Attribute):
                    result.add(_ALL)
        if tokens & _WIDE:
            result.add(_ALL)
        bare = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        if tokens & {"exec-import", "str-module"} or bare & {
            "exec",
            "eval",
            "compile",
            "__import__",
        }:
            result.add(_ALL)  # runs text or imports by name: the patch can sit in any file
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
