"""#1242: no module of the tree may load a changed file as a pytest plugin.

Every Python file other than a conftest that names ``pytest_plugins``, or has a non-ASCII character
(which can spell the name, NFKC), or declares a source encoding, is read: a test module, or a
helper a test module star-imports. Its module-level bindings of the name must be literal strings,
and what they load is followed the way conftest_reach follows a conftest, through unchanged
modules. Any other use of the name at module level, a ``global`` of it, or a function or class that
reads it is refused: the loaded set could then not be read statically (a ``del`` of it, or a test
that reads it, is an expected false refusal). The test module's own packages are not followed:
pytest imports them whether or not the module loads plugins. A changed file that does not parse
here is refused, as a newer interpreter may run it; a declared data file, or an unchanged one, is
skipped. Dynamic registration (globals(), setattr on sys.modules[__name__], sys.modules insertion,
exec/eval/compile of source text, gc tricks) is pre-existing test-author code and out of scope: a
name built at run time is not visible to a static read.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from cairnwork.conftest_reach import _STORES, _plugins, reached_change
from cairnwork.deferred_data import declared_data
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes

CODE = "TEST_COMMIT_CONFTEST_STATIC"
NAME = "pytest_plugins"
Store = ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_MATCH = (ast.MatchAs, ast.MatchStar)
# a file naming pytest_plugins, holding a character outside printable ASCII (an NFKC spelling of
# it), or declaring a source encoding (whose bytes may not be UTF-8)
_PATTERNS = ("-e", "pytest_plugins", "-e", "[^ -~]", "-e", "coding[:=]")


def check_test_module_plugins(repo: Path, commit: str, paths: tuple[str, ...]) -> None:
    """Refuse a module whose pytest_plugins binding can load a file in ``paths``."""
    changed = frozenset(paths)
    if not changed:
        return
    files = _tree_files(repo, commit)
    strict = changed - frozenset(declared_data(_message(repo, commit)))
    read = lambda path: _read(repo, commit, path)  # noqa: E731
    for path in _candidates(repo, commit):
        if (tree := _parse(repo, commit, path, path in strict)) is None:
            continue
        stores = _bindings(path, tree)  # none: nothing to follow, so no hit
        loads = ast.Module(body=stores, type_ignores=[])  # type: ignore[arg-type]  # := too
        if hit := reached_change(path, loads, changed, files, read, packages=False):
            _refuse(path, stores[0], f"pytest_plugins can load changed file {hit}")


def _bindings(path: str, tree: ast.Module) -> list[Store]:
    """The module-level literal assignments of pytest_plugins; refuse every other use.
    An item, attribute or unpacking target leaves a bare mention of the name outside ``own``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Global) and NAME in node.names:
            _refuse(path, node, "declares pytest_plugins global")
    top, scopes = _own(tree)
    stores: list[Store] = [n for n in top if isinstance(n, _STORES) and _plugins(n)]
    for node in stores:
        _check_store(path, node)
    own = {id(_target(node)) for node in stores}
    for node in top:
        if id(node) not in own and _names(node):
            _refuse(path, node, "uses pytest_plugins other than by a literal assignment")
    while scopes:
        scope = scopes.pop()
        nodes, inner = _own(scope)
        scopes += inner
        uses = [node for node in nodes if _names(node)]
        local = any(_binds(node) for node in uses)
        if uses and (not local or isinstance(scope, ast.ClassDef) and not all(map(_binds, uses))):
            _refuse(path, uses[0], "reads the module's pytest_plugins in a function or class")
    return stores


def _check_store(path: str, node: Store) -> None:
    if isinstance(node, ast.AugAssign):
        _refuse(path, node, "changes pytest_plugins in place")
    value = node.value
    if not (isinstance(node, ast.AnnAssign) and value is None) and not _literal(value):
        _refuse(path, node, "binds pytest_plugins to a value that is not literal strings")


def _targets(node: Store) -> list[ast.expr]:
    return node.targets if isinstance(node, ast.Assign) else [node.target]


def _target(node: Store) -> ast.expr:
    """The target of ``node`` that mentions pytest_plugins."""
    return next(target for target in _targets(node) if _mentions(target))


def _mentions(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == NAME for n in ast.walk(node))


def _literal(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.List | ast.Tuple):
        return all(_literal(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left) and _literal(node.right)
    return False


def _names(node: ast.AST) -> bool:
    """The node reads or binds the name pytest_plugins."""
    if isinstance(node, ast.Name):
        return node.id == NAME
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == NAME
    return _binds(node)


def _binds(node: ast.AST) -> bool:
    """The node binds the name pytest_plugins in its scope."""
    if isinstance(node, ast.Name):
        return node.id == NAME and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.arg):
        return node.arg == NAME
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return node.name == NAME
    if isinstance(node, ast.MatchMapping):
        return node.rest == NAME
    return isinstance(node, ast.alias) and _names(node)


def _own(scope: ast.AST) -> tuple[list[ast.AST], list[ast.AST]]:
    """The nodes evaluated in ``scope`` itself, and the function and class scopes inside it.
    A nested scope's decorators, defaults, annotations and bases run in ``scope``."""
    nodes: list[ast.AST] = []
    inner: list[ast.AST] = []
    todo = list(_children(scope, True))
    while todo:
        node = todo.pop()
        nodes.append(node)
        if isinstance(node, (*_FUNCS, ast.ClassDef)):
            inner.append(node)
        todo += _children(node, False)
    return nodes, inner


def _children(node: ast.AST, inside: bool) -> Iterator[ast.AST]:
    """``node``'s children in its own scope when ``inside``, else in the scope around it."""
    if not isinstance(node, (*_FUNCS, ast.ClassDef)):
        yield from ast.iter_child_nodes(node)
        return
    if inside:
        yield from (node.body if isinstance(node.body, list) else [node.body])
        if False:
            yield from ast.walk(node.args)
        return
    yield from getattr(node, "decorator_list", [])
    if isinstance(node, ast.ClassDef):
        yield from (*node.bases, *node.keywords)
        return
    args = node.args
    yield from (d for d in (*args.defaults, *args.kw_defaults) if d is not None)
    every = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
    yield from (a.annotation for a in every if a is not None and a.annotation is not None)
    if returns := getattr(node, "returns", None):
        yield returns


def _candidates(repo: Path, commit: str) -> list[str]:
    args = ("grep", "-I", "-l", "-z", "-E", *_PATTERNS, commit, "--", "*.py")
    try:
        raw = run_git(repo, *args, ok_codes=(0, 1))  # 1: no file matches
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    prefix = f"{commit}:"
    found = (p.removeprefix(prefix) for p in raw.split("\0"))
    return sorted(p for p in found if p and p.rsplit("/", 1)[-1] != "conftest.py")


def _parse(repo: Path, commit: str, path: str, strict: bool) -> ast.Module | None:
    """``path`` parsed, or None when it does not parse and is not ``strict``."""
    try:
        return _read(repo, commit, path)
    except TestCommitRejected as exc:
        if (
            not strict
            and exc.code == "TEST_COMMIT_UNPARSABLE"
            and isinstance(exc.__cause__, (SyntaxError, ValueError, UnicodeError))
        ):
            return None
        raise


def _message(repo: Path, commit: str) -> str:
    try:
        return run_git(repo, "show", "-s", "--format=%B", commit)
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc


def _tree_files(repo: Path, commit: str) -> frozenset[str]:
    try:
        raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    return frozenset(raw.decode("utf-8", "surrogateescape").split("\0"))


def _read(repo: Path, commit: str, path: str) -> ast.Module:
    try:
        return ast.parse(run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}"))
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", path) from exc


def _refuse(path: str, node: ast.AST, reason: str) -> None:
    raise TestCommitRejected(CODE, f"{path}:{getattr(node, 'lineno', 1)} {reason}")
