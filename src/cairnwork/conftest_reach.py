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
  do the literal names of a ``pytest_plugins`` assignment; every import counts, at any depth (a
  fixture body's too);
* the ``__init__.py`` of the conftest's directory and of each directory above it count too:
  pytest may import the conftest as a module of that package (r2, cw#948 cross-model review).

The bare name ``conftest`` is not matched: a changed conftest is checked by conftest_check.py
itself. A name built at run time, or an unchanged module outside the test commit's top
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
from collections.abc import Iterable, Iterator
from posixpath import dirname, join

MAX_FILES = 500  # unchanged modules followed per conftest before the walk refuses
MODULE_SUFFIXES = (".py", ".pyc", ".so", ".pyd")  # files an import statement can load (r6)


def reached_change(
    conftest: str,
    tree: ast.Module,
    changed: frozenset[str],
    files: frozenset[str],
    read,
) -> str:
    """The first file in ``changed`` that ``conftest`` (parsed as ``tree``) can load, following
    imports through the unchanged ``files`` under the top directories of ``changed``; or a reason
    when there are too many to follow; '' when none. ``read(path)`` parses a file of the tree."""
    tops = {path.split("/", 1)[0] for path in changed}
    queue, seen = [(conftest, tree)], {conftest}
    for init in sorted(_inits(conftest) & files):  # the packages pytest imports the conftest in
        if init in changed:
            return init
        seen.add(init)
        queue.append((init, read(init)))
    by_name = _names(files)
    while queue:
        here, parsed = queue.pop(0)
        for target in sorted(_targets(here, parsed, files, by_name)):
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


def _targets(here: str, tree: ast.Module, files: frozenset[str], by_name) -> set[str]:
    """The files of the tree that one import in ``tree`` (the file ``here``) may load."""
    found: set[str] = set()
    for level, names in _imports(tree):
        for name in names:
            parts = name.split(".") if name else []
            if not level:  # every dotted prefix, as a file under any directory
                for k in range(1, len(parts) + 1):
                    found |= by_name.get(".".join(parts[:k]), set())
                continue
            base = dirname(here)
            for _ in range(level - 1):
                base = dirname(base)
            # the empty module name tries the package's own __init__.py
            found |= _module_files(base, parts, files, by_name)
    return found


def _imports(tree: ast.Module) -> Iterator[tuple[int, Iterable[str]]]:
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
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _plugins(node):  # pytest_plugins
            yield 0, [t for x in _items(node.value) if (t := _text(x))]


def _text(node: ast.expr | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _items(node: ast.expr | None) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return [node] if node is not None else []


def _plugins(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets)


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
