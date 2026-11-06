"""#798 half 2: a test commit may add fixtures to a ``conftest.py`` (hygiene check, not proof).

The proof is the differential base probe (p2p_conftest.py): the same base tests must have the same
outcomes with and without the test commit's conftest. This module is the cheap, static half that
runs first, at the producer and (full mode) at the receiver:

* only the exact basename ``conftest.py`` (an add or a modify; the caller refuses a delete, a
  rename and any other case of the name);
* additive: the base file's top-level nodes are unchanged, new nodes come after them;
* a new node is an import (standard library, ``pytest`` or the repository's own top-level
  packages, no star, no ``pytest_*`` name), a literal assignment to a plain name, or a function
  whose only decorators are the pytest fixture decorator (resolved through the import table; literal
  arguments, no ``autouse``; literal parameter defaults, annotations that call nothing), never a
  ``pytest_*``, ``test*`` or dunder name. No classes;
* no conftest of the test commit's tree, changed or not, has an import that can load a file the
  test commit adds or changes (conftest_reach.py, #924); this runs even when no conftest changed,
  at the producer and in the pipeline judge (a full receiver checks only the task's conftest
  paths, #952).

It is hygiene, not the guard: a name imported from the repository or rebound can carry behaviour
it cannot see, and behaviour that depends on the candidate's code is trusted test author code, like
any test file. The differential probe (p2p_conftest.py) is what holds. Every refusal is
``TEST_COMMIT_CONFTEST_STATIC``.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from functools import partial
from pathlib import Path, PurePosixPath
from posixpath import dirname

from cairnwork.conftest_reach import reached_change
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes

CONFTEST = "conftest.py"
CODE = "TEST_COMMIT_CONFTEST_STATIC"


def conftest_of(paths: Iterable[str]) -> tuple[str, ...]:
    """The paths whose basename is exactly ``conftest.py`` (sorted)."""
    return tuple(sorted(p for p in paths if PurePosixPath(p).name == CONFTEST))


def without_conftests(paths: Iterable[str]) -> tuple[str, ...]:
    """``paths`` less the conftests: those are not verifier targets (``untargeted``), their check
    is the differential probe (#798)."""
    gone = set(conftest_of(paths))
    return tuple(p for p in paths if p not in gone)


def governed(conftests: Iterable[str], existing: Iterable[str]) -> tuple[str, ...]:
    """The base test files under a conftest's directory: the tests it can change."""
    dirs = tuple((dirname(c) + "/") if dirname(c) else "" for c in conftests)
    return tuple(sorted(t for t in existing if t.startswith(dirs)))


def check_conftests(repo: Path, base: str, test_commit: str, paths: Iterable[str]) -> None:
    """Refuse a conftest change the allowlist does not cover; see the module docstring."""
    paths = tuple(paths)
    names = conftest_of(paths)
    own = _own_packages(repo, base) if names else frozenset[str]()
    for path in names:
        new = _parse(repo, f"{test_commit}:{path}", path, present=True)
        old = _parse(repo, f"{base}:{path}", path, present=False)
        before = [ast.dump(n) for n in old.body]
        if before != [ast.dump(n) for n in new.body[: len(before)]]:
            _refuse(path, new.body[0] if new.body else None, "changes existing top-level code")
        table = _imports(new)
        for node in new.body[len(before) :]:
            problem = _rebinds(node, table) or _problem(node, own, table)
            if problem:
                _refuse(path, node, problem)
    if paths:
        _check_reach(repo, test_commit, paths)


def _check_reach(repo: Path, test_commit: str, paths: tuple[str, ...]) -> None:
    """#924: no conftest of the test commit's tree, changed by it or not (a base conftest may
    already import a module the test commit changes), can load a file the test commit changes.
    Names are matched under any directory, so the verifier's import roots need no reading."""
    files = _tree_files(repo, test_commit)
    read = partial(_read, repo, test_commit)
    for path in conftest_of(files):
        changed = frozenset(paths) - {path}
        if hit := reached_change(path, read(path), changed, files, read):
            why = f"can load {hit}, which the test commit changes" if hit in changed else hit
            _refuse(path, None, f"an import that {why}")


def _tree_files(repo: Path, commit: str) -> frozenset[str]:
    """The tree's paths; one that is not UTF-8 is kept undecodable (no import resolves to it)
    rather than failing the whole check (#924 r4)."""
    try:
        listing = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    return frozenset(listing.decode("utf-8", "surrogateescape").split("\0"))


def _read(repo: Path, commit: str, path: str) -> ast.Module:
    return _parse(repo, f"{commit}:{path}", path, present=True)


def _refuse(path: str, node: ast.AST | None, why: str) -> None:
    raise TestCommitRejected(CODE, f"{path}:{getattr(node, 'lineno', 1)} {why}")


def _parse(repo: Path, blob: str, path: str, *, present: bool) -> ast.Module:
    try:
        source = run_git(repo, "show", blob)
    except GitCommandError as exc:
        if present:
            raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
        return ast.Module(body=[], type_ignores=[])  # a new file
    except UnicodeDecodeError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", f"{path} is not UTF-8") from exc
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", path) from exc


def _own_packages(repo: Path, base: str) -> frozenset[str]:
    """Top-level names of the repository's tree (and of ``src/``): its own packages."""
    names: list[str] = []
    for tree in (base, f"{base}:src"):
        try:
            names += run_git(repo, "ls-tree", "--name-only", tree).split("\n")
        except GitCommandError:
            pass  # no src/ on this base
    return frozenset(PurePosixPath(n).stem for n in names if n)


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local name -> dotted origin, for the module's top-level imports."""
    table: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                table[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            table |= {(a.asname or a.name): f"{node.module}.{a.name}" for a in node.names}
    return table


def _problem(node: ast.stmt, own: frozenset[str], table: dict[str, str]) -> str:
    if isinstance(node, ast.Import | ast.ImportFrom):
        return _import_problem(node, own)
    if isinstance(node, ast.Expr):  # a docstring
        ok = isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        return "" if ok else "an expression statement"
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return _assign_problem(node)
    if isinstance(node, ast.FunctionDef):
        return _fixture_problem(node, table)
    return f"a {type(node).__name__} is not allowed"


def _rebinds(node: ast.stmt, table: dict[str, str]) -> str:
    """A new assignment or function that takes the name of an import would change what a decorator
    (or any later name) resolves to, so the import table could not be trusted."""
    if isinstance(node, ast.Assign):
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names = {node.target.id}
    elif isinstance(node, ast.FunctionDef):
        names = {node.name}
    else:
        return ""
    return "a name that rebinds an import" if names & table.keys() else ""


def _reserved(name: str) -> bool:
    return name.startswith(("pytest", "test", "collect_")) or (
        name.startswith("__") and name.endswith("__")
    )


def _known(root: str, own: frozenset[str]) -> bool:
    """A module the repository has is a repository module first (it shadows a standard library
    name on the verifier's path); otherwise the standard library or pytest."""
    return root in own or root == "pytest" or root in sys.stdlib_module_names


def _import_problem(node: ast.Import | ast.ImportFrom, own: frozenset[str]) -> str:
    if isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        trusted = (
            node.level > 0 or root in own or root == "pytest" or root in sys.stdlib_module_names
        )
    else:
        trusted = all(_known(a.name.split(".")[0], own) for a in node.names)
    if not trusted:
        return "an import outside the standard library, pytest and the repository's packages"
    for alias in node.names:
        if (
            alias.name == "*"
            or alias.name.startswith("pytest_")
            or (alias.asname or "").startswith("pytest_")
        ):
            return "a star import or a pytest_* name"
    return ""


def _assign_problem(node: ast.Assign | ast.AnnAssign) -> str:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if len(targets) != 1 or not isinstance(targets[0], ast.Name) or node.value is None:
        return "an assignment that is not a plain name with a value"
    if _reserved(targets[0].id):
        return "an assignment to a reserved name"
    return "" if _literal(node.value) else "an assigned value that is not a literal"


def _literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _fixture_problem(node: ast.FunctionDef, table: dict[str, str]) -> str:
    if _reserved(node.name):
        return "a function with a reserved name"
    if not node.decorator_list:
        return "a function that is not a fixture"
    for dec in node.decorator_list:
        func, args, keywords = (
            (dec.func, dec.args, dec.keywords) if isinstance(dec, ast.Call) else (dec, [], [])
        )
        if _origin(func, table) != "pytest.fixture":
            return "a decorator other than the pytest fixture decorator"
        if any(k.arg in (None, "autouse") for k in keywords):
            return "an autouse or starred fixture argument"
        if not all(_literal(a) for a in (*args, *(k.value for k in keywords))):
            return "a fixture argument that is not a literal"
    return _signature_problem(node.args, node.returns)


def _signature_problem(args: ast.arguments, returns: ast.expr | None) -> str:
    """#924 r4: defaults and annotations run when the conftest is imported. A default must be a
    literal; an annotation names a type (no call, walrus or lambda)."""
    defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    if not all(_literal(d) for d in defaults):
        return "a parameter default that is not a literal"
    params = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
    notes = [p.annotation for p in params if p is not None and p.annotation is not None]
    runs = (ast.Call, ast.NamedExpr, ast.Lambda)
    if any(isinstance(n, runs) for note in (*notes, returns) if note for n in ast.walk(note)):
        return "an annotation that runs code"
    return ""


def _origin(node: ast.expr, table: dict[str, str]) -> str:
    """The dotted origin of a Name or Attribute chain through the import table, else ''."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in table:
        return ""
    return ".".join([table[node.id], *parts])
