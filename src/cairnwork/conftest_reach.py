"""#924: which files a test-commit conftest can load through its imports.

The static check (conftest_check.py) reads the conftest's own nodes, and the differential base
probe (p2p_conftest.py) sees only base test outcomes. A module that the conftest imports and that
the test commit itself adds or changes is neither: its import-time code runs before every test,
can change the runner, and leaves every base outcome as it was. So a conftest change is refused
when one of its imports can load such a file, directly or through unchanged repository files in
the test commit's own top directories (where every test-commit path lives).

Resolution over-approximates pytest on purpose (refusing too much is the safe side):

* an absolute import of ``a.b.c`` may load, whatever directory is on ``sys.path`` (the repository
  root, ``src/``, the verifier's import roots, a conftest's or test module's base directory in
  prepend mode, or one a conftest puts there itself), every ``.py`` file of the tree whose path
  ends in ``a``, ``a/b`` or ``a/b/c`` as a module or a package's ``__init__.py``, namespace
  packages included (#924 r4/r5); ``from M import n`` also tries ``M.n``;
* relative imports are resolved from the importing file's directory, to the same kinds of
  module file (#976);
* ``importlib.import_module("...")`` and ``__import__("...")`` with a literal name count as
  imports (a leading dot also relative to the file, a literal ``fromlist`` as submodules), and so
  do the string literals put in ``pytest_plugins`` (assignment, slice assignment, ``+=``, ``+``,
  a method call on the list, starred, comma-separated, loop/with/match targets, #1010); every
  import counts, at any depth (a fixture body's too);
* the ``__init__.py`` of the conftest's directory and of each directory above it count too:
  pytest may import the conftest as a module of that package (r2, cw#948 cross-model review);
* a star import (``from pkg import *`` or ``from .pkg import *``) reaches every module file
  directly under the package directory, and child subpackages (their ``__init__`` files) one level
  down (#981, #1057).

The bare name ``conftest`` is not matched: a changed conftest is checked by conftest_check.py
itself. A name built at run time (a ``pytest_plugins`` value that is not a literal, or the list set
or changed some other way, #1010), or an unchanged module outside the test commit's top
directories that imports a test-commit file, is not followed: that is test author code, as in
conftest_check.py. Nor are base test files: a changed module that only a base test file imports
also runs its import-time code in the base run (a known residual, #951, kept apart so that a test
commit may still update a shared test helper). An archive (``.zip``, ``.whl``) loads only once
its own path is on ``sys.path``, which no import names: conftest_check.py refuses the direct form
(a conftest or the pytest configuration naming the file); the rest is a known residual (#967).
This runs at the producer, in the pipeline judge and at a full receiver, on every test path
(#952).

Expected false refusals (fail closed): a data file under a test directory that is named like a
module some conftest imports (``tests/data/sample/calc.py`` when a conftest imports ``calc``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Sequence
from posixpath import dirname, join

from cairnwork.conftest_plugin_binds import imported_plugins, unreadable

MAX_FILES = 500  # unchanged modules followed per conftest before the walk refuses
MODULE_SUFFIXES = (".py", ".pyc", ".so", ".pyd")  # files an import statement can load (r6)
_DYNAMIC = frozenset({"setattr", "delattr", "exec", "eval", "globals", "locals", "vars"})


def reached_change(
    conftest: str,
    tree: ast.Module,
    changed: frozenset[str],
    files: frozenset[str],
    read,
    *,
    packages: bool = True,
) -> str:
    """The first file in ``changed`` that ``conftest`` (parsed as ``tree``) can load, following
    imports through the unchanged ``files`` under the top directories of ``changed``; or a reason
    when there are too many to follow; '' when none. ``read(path)`` parses a file of the tree.
    ``packages``: start from the packages pytest imports the conftest in."""
    tops = {path.split("/", 1)[0] for path in changed}
    queue, seen = [(conftest, tree)], {conftest}
    for init in sorted(_inits(conftest) & files if packages else ()):
        if init in changed:
            return init
        seen.add(init)
        queue.append((init, read(init)))
    by_name = _names(files)
    while queue:
        here, parsed = queue.pop(0)
        if (targets := _targets(here, parsed, files, by_name)) is None or imported_plugins(parsed):
            return "has an unreadable pytest_plugins with-binding"
        for target in sorted(targets):
            if target in changed:
                return target
            if target in seen or target.split("/", 1)[0] not in tops:
                continue
            if len(seen) > MAX_FILES:
                return f"has more than {MAX_FILES} modules to follow"
            seen.add(target)
            queue.append((target, read(target)))
    return ""


def _names(files: frozenset[str]) -> dict[str, set[str]]:
    """#924 r5: each dotted name a module file of the tree may be imported as, whatever directory
    is on ``sys.path``: every trailing run of its path parts (``a/b/c.py`` as ``c``, ``b.c`` and
    ``a.b.c``; ``a/b/__init__.py`` as ``b`` and ``a.b``), less the bare name ``conftest``. A
    native extension or bytecode file counts under the part of its name before the first dot
    (``colorsys.cpython-314-darwin.so`` as ``colorsys``, r6), and a cached one in ``__pycache__``
    as the module beside it (#980)."""
    out: dict[str, set[str]] = {}
    for path in files:
        parts = _stem(path).split("/") if path.endswith(MODULE_SUFFIXES) else []
        parts = parts[:-1] if parts[-1:] == ["__init__"] else parts
        for k in range(len(parts)):
            if (name := ".".join(parts[k:])) != "conftest":
                out.setdefault(name, set()).add(path)
    return out


def _targets(here: str, tree: ast.Module, files: frozenset[str], by_name) -> set[str] | None:
    """The files of the tree that one import in ``tree`` (the file ``here``) may load."""
    found: set[str] = set()
    for level, names in _imports(tree, stdlib="contextlib" not in by_name):
        if level < 0:
            return None
        for name in names:
            star = name == "*" or name.endswith(".*")
            if star:
                name = name[:-2] if name.endswith(".*") else ""
            parts = name.split(".") if name else []
            base = dirname(here)
            for _ in range(level - 1):
                base = dirname(base)
            if not level:  # every dotted prefix, as a file under any directory
                for k in range(1, len(parts) + 1):
                    found |= by_name.get(".".join(parts[:k]), set())
                inits = {
                    path
                    for path in by_name.get(name, ())
                    if _stem(path).rpartition("/")[2] == "__init__"
                }
            else:
                # the empty module name tries the package's own __init__.py
                modules = _module_files(base, parts, files, by_name)
                found |= modules
                package = join(base, *parts) if parts else base
                inits = {path for path in modules if _stem(path) == join(package, "__init__")}
            if star:
                directories = {dirname(_stem(init)) for init in inits}
                found |= {
                    path
                    for path in files
                    if (
                        _stem(path).rpartition("/")[0] in directories
                        and _stem(path).rpartition("/")[2] != "__init__"
                    )
                    or (
                        _stem(path).endswith("/__init__")
                        and dirname(_stem(path)[:-9]) in directories
                    )
                }
    return found


_STORES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)  # := too (#1027)


def _imports(tree: ast.Module, *, stdlib: bool = True) -> Iterator[tuple[int, Iterable[str]]]:
    """(level, dotted names it may load) per import in ``tree``, at any depth."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield 0, [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield node.level, [module, *(f"{module}.{a.name}".lstrip(".") for a in node.names)]
        elif isinstance(node, ast.Call) and _dynamic(node.func) and node.args:
            name = _text(node.args[0])
            subs = [_text(x) for k in node.keywords if k.arg == "fromlist" for x in _items(k.value)]
            names = [name, *(f"{name}.{sub}" for sub in subs if sub)] if name else []
            dots = len(name) - len(name.lstrip("."))  # import_module(".x", package=...)
            yield 0, [n.lstrip(".") for n in names]
            if dots:
                yield dots, [n.lstrip(".") for n in names]
        elif values := _plugin_values(node, tree if stdlib else None):
            if unreadable(node, values, tree):
                yield -1, ()
            else:
                yield 0, _strings(values)


def _strings(values: Sequence[ast.AST | None]) -> list[str]:
    """What a pytest_plugins value or call may name (#1010): each comma-separated part of every
    string literal in it, as pytest splits them, and of literals joined by ``+`` (#1018 r1)."""
    names: list[str] = []
    for value in (value for value in values if value is not None):
        order, todo = [], [value]
        while todo:  # parents before children; no recursion on a long chain (#1018 r2)
            order.append(node := todo.pop())
            todo += ast.iter_child_nodes(node)
        whole: dict[int, bool] = {}
        for node in reversed(order):  # children first: is the node one joined string?
            joins = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
            whole[id(node)] = _is_str(node) or (
                joins and whole[id(node.left)] and whole[id(node.right)])  # fmt: skip
        todo = [value]
        while todo:
            node = todo.pop()
            if not whole[id(node)]:
                todo += ast.iter_child_nodes(node)
                continue
            text = _joined(node) or ""  # a joined name stands alone: its parts are not names
            names += [part.strip() for part in text.split(",") if part.strip()]
    return names


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _joined(node: ast.AST) -> str | None:
    """A string literal, or string literals joined by ``+``: pytest loads the joined name. No
    recursion: a chain of thousands of ``+`` must not raise (#1018 r2)."""
    parts, todo = [], [node]
    while todo:
        here = todo.pop()
        if _is_str(here):
            parts.append(here.value)  # type: ignore[attr-defined]
        elif isinstance(here, ast.BinOp) and isinstance(here.op, ast.Add):
            todo += [here.right, here.left]  # the left part is taken first
        else:
            return None
    return "".join(parts)


def _text(node: ast.expr | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _items(node: ast.expr | None) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return [node] if node is not None else []


def _nullcontext_imports(tree: ast.Module, name: str, from_import: bool) -> set[int]:
    stmt = ast.ImportFrom if from_import else ast.Import
    target = "nullcontext" if from_import else "contextlib"
    return {
        id(a)
        for s in tree.body
        if isinstance(s, stmt)
        and (not isinstance(s, ast.ImportFrom) or (s.level == 0 and s.module == "contextlib"))
        for a in s.names
        if a.name == target and (a.asname or a.name) == name
    }


def _with_expr(expr: ast.expr, tree: ast.Module | None) -> ast.expr | None:
    if tree is None or not isinstance(expr, ast.Call):
        return None
    if len(expr.args) != 1 or expr.keywords or isinstance(expr.args[0], ast.Starred):
        return None
    fn = expr.func
    if isinstance(fn, ast.Name):
        name, from_import = fn.id, True
    elif (
        isinstance(fn, ast.Attribute)
        and fn.attr == "nullcontext"
        and isinstance(fn.value, ast.Name)
    ):
        name, from_import = fn.value.id, False
    else:
        return None
    if not (good := _nullcontext_imports(tree, name, from_import)):
        return None
    for n in ast.walk(tree):
        if isinstance(n, ast.alias):
            if n.name == "*" or (id(n) not in good and (n.asname or n.name.split(".")[0]) == name):
                return None
        elif (
            (
                isinstance(n, ast.Name)
                and (n.id in _DYNAMIC or (not isinstance(n.ctx, ast.Load) and n.id == name))
            )
            or (isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Load))
            or (isinstance(n, ast.MatchMapping) and n.rest == name)
            or (isinstance(n, ast.arg) and n.arg == name)
            or getattr(n, "name", None) == name
        ):
            return None
    return expr.args[0]


def _plain_index(node: ast.AST) -> bool:
    if isinstance(node, ast.Slice):
        return all(_plain_index(x) for x in (node.lower, node.upper, node.step) if x is not None)
    return isinstance(node, ast.Constant) and type(node.value) is int


def _paired_values(target: ast.AST, value: ast.expr | None) -> list[ast.expr | None]:
    if not _mentions(target):
        return []
    if isinstance(target, ast.Name) or (
        isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Name)
        and target.value.id == "pytest_plugins"
        and _plain_index(target.slice)
    ):
        return [value]
    if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
        plain = not any(isinstance(e, ast.Starred) for e in (*target.elts, *value.elts))
        if plain and len(target.elts) == len(value.elts):
            pairs = zip(target.elts, value.elts, strict=True)
            return [v for sub, val in pairs for v in _paired_values(sub, val)]
    return [None]


def _plugin_values(node: ast.AST, tree: ast.Module | None = None) -> Sequence[ast.AST | None]:
    """Expressions/patterns a node may feed into ``pytest_plugins`` (#1010)."""
    if isinstance(node, _STORES) and _plugins(node):
        return [node.value]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest_plugins":
            return [*node.args]  # list methods take no keywords
    if isinstance(node, ast.For | ast.AsyncFor) and _mentions(node.target):
        return [node.iter]
    if isinstance(node, ast.With | ast.AsyncWith):
        return [
            _with_expr(i.context_expr, tree)
            for i in node.items
            if i.optional_vars is not None and _mentions(i.optional_vars)
        ]
    if isinstance(node, ast.Match):
        pats = [
            p
            for c in node.cases
            for p in ast.walk(c.pattern)
            if _pattern_bound(p) == "pytest_plugins"
        ]
        if pats:  # Never walk MatchMapping keys: case {"y": pytest_plugins} binds d["y"], not "y".
            return [
                node.subject,
                *(p.pattern for p in pats if isinstance(p, ast.MatchAs) and p.pattern is not None),
            ]
    return ()


def _mentions(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "pytest_plugins" for n in ast.walk(node))


def _pattern_bound(node: ast.AST) -> str | None:
    if isinstance(node, ast.MatchAs | ast.MatchStar):
        return node.name
    return node.rest if isinstance(node, ast.MatchMapping) else None


def _plugins(node: ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr) -> bool:
    """The statement binds ``pytest_plugins``: as a name, an item or slice, or one target of an
    unpacking (#1010)."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(_mentions(t) for t in targets)


def _inits(conftest: str) -> set[str]:
    """The ``__init__.py`` of the conftest's directory and of each directory above it."""
    return {join(d, "__init__.py") for d in _above(dirname(conftest))}


def _dynamic(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "import_module"
    return isinstance(func, ast.Name) and func.id in ("__import__", "import_module")


def _module_files(start: str, parts: list[str], files: frozenset[str], by_name) -> set[str]:
    """The files of the tree that are each dotted prefix of ``parts`` under ``start``, as a module
    file or a package init: a ``.py`` file, or a native extension or bytecode file by the part of
    its name before the first dot (#976, as ``_names``)."""
    stems = {join(start, "__init__")} if start else set()
    for k in range(1, len(parts) + 1):
        stem = join(start, *parts[:k]) if start else join(*parts[:k])
        stems |= {stem, join(stem, "__init__")}
    stems = {stem.lstrip("/") for stem in stems}
    out = {f"{stem}.py" for stem in stems} & files
    for stem in stems:
        key = stem.removesuffix("/__init__").replace("/", ".")
        out |= {path for path in by_name.get(key, ()) if _stem(path) == stem}
    return out


def _stem(path: str) -> str:
    """``path`` less everything after the first dot of its file name, and less a ``__pycache__``
    directory it is in: Python loads ``d/__pycache__/m.<tag>.pyc`` as the module ``d/m`` (#980)."""
    head, _, name = path.rpartition("/")
    if head.rpartition("/")[2] == "__pycache__":
        head = head.rpartition("/")[0]
    return join(head, name.split(".", 1)[0])


def _above(directory: str) -> list[str]:
    """``directory`` and each directory above it, short of the repository root."""
    out = []
    while directory:
        out.append(directory)
        directory = dirname(directory)
    return out
