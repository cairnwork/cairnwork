"""Subprocess-side AST classification for P2P collected test ids."""

from __future__ import annotations

import ast
import json
import re
import sys

from cairnwork.collect_reader import _valid_exposed_id
from cairnwork.node_file import file_part, name_part
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
from cairnwork.p2p_function import _function
from cairnwork.p2p_import_time import _import_time_uses_changed
from cairnwork.p2p_simple_module import _simple_test_module, _utf8_source


def classify_payload(data: dict) -> dict:
    base_ids, test_ids, edited = map(set, (data["base_ids"], data["test_ids"], data["edited"]))
    exposed = data.get("exposed")
    changed = data.get("changed_test_files")
    if (
        not isinstance(changed, list)
        or not all(isinstance(item, str) for item in changed)
        or len(set(changed)) != len(changed)
    ):
        changed = None
    if (
        not isinstance(exposed, list)
        or not all(isinstance(item, str) and _valid_exposed_id(item) for item in exposed)
        or len(set(exposed)) != len(exposed)
    ):
        exposed = None
    texts, bases, test_trees, removed, dropped = {}, {}, {}, set(), set()
    collected_funcs = {_function(n) for n in test_ids}
    for nodeid in base_ids - test_ids:
        path = _file(nodeid)
        gone = False
        if "::" in nodeid and path in edited and _function(nodeid) not in collected_funcs:
            if path not in texts:
                texts[path] = data["sources"]["test"][path]
                bases[path] = _tree(data["sources"]["base"][path])
                test_trees[path] = _tree(texts[path])
            *owners, name = name_part(_function(nodeid)).split("::")
            test_tree = test_trees[path]
            if _defines(bases[path], owners, name):
                gone = (
                    test_tree is not None
                    and not _scope_mentions(test_tree, owners, name, texts[path])
                    and not _hidden(test_tree, bases[path], name, path, test_ids, base_ids)
                    and not _import_time_uses_changed(bases[path], test_tree)
                )
            else:
                origin = _inherited_origin(bases[path], owners, name)
                gone = (
                    origin is not None
                    and test_tree is not None
                    and not _scope_mentions(test_tree, [origin], name, texts[path])
                    and not _scope_mentions(test_tree, owners, name, texts[path])
                    and not _kept_uncollected(test_tree, name, path, test_ids)
                    and _simple_test_module(test_tree)
                    and _utf8_source(texts[path])
                    and changed == [path]
                    and f"{path}::{origin}::{name}" not in test_ids
                    and not any(
                        _file(func) == path and func.rpartition("::")[2] == name
                        for func in collected_funcs
                    )
                )
            if gone and _runtime_exposes(exposed, path, name):
                gone = False
        (removed if gone else dropped).add(nodeid)
    return {"removed": sorted(removed), "dropped": sorted(dropped)}


def main() -> None:
    sys.stdout.write(json.dumps(classify_payload(json.load(sys.stdin)), separators=(",", ":")))


def _file(nodeid: str) -> str:
    return file_part(nodeid)


def _hidden(tree, base, name, path, test_ids, base_ids) -> bool:
    """#1127: whether the test commit keeps ``name`` bound where pytest does not collect it (a
    shadowed same-name class or method, a class pytest skips, a nested class, a function body).
    The base test's code is then still there, only hidden: dropped, never removed. A binding the
    base already had in the same scope, unchanged and not collected there either, does not
    count; each copy counts once, so a second copy of an excused one is hidden."""

    def got(ids):
        return {name_part(_function(n)) for n in ids if _file(n) == path}

    try:
        kept = hidden_bindings(tree, name, got(test_ids))
        # #1122: the base side keeps the narrow shadowing check, so its excuse never grows (#1144)
        excused = hidden_bindings(base, name, got(base_ids), annotations=False)
        return bool(kept - excused)  # counts, not a set
    except RecursionError:  # ast.dump of a very deep expression: not provably gone
        return True


def _runtime_exposes(exposed, path, name) -> bool:
    """Whether collect-only found an uncollected live test with this name in the file."""
    return exposed is None or any(
        _file(item) == path and item.rpartition("::")[2] == name for item in exposed
    )


def _kept_uncollected(tree, name, path, test_ids) -> bool:
    """An inherited deletion excuses no binding left uncollected in the source file."""
    got = {name_part(_function(n)) for n in test_ids if _file(n) == path}
    try:
        return bool(hidden_bindings(tree, name, got))
    except RecursionError:
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


def _inherited_origin(tree: ast.Module | None, owners: list[str], name: str) -> str | None:
    """Find one unambiguous same-file ancestor that defines an inherited test."""
    if tree is None or len(owners) != 1 or rebinds_by_string(tree, name):
        return None
    owner = owners[0]
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner]
    try:
        binding = last_binding(tree.body, owner, module=True)
    except ValueError:
        return None
    if (
        len(classes) != 1
        or binding is not classes[0]
        or not _safe_inheritance_class(tree, classes[0], name)
    ):
        return None
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    origins: set[str] = set()
    visited: set[str] = set()

    def visit(cls: ast.ClassDef) -> bool:
        for base in cls.bases:
            if not isinstance(base, ast.Name) or base.id == "TestCase":
                return False
            if base.id == "object":
                continue
            ancestors = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base.id]
            bindings = [n for n in tree.body if binds_in_scope(n, base.id, module=True)]
            if len(ancestors) != 1 or len(bindings) != 1 or bindings[0] is not ancestors[0]:
                return False
            ancestor = ancestors[0]
            if not _safe_inheritance_class(tree, ancestor, name, allow_name=True):
                return False
            if base.id not in visited:
                visited.add(base.id)
                if _mentions(ancestor.body, name, pattern, include_nested_classes=False):
                    if not _defines(tree, [base.id], name):
                        return False
                    origins.add(base.id)
                if not visit(ancestor):
                    return False
        return True

    if not visit(classes[0]) or len(origins) != 1:
        return None
    return next(iter(origins))


def _safe_inheritance_class(tree, cls, name, *, allow_name=False) -> bool:
    return (
        not cls.keywords
        and all(map(is_pytest_decorator, cls.decorator_list))
        and not _class_binds_hook(cls)
        and not _module_mutates_base(tree, cls.name)
        and not _mentions_dunder_test(cls)
        and (
            allow_name
            or not _mentions(
                cls.body,
                name,
                re.compile(rf"\b{re.escape(name)}\b"),
                include_nested_classes=False,
            )
        )
    )


if __name__ == "__main__":
    main()
