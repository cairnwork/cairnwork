"""The test functions pytest collects from one test file, read from its AST (#660).

deferred_tests.new_tests and p2p_nodes.changed_tests compare these between the base and the test
commit. Read: each top-level ``test*`` function, and each ``test*`` method of a top-level test
class, its own or one it inherits from a class defined earlier in the same file and named
among its bases by a plain name (every base's version counts, #1009). pytest collects an inherited
test under the subclass's own node id too, so a change to a parent's test is a change to each
such copy, and a target naming one copy does not run the others.

A test class is one named ``Test*``, or with a base that is a plain or dotted name ending in
``TestCase`` (``TestCase``, ``unittest.TestCase``, ``SimpleTestCase``; pytest collects every
unittest subclass whatever its name, #792), or with a base defined earlier in the file that is
such a TestCase subclass. A base's own name is not resolved: ``from x import TestCase`` is trusted
by its spelling, and a class that merely looks like one is read too (pytest then runs none of its
tests, so the base run refuses it, DEFERRED_TESTS_NOT_RUN).

``uncollected``: the test classes with an ``__init__`` or ``__new__`` of their own or
inherited the same way. pytest does not collect such a class (it warns and runs none of its
tests), so a target naming the file still runs none of them. A ``unittest.TestCase`` subclass with
its own ``__init__`` is collected by pytest's unittest support but is counted here as well: a test
commit with one is refused, never wrongly accepted. Bases imported from another module are not
read; neither are custom ``python_functions``/``python_classes`` settings. A class's decorators are
part of each of its tests, and so are (#968, #979) its whole class less its ``test*`` methods
(bases, keywords and metaclass, ``pytestmark``, attributes, flags, helper and setup methods)
and the same of each base class defined earlier in the file; every module-level statement that
is not an import, a test or a plain function, whole: so every non-test class (its body runs at
import), every decorated function (a fixture, ``autouse`` under any name), one whose defaults or
annotations call something, and the functions pytest calls itself (xunit setup and teardown,
``pytest_*`` hooks); and each import, plain function or class that any of those or the test's
own decorators read by name, in turn. So whatever in the file runs at import or for every test
changes every test (#979 r3, by construction). A class pytest does not collect keeps only its
own decorators (#925: removing a class-level skip changes them all).

``hidden_tests`` (#925): what may give pytest a test that is not read above, at module level or
in a class body, inside a block (``if``, ``try``, ``with``, a loop) or not: a ``test*`` function
or ``Test*`` class in a block or nested in a class, and a ``test*``/``Test*`` name bound another
way (assignment, walrus, loop, ``with`` or ``match`` target, ``from x import test_y``, a
``TestCase`` imported relatively or from a module whose top-level name is a file stem or a
directory anywhere in the test commit's tree, see ``local_names``), unless the value is a
literal, a star import, and a function or class that declares a ``test*`` name ``global``.
new_tests refuses a test commit that changes one. Names set at run time (``setattr``,
``globals()``) are not seen.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from collections.abc import Container
from pathlib import Path

from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git_bytes

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_DEFS, ast.ClassDef)  # a ``global`` in either binds at module level
_NOT_COLLECTED = frozenset({"__init__", "__new__"})
_ASSIGNS = (ast.Assign, ast.AnnAssign, ast.AugAssign)
_ACTS = (ast.Call, ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)  # may act when evaluated
_XUNIT = frozenset({"setup_module", "teardown_module", "setup_function", "teardown_function",
                    "setUpModule", "tearDownModule", "setup", "teardown"})  # fmt: skip


def test_functions(tree: ast.Module) -> tuple[dict[str, str], frozenset[str]]:
    """(node id -> ``ast.dump`` of its function, the ``Test*`` classes pytest does not collect);
    a node id is ``name`` or ``Class::name``, without the file."""
    functions: dict[str, str] = {}
    classes: dict[str, _Class] = {}
    uncollected: set[str] = set()
    reach = _Reach(tree)
    for node in tree.body:
        if isinstance(node, _DEFS) and node.name.startswith("test"):
            functions[node.name] = _digest(
                reach.module + ast.dump(node) + reach(node.decorator_list)
            )
        elif isinstance(node, ast.ClassDef):
            methods, blocked, case, marks = _members(node, classes, reach)
            classes[node.name] = (methods, blocked, case, marks)
            if node.name.startswith("Test") or case:
                tests = {n: b for n, b in methods.items() if n.startswith("test")}
                # a class pytest does not collect gets no marks; its own decorators as before
                own = "".join(ast.dump(d) for d in node.decorator_list)
                tests = {
                    n: _digest((own if blocked else reach.module + marks) + b)
                    for n, b in tests.items()
                }
                functions.update({f"{node.name}::{n}": body for n, body in tests.items()})
                if blocked:
                    uncollected.add(node.name)
    return functions, frozenset(uncollected)


_Class = tuple[dict[str, str], bool, bool, str]


def _members(node: ast.ClassDef, classes: dict[str, _Class], reach: _Reach) -> _Class:
    """A class's methods, its own first, then every base's that it does not define; and whether
    it has an ``__init__`` or ``__new__`` (its own or a base's); and whether it is a TestCase
    subclass (a base named ``*TestCase``, or an earlier class that is one); and its marks: the
    class less its ``test*`` methods, with what it reads in turn, so each base class defined
    earlier in the file too (pytest applies their marks along the MRO, #925, #968, #979)."""
    methods = {
        item.name: ast.dump(item) + reach(item.decorator_list)
        for item in node.body
        if isinstance(item, _DEFS)
    }
    blocked, case, own = bool(_NOT_COLLECTED & methods.keys()), False, set(methods)
    marks = reach([_shell(node)])  # decorators, bases, keywords, every statement but its tests
    for base in node.bases:
        last = _base_name(base)
        case = case or last.endswith("TestCase")
        if isinstance(base, ast.Name) and base.id in classes:
            inherited, base_blocked, base_case, _ = classes[base.id]  # its marks: reach follows it
            blocked, case = blocked or base_blocked, case or base_case
            for name, body in inherited.items():  # every base's: the MRO may pick a later one's
                if name not in own:  # (#1009); a change to any of them reads as a change
                    methods[name] = _digest(methods.get(name, "") + body)  # bounded per level
    return methods, blocked, case, marks


class _Reach:
    """#968: ``reach(nodes)`` is a digest of the ``ast.dump`` of ``nodes`` and of each
    module-level import, function or class that binds a name they read, in turn (a class without
    its ``test*`` methods). ``module`` is ``reach`` of every other module-level statement, whole:
    assignments, blocks and calls (``pytestmark``, ``TestX.pytestmark = ...``, ``setattr``, a
    flag), so no form of mark or condition set at module level is missed. Each statement is
    dumped once and each set of names followed once, so a file costs about its size, not its
    size times its tests (#979 r1)."""

    def __init__(self, tree: ast.Module) -> None:
        self._by_name: dict[str, list[ast.stmt]] = {}
        self._dumps: dict[int, str] = {}
        self._found: dict[frozenset[str], str] = {}
        self._read: dict[int, list[str]] = {}
        rest = []
        for stmt in tree.body:
            names = _binds(stmt)
            for name in names:
                self._by_name.setdefault(name, []).append(stmt)
            if not names or _for_every_test(stmt):
                rest.append(stmt)
        self.module = self(rest)

    def __call__(self, nodes: list) -> str:
        names = frozenset(n.id for x in nodes for n in ast.walk(x) if isinstance(n, ast.Name))
        if (found := self._found.get(names)) is None:
            self._found[names] = found = self._follow(names)
        return _digest("".join(ast.dump(n) for n in nodes) + found)

    def _follow(self, start: frozenset[str]) -> str:
        """The digests of the statements that bind ``start``, and in turn, in file order."""
        seen: dict[int, ast.stmt] = {}
        names = list(start)
        while names:
            for stmt in self._by_name.get(names.pop(), ()):
                if id(stmt) not in seen:
                    seen[id(stmt)] = stmt
                    names += self._reads(stmt)
        found = sorted(seen.items(), key=lambda item: (item[1].lineno, item[1].col_offset))
        return "".join(self._dump(key, stmt) for key, stmt in found)

    def _reads(self, stmt: ast.stmt) -> list[str]:
        """The names a statement's shell reads, walked once per statement (#979 r2)."""
        if (names := self._read.get(id(stmt))) is None:
            self._read[id(stmt)] = names = [
                n.id for n in ast.walk(_shell(stmt)) if isinstance(n, ast.Name)
            ]
        return names

    def _dump(self, key: int, stmt: ast.stmt) -> str:
        if (dump := self._dumps.get(key)) is None:
            self._dumps[key] = dump = _digest(ast.dump(_shell(stmt)))
        return dump


def _for_every_test(stmt: ast.stmt) -> bool:
    """#979 r3, by construction: a module-level class or function that is not a test and that
    can act at import or for every test without a test naming it. That is any class but a test
    class (its body runs at import), and any function but a test that has a decorator (it runs
    at import: a fixture, an ``autouse`` one under any alias), a default or annotation that
    calls something, or a name pytest calls itself (xunit setup or teardown, a ``pytest_*``
    hook). Only a plain function whose body runs when called stays out, followed by name."""
    if isinstance(stmt, ast.ClassDef):
        return not stmt.name.startswith("Test") and not any(
            _base_name(base).endswith("TestCase") for base in stmt.bases
        )
    if not isinstance(stmt, _DEFS) or stmt.name.startswith("test"):
        return False
    args = stmt.args
    evaluated = [*args.defaults, *args.kw_defaults, stmt.returns]
    evaluated += [a.annotation for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    evaluated += [a.annotation for a in (args.vararg, args.kwarg) if a]
    acts = any(isinstance(n, _ACTS) for e in evaluated if e for n in ast.walk(e))
    return (
        bool(stmt.decorator_list) or acts or stmt.name in _XUNIT or stmt.name.startswith("pytest_")
    )


def _base_name(base: ast.expr) -> str:
    return base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _binds(stmt: ast.stmt) -> set[str]:
    """The names a module-level import, function or class binds; none for any other statement,
    which counts whole (a star import is refused by hidden_tests when it changes)."""
    if isinstance(stmt, _SCOPES):
        return {stmt.name}
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in stmt.names}
    return set()


def _shell(stmt: ast.stmt) -> ast.stmt:
    """A class without its ``test*`` methods: those are tests of their own."""
    if not isinstance(stmt, ast.ClassDef):
        return stmt
    shell = copy.copy(stmt)
    shell.body = [m for m in stmt.body if not (isinstance(m, _DEFS) and m.name.startswith("test"))]
    return shell


def hidden_tests(tree: ast.Module, local: Container[str] | None = None) -> frozenset[str]:
    """#925: ``ast.dump`` of each statement that may give pytest a test test_functions does not read
    (see the module docstring), at module level or in a class body, in a block or not; not inside
    a function, whose local names pytest never collects, unless it declares a ``test*`` name
    ``global``: then the whole function or class counts, as it may bind a module-level test.
    ``local``: the names the test commit's tree may give a top-level module (``local_names``);
    ``None`` trusts no imported ``TestCase``."""
    found: set[str] = set()
    scan = [node for node in tree.body if not isinstance(node, (*_DEFS, ast.ClassDef))]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            scan += [item for item in node.body if not isinstance(item, _DEFS)]
    _hidden(ast.Module(body=scan, type_ignores=[]), found, None, local)
    for node in ast.walk(tree):
        if isinstance(node, _SCOPES) and any(_global_test(inner) for inner in ast.walk(node)):
            found.add(ast.dump(node))
    return frozenset(found)


def _global_test(node: ast.AST) -> bool:
    names = node.names if isinstance(node, ast.Global) else []
    return any(name.startswith(("test", "Test")) for name in names)


def _hidden(node: ast.AST, found: set[str], stmt: ast.AST | None, local) -> None:
    for child in ast.iter_child_nodes(node):
        here = child if isinstance(child, ast.stmt) else stmt
        if isinstance(child, _DEFS):
            if child.name.startswith("test"):
                found.add(ast.dump(child))
        elif isinstance(child, ast.ClassDef) and child.name.startswith("Test"):
            found.add(ast.dump(child))
        elif isinstance(child, _ASSIGNS) and _literal(child.value):
            continue  # a constant or a display of constants: pytest collects only callables
        else:
            if here is not None and _binds_test(child, local):
                found.add(ast.dump(here))
            _hidden(child, found, here, local)


def _binds_test(node: ast.AST, local: Container[str] | None) -> bool:
    """A name binding pytest's default rules could collect: an assignment, walrus, loop, ``with``
    or ``match`` capture target, an imported name (``from x import test_y``, ``import x as
    test_y``), or a star import, which binds whatever ``test*`` the other module holds. A
    ``TestCase`` from an absolute import whose top-level module name is not in ``local``
    (unittest's, a framework's) does not count: such a base class holds no tests."""
    if isinstance(node, ast.Name):
        names = [node.id] if isinstance(node.ctx, ast.Store) else []
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
        names = [node.name] if node.name else []
    elif isinstance(node, ast.MatchMapping):
        names = [node.rest] if node.rest else []
    elif isinstance(node, ast.ImportFrom):
        names = [alias.asname or alias.name for alias in node.names]
        top = (node.module or "").split(".", 1)[0]
        if local is not None and not node.level and top not in local:
            names = [name for name in names if name != "TestCase"]
        if "*" in names:
            return True
    elif isinstance(node, ast.Import):  # a plain import binds a module, which pytest never collects
        names = [alias.asname for alias in node.names if alias.asname]
    else:
        names = []
    return any(name.startswith(("test", "Test")) for name in names)


def _literal(value: ast.expr | None) -> bool:
    if value is None:  # an annotation only (``test_x: int``) binds nothing
        return True
    try:
        ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def local_names(repo: Path, commit: str) -> frozenset[str]:
    """#925: every name the test commit's tree may give a top-level module: the stem of each
    ``.py`` file and the name of each directory, at any depth (prepend mode or a ``pythonpath``
    entry may put any directory first on ``sys.path``). One ``ls-tree -r`` of the whole tree; an
    imported ``TestCase`` whose top-level module has such a name is not trusted."""
    try:
        raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    names: set[str] = set()
    for path in raw.decode("utf-8", "surrogateescape").split("\0"):
        *dirs, name = path.split("/")
        names.update([*dirs, name[:-3]] if name.endswith(".py") else dirs)
    return frozenset(names)
