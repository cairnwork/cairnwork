"""#987: the ``from M import f`` names a test module binds under a test name (feeds
conftest_names._test_bound_helpers)."""

from __future__ import annotations

import ast
import sys
from posixpath import dirname

from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.fixture_forms import CODE, module_level

_Pair = tuple[str, str]  # (module, name); name "" is the module itself, "*" a star import


def _test_named_imports(file: str, tree: ast.Module) -> list[_Pair]:
    """(module, name) of each import that ends up under a name starting with ``test``, through
    any chain of module-level bindings (``h = f``, ``test_x = h``; in ``if``/``try``/``with``/
    ``for`` blocks too): assignment, annotated assignment, ``:=``, ``with ... as``, ``for``.
    Any name or ``module.name`` read in the bound value counts (a wrapping call too). A star
    import yields (module, "*"). Flow-insensitive; over-taking only tightens."""
    src: dict[str, set[_Pair]] = {}
    links: list[tuple[set[str], ast.AST]] = []
    for node in module_level(tree.body):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.name if a.asname else a.name.split(".")[0]
                src.setdefault(a.asname or name, set()).add((name, ""))
        elif isinstance(node, ast.ImportFrom):
            up = dirname(file)
            for _ in range(max(node.level - 1, 0)):
                up = dirname(up)
            head = ".".join(filter(None, [*(up.split("/") if node.level else ()), node.module]))
            for a in node.names:
                key = "test*" if a.name == "*" else a.asname or a.name
                src.setdefault(key, set()).update(
                    {(head, a.name), (f"{head}.{a.name}".strip("."), "")}
                )
        elif not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            links += _links(node)
    while True:
        before = sum(len(v) for v in src.values())
        for names, value in links:
            got = _read(value, src)
            for name in names:
                src.setdefault(name, set()).update(got)
        if sum(len(v) for v in src.values()) == before:
            break
    return sorted({p for n, ps in src.items() if n.startswith("test") for p in ps if p[1]})


def _read(value: ast.AST, src: dict[str, set[_Pair]]) -> set[_Pair]:
    """Every name read in ``value``; ``a.b.c`` of a module ``a`` is (``a.b``, ``c``)."""
    out: set[_Pair] = set()
    inner = {id(n.value) for n in ast.walk(value) if isinstance(n, ast.Attribute)}
    for n in ast.walk(value):
        if isinstance(n, ast.Name):
            out |= src.get(n.id, set())
        elif isinstance(n, ast.Attribute) and id(n) not in inner:
            parts, root = [], n
            while isinstance(root, ast.Attribute):
                parts.insert(0, root.attr)
                root = root.value
            if isinstance(root, ast.Name):
                path = ".".join(parts[:-1])
                out |= {
                    (f"{m}.{path}".strip("."), parts[-1]) for m, o in src.get(root.id, ()) if not o
                }
    return out


def _targets(module: str, name: str, index: dict[str, set[str]]) -> set[str]:
    """Base files of ``module``. One in no base file and not a submodule ``module.name`` has
    parameters that cannot be read: refused, except the standard library and pytest."""
    if module in index or f"{module}.{name}" in index:
        return index.get(module, set())
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top in {"pytest", "_pytest"}:
        return set()
    raise TestCommitRejected(
        CODE, f"a base test name is bound to {module!r}.{name!r}, which is not in the base"
    )


def _links(node: ast.stmt) -> list[tuple[set[str], ast.AST]]:
    """(target names, value) of each binding in ``node`` itself (not its nested blocks)."""
    pairs: list[tuple[ast.AST, ast.AST | None]] = []
    if isinstance(node, ast.Assign):
        pairs = [(t, node.value) for t in node.targets]
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        pairs = [(node.target, node.value)]
    elif isinstance(node, ast.For | ast.AsyncFor):
        pairs = [(node.target, node.iter)]
    elif isinstance(node, ast.With | ast.AsyncWith):
        pairs = [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars]
    pairs += [(n.target, n.value) for n in ast.walk(node) if isinstance(n, ast.NamedExpr)]
    return [({n.id for n in ast.walk(t) if isinstance(n, ast.Name)}, v) for t, v in pairs if v]


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = node.args
    return {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if x}
