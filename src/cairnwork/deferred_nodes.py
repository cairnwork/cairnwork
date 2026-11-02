"""The test functions pytest collects from one test file, read from its AST (#660).

deferred_tests.new_tests and p2p_nodes.changed_tests compare these between the base and the test
commit. Read: each top-level ``test*`` function, and each ``test*`` method of a top-level
``Test*`` class, its own or one it inherits from a class defined earlier in the same file and named
among its bases by a plain name (the first base that has it wins). pytest collects an inherited
test under the subclass's own node id too, so a change to a parent's test is a change to each
such copy, and a target naming one copy does not run the others.

``uncollected``: the ``Test*`` classes with an ``__init__`` or ``__new__`` of their own or
inherited the same way. pytest does not collect such a class (it warns and runs none of its
tests), so a target naming the file still runs none of them. A ``unittest.TestCase`` subclass with
its own ``__init__`` is collected by pytest's unittest support but is counted here as well: a test
commit with one is refused, never wrongly accepted. Bases imported from another module are not
read; neither are nested classes or custom ``python_functions``/``python_classes`` settings.
"""

from __future__ import annotations

import ast

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NOT_COLLECTED = frozenset({"__init__", "__new__"})


def test_functions(tree: ast.Module) -> tuple[dict[str, str], frozenset[str]]:
    """(node id -> ``ast.dump`` of its function, the ``Test*`` classes pytest does not collect);
    a node id is ``name`` or ``Class::name``, without the file."""
    functions: dict[str, str] = {}
    classes: dict[str, tuple[dict[str, str], bool]] = {}
    uncollected: set[str] = set()
    for node in tree.body:
        if isinstance(node, _DEFS) and node.name.startswith("test"):
            functions[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            methods, blocked = _members(node, classes)
            classes[node.name] = (methods, blocked)
            if node.name.startswith("Test"):
                tests = {n: body for n, body in methods.items() if n.startswith("test")}
                functions.update({f"{node.name}::{n}": body for n, body in tests.items()})
                if blocked:
                    uncollected.add(node.name)
    return functions, frozenset(uncollected)


def _members(
    node: ast.ClassDef, classes: dict[str, tuple[dict[str, str], bool]]
) -> tuple[dict[str, str], bool]:
    """A class's methods, its own first, then each base's that it does not define; and whether
    it has an ``__init__`` or ``__new__`` (its own or a base's)."""
    methods = {item.name: ast.dump(item) for item in node.body if isinstance(item, _DEFS)}
    blocked = bool(_NOT_COLLECTED & methods.keys())
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in classes:
            inherited, base_blocked = classes[base.id]
            blocked = blocked or base_blocked
            for name, body in inherited.items():
                methods.setdefault(name, body)
    return methods, blocked
