"""Subprocess-side AST classification for P2P collected test ids."""

from __future__ import annotations

import ast
import json
import re
import sys

from cairnwork.p2p_bindings import (
    binds_in_scope,
    hidden_bindings,
    is_pytest_decorator,
    last_binding,
    last_module_def,
    mentions_ast,
    rebinds_by_string,
    walk_without_nested_classes,
)


def classify_payload(data: dict) -> dict:
    base_ids, test_ids, edited = map(set, (data["base_ids"], data["test_ids"], data["edited"]))
    texts, bases, test_trees, removed, dropped = {}, {}, {}, set(), set()
    collected_funcs = {_function(n) for n in test_ids}
    for nodeid in base_ids - test_ids:
        path = _file(nodeid)
        gone = False
        if path in edited and _function(nodeid) not in collected_funcs:
            if path not in texts:
                texts[path] = data["sources"]["test"][path]
                bases[path] = _tree(data["sources"]["base"][path])
                test_trees[path] = _tree(texts[path])
            *owners, name = _function(nodeid).split("::")[1:]
            gone = (
                _defines(bases[path], owners, name)
                and test_trees[path] is not None
                and not _scope_mentions(test_trees[path], owners, name, texts[path])
                and not _hidden(test_trees[path], bases[path], name, path, test_ids, base_ids)
            )
        (removed if gone else dropped).add(nodeid)
    return {"removed": sorted(removed), "dropped": sorted(dropped)}


def main() -> None:
    sys.stdout.write(json.dumps(classify_payload(json.load(sys.stdin)), separators=(",", ":")))


def _file(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _function(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


def _hidden(tree, base, name, path, test_ids, base_ids) -> bool:
    """#1127: whether the test commit keeps ``name`` bound where pytest does not collect it (a
    shadowed same-name class or method, a class pytest skips, a nested class, a function body).
    The base test's code is then still there, only hidden: dropped, never removed. A binding the
    base already had in the same scope, unchanged and not collected there either, does not
    count; each copy counts once, so a second copy of an excused one is hidden."""

    def got(ids):
        return {_function(n).partition("::")[2] for n in ids if _file(n) == path}

    try:
        kept = hidden_bindings(tree, name, got(test_ids))
        # #1122: the base side keeps the narrow shadowing check, so its excuse never grows (#1144)
        excused = hidden_bindings(base, name, got(base_ids), annotations=False)
        return bool(kept - excused)  # counts, not a set
    except RecursionError:  # ast.dump of a very deep expression: not provably gone
        return True


def _tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):  # 3.11 cannot build a very deep AST
        return None  # nothing is provably defined: the id is dropped, never removed


def _scope_mentions(tree: ast.Module, owners: list[str], name: str, text: str = "") -> bool:
    """Whether static source evidence can account for a collected name."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    if not owners:
        module = [n for n in tree.body if not isinstance(n, ast.ClassDef)]
        return _mentions(module, name, pattern)
    body = tree.body
    target_class: ast.ClassDef | None = None
    for owner in owners:
        found = [n for n in body if isinstance(n, ast.ClassDef) and n.name == owner]
        if not found:  # #1127: identifiers are NFKC-normalized, the raw text is not
            return bool(pattern.search(text)) or _mentions(tree.body, name, pattern)
        target_class = found[-1]
        body = target_class.body
    if target_class is None:
        return bool(pattern.search(text))
    if target_class.keywords or not all(map(is_pytest_decorator, target_class.decorator_list)):
        return True
    imported_testcase = _is_unambiguous_testcase(tree)
    if any(
        _base_may_supply(base, name, tree, set(), imported_testcase) for base in target_class.bases
    ):
        return True
    module = [n for n in tree.body if not isinstance(n, ast.ClassDef)]
    nodes = [*body, *module]
    if any(_mentions_dunder_test(node) for node in nodes):
        return True
    if _mentions(body, name, pattern, include_nested_classes=False):
        return True
    return _mentions(module, name, pattern)


def _base_may_supply(
    base: ast.expr, name: str, tree: ast.Module, seen: set[str], imported_testcase: bool
) -> bool:
    """Conservatively decide whether a class base can supply a test name."""
    if isinstance(base, ast.Name):
        if base.id == "object":
            return False
        if base.id == "TestCase":
            return not imported_testcase
        if base.id in seen:
            return True
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base.id]
        bindings = [n for n in tree.body if binds_in_scope(n, base.id, module=True)]
        if len(classes) != 1 or len(bindings) != 1 or bindings[0] is not classes[0]:
            return True
        cls = classes[0]
        if cls.keywords or any(not is_pytest_decorator(d) for d in cls.decorator_list):
            return True  # an inherited metaclass or a decorator on the base can add tests
        if _module_mutates_base(tree, base.id) or _class_binds_hook(cls):
            return True  # __init_subclass__ runs on A and can add tests the source never names
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        if _mentions(cls.body, name, pattern, include_nested_classes=False):
            return True
        return any(
            _base_may_supply(b, name, tree, seen | {base.id}, imported_testcase) for b in cls.bases
        )
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        if base.value.id == "unittest" and base.attr == "TestCase":
            return False
    return True


def _is_unambiguous_testcase(tree: ast.Module) -> bool:
    bindings = [n for n in tree.body if binds_in_scope(n, "TestCase", module=True)]
    return (
        len(bindings) == 1
        and isinstance(bindings[0], ast.ImportFrom)
        and bindings[0].module == "unittest"
        and any(a.name == "TestCase" and a.asname in {None, "TestCase"} for a in bindings[0].names)
    )


def _module_mutates_base(tree: ast.Module, name: str) -> bool:
    for stmt in tree.body:
        pending: list[ast.AST] = [stmt]
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "__init_subclass__"
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == name
            ):
                return True
            pending.extend(ast.iter_child_nodes(node))
    return False


def _class_binds_hook(cls: ast.ClassDef) -> bool:
    return any(binds_in_scope(stmt, "__init_subclass__") for stmt in cls.body)


def _mentions_dunder_test(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "__test__")
        or (isinstance(n, ast.Constant) and n.value == "__test__")
        or (isinstance(n, ast.arg) and n.arg == "__test__")
        for n in ast.walk(node)
    )


def _mentions(
    nodes: list[ast.stmt], name: str, pattern: re.Pattern, *, include_nested_classes: bool = True
) -> bool:
    """Search source-level identifiers, bindings, attributes and string constants."""
    for node in nodes:
        walk = ast.walk(node) if include_nested_classes else walk_without_nested_classes(node)
        for n in walk:
            if mentions_ast(n, name, pattern, defs=True):
                return True
    return False


def _defines(tree: ast.Module | None, owners: list[str], name: str) -> bool:
    """Whether the base file itself has ``def name`` in the class chain ``owners``."""
    if tree is None or rebinds_by_string(tree, name):  # #1127: may collect other code
        return False
    body = tree.body
    module = True
    for owner in owners:
        try:
            last = last_binding(body, owner, module=module)
        except ValueError:
            return False
        if not isinstance(last, ast.ClassDef) or last.name != owner:  # #1122: a walrus in
            return False  # another class's header rebinds the name; it is not this class
        body = last.body
        module = False
    try:
        last = last_binding(body, name, module=module)
    except ValueError:
        if not module:
            return False
        try:
            last = last_module_def(body, name)
        except RecursionError:
            return False
    # #1122: a walrus in another def's header (annotation, default, decorator) is the last binding
    return isinstance(last, (ast.FunctionDef, ast.AsyncFunctionDef)) and last.name == name


if __name__ == "__main__":
    main()
