"""The test functions pytest collects from one test file, read from its AST (#660).

deferred_tests.new_tests and p2p_nodes.changed_tests compare these between the base and the test
commit. Read: each top-level ``test*`` function, and each ``test*`` method of a top-level test
class, its own or one it inherits from a class defined earlier in the same file and named
among its bases by a plain name (the first base that has it wins). pytest collects an inherited
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
part of each of its tests (#925: removing a class-level skip changes them all).

``hidden_tests`` (#925): what may give pytest a test that is not read above, at module level or
in a class body, inside a block (``if``, ``try``, ``with``, a loop) or not: a ``test*`` function
or ``Test*`` class in a block or nested in a class, and a ``test*``/``Test*`` name bound another
way (assignment, walrus, loop or ``with`` target, ``from x import test_y``), unless the value is a
literal. new_tests refuses a test commit that changes one. Names set at run time (``setattr``,
``globals()``) are not seen.
"""

from __future__ import annotations

import ast

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NOT_COLLECTED = frozenset({"__init__", "__new__"})
_ASSIGNS = (ast.Assign, ast.AnnAssign, ast.AugAssign)


def test_functions(tree: ast.Module) -> tuple[dict[str, str], frozenset[str]]:
    """(node id -> ``ast.dump`` of its function, the ``Test*`` classes pytest does not collect);
    a node id is ``name`` or ``Class::name``, without the file."""
    functions: dict[str, str] = {}
    classes: dict[str, tuple[dict[str, str], bool, bool]] = {}
    uncollected: set[str] = set()
    for node in tree.body:
        if isinstance(node, _DEFS) and node.name.startswith("test"):
            functions[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            methods, blocked, case = _members(node, classes)
            classes[node.name] = (methods, blocked, case)
            if node.name.startswith("Test") or case:
                deco = "".join(ast.dump(d) for d in node.decorator_list)  # #925: a class skip
                tests = {n: deco + body for n, body in methods.items() if n.startswith("test")}
                functions.update({f"{node.name}::{n}": body for n, body in tests.items()})
                if blocked:
                    uncollected.add(node.name)
    return functions, frozenset(uncollected)


def _members(
    node: ast.ClassDef, classes: dict[str, tuple[dict[str, str], bool, bool]]
) -> tuple[dict[str, str], bool, bool]:
    """A class's methods, its own first, then each base's that it does not define; and whether
    it has an ``__init__`` or ``__new__`` (its own or a base's); and whether it is a TestCase
    subclass (a base named ``*TestCase``, or an earlier class that is one)."""
    methods = {item.name: ast.dump(item) for item in node.body if isinstance(item, _DEFS)}
    blocked, case = bool(_NOT_COLLECTED & methods.keys()), False
    for base in node.bases:
        last = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        case = case or last.endswith("TestCase")
        if isinstance(base, ast.Name) and base.id in classes:
            inherited, base_blocked, base_case = classes[base.id]
            blocked, case = blocked or base_blocked, case or base_case
            for name, body in inherited.items():
                methods.setdefault(name, body)
    return methods, blocked, case


def hidden_tests(tree: ast.Module) -> frozenset[str]:
    """#925: ``ast.dump`` of each statement that may give pytest a test test_functions does not read
    (see the module docstring), at module level or in a class body, in a block or not; not inside
    a function, whose local names pytest never collects."""
    found: set[str] = set()
    scan = [node for node in tree.body if not isinstance(node, (*_DEFS, ast.ClassDef))]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            scan += [item for item in node.body if not isinstance(item, _DEFS)]
    _hidden(ast.Module(body=scan, type_ignores=[]), found, None)
    return frozenset(found)


def _hidden(node: ast.AST, found: set[str], stmt: ast.AST | None) -> None:
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
            if here is not None and _binds_test(child):
                found.add(ast.dump(here))
            _hidden(child, found, here)


def _binds_test(node: ast.AST) -> bool:
    """A name binding pytest's default rules could collect: an assignment, walrus, loop or
    ``with`` target, or an imported name (``from x import test_y``, ``import x as test_y``)."""
    if isinstance(node, ast.Name):
        names = [node.id] if isinstance(node.ctx, ast.Store) else []
    elif isinstance(node, ast.ImportFrom):
        names = [alias.asname or alias.name for alias in node.names]
        names = [name for name in names if name != "TestCase"]  # the base class has no tests
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
