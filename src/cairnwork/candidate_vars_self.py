"""Narrow static proof for the honest ``vars(self)`` method idiom (#1155)."""

from __future__ import annotations

import ast


def safe_vars_self_statement(tree: ast.Module, statement: ast.AST) -> bool:
    """Return true only if all vars uses in this statement are safe direct method calls."""
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    calls = [
        node for node in ast.walk(statement) if isinstance(node, ast.Name) and node.id == "vars"
    ]
    if not calls or _name_rebound(tree, {"vars"}):
        return False
    for name in calls:
        parent = parents.get(name)
        if not isinstance(parent, ast.Call) or parent.func is not name:
            return False
        if len(parent.args) != 1 or parent.keywords or isinstance(parent.args[0], ast.Starred):
            return False
        receiver = parent.args[0]
        if not isinstance(receiver, ast.Name):
            return False
        method = _method_for(name, receiver.id, parents)
        if method is None or _rebound_in_method(method, receiver.id):
            return False
        if _unbound_method_reference(tree, method, parents):
            return False
    return True


def _method_for(
    node: ast.AST, receiver_name: str, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Lambda):
            return None
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = current
            if not isinstance(parents.get(method), ast.ClassDef):
                return None
            if len(method.decorator_list) > 1:
                return None
            if method.decorator_list and not (
                isinstance(method.decorator_list[0], ast.Name)
                and method.decorator_list[0].id == "classmethod"
            ):
                return None
            args = method.args.args
            if (
                method.args.posonlyargs
                or not args
                or args[0].arg != receiver_name
                or args[0].annotation is not None
            ):
                return None
            if len(method.args.defaults) == len(args):
                return None
            if _has_forbidden_name(
                [
                    *method.args.defaults,
                    *[v for v in method.args.kw_defaults if v is not None],
                    *[
                        a.annotation
                        for a in [
                            *method.args.posonlyargs,
                            *method.args.args,
                            *method.args.kwonlyargs,
                            *([method.args.vararg] if method.args.vararg else []),
                            *([method.args.kwarg] if method.args.kwarg else []),
                        ]
                        if a.annotation is not None
                    ],
                    *method.decorator_list,
                    *([method.returns] if method.returns is not None else []),
                ],
                {"vars", receiver_name},
            ):
                return None
            if _name_rebound(_module_for(method, parents), {"classmethod", "staticmethod"}):
                return None
            if not any(node is candidate for body in method.body for candidate in ast.walk(body)):
                return None
            return method
    return None


def _module_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.Module:
    while node in parents:
        node = parents[node]
    assert isinstance(node, ast.Module)
    return node


def _has_forbidden_name(nodes: list[ast.AST], names: set[str]) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id in names for root in nodes for node in ast.walk(root)
    )


def _name_rebound(tree: ast.Module, names: set[str]) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in names
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            return True
        if isinstance(node, ast.arg) and node.arg in names:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and names.intersection(node.names):
            return True
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            if getattr(node, "name", None) in names or getattr(node, "rest", None) in names:
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(
                names.intersection({alias.name, alias.name.split(".")[0], alias.asname})
                for alias in node.names
            ):
                return True
    return False


def _rebound_in_method(method: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    if method.args.posonlyargs or not method.args.args:
        return True
    receiver = method.args.args[0]
    if receiver.arg != name:
        return True
    return _name_rebound_in_node(method, name, receiver)


def _name_rebound_in_node(node: ast.AST, name: str, receiver: ast.arg) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Name)
            and child.id == name
            and isinstance(child.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(child, ast.arg) and child.arg == name and child is not receiver:
            return True
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child is not node
        ):
            if child.name == name:
                return True
        if isinstance(child, (ast.Global, ast.Nonlocal)) and name in child.names:
            return True
        if isinstance(child, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            if getattr(child, "name", None) == name or getattr(child, "rest", None) == name:
                return True
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            if any(
                name in {alias.name, alias.name.split(".")[0], alias.asname}
                for alias in child.names
            ):
                return True
    return False


def _unbound_method_reference(
    tree: ast.Module,
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    owner = parents.get(method)
    if not isinstance(owner, ast.ClassDef):
        return True
    class_name = owner.name
    if owner.decorator_list or any(
        isinstance(node, ast.Constant) and node.value == class_name for node in ast.walk(tree)
    ):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == class_name:
            return True
        in_method_body = _inside(node, method.body, parents)
        if in_method_body:
            continue
        if isinstance(node, ast.Name) and node.id == method.name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == method.name:
            if not (
                isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
                and isinstance(call := parents.get(node), ast.Call)
                and call.func is node
                and not _inside_classmethod(node, parents)
            ):
                return True
        if isinstance(node, ast.Constant) and node.value == method.name:
            return True
        if isinstance(node, ast.arg) and node.arg == method.name:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node is not method and node.name == method.name:
                return True
        if isinstance(node, ast.ClassDef) and node.name == method.name:
            return True
        if isinstance(node, ast.keyword) and node.arg == method.name:
            return True
        if isinstance(node, ast.alias) and (node.name == method.name or node.asname == method.name):
            return True
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if getattr(node, "name", None) == method.name:
                return True
        if isinstance(node, ast.MatchMapping) and node.rest == method.name:
            return True
    return False


def _inside_classmethod(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return (
                len(node.decorator_list) == 1
                and isinstance(node.decorator_list[0], ast.Name)
                and node.decorator_list[0].id == "classmethod"
            )
    return False


def _inside(node: ast.AST, ancestors: list[ast.stmt], parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if node in ancestors:
            return True
    return False
