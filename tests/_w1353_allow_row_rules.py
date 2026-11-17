"""Static checks for issued rows and witness cache ownership."""

from __future__ import annotations

import ast
from pathlib import Path

ROW_KEYS = {"observed", "raw_return", "classification", "matches_spec"}


def _function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _registry_findings(tree, stem, parents):
    issues = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tests._w1353_allow_rowreg":
            allowed = (
                stem == "_w1353_probe"
                and len(node.names) == 1
                and node.names[0].name == "issue"
                and node.names[0].asname in {None, "issue"}
            )
            if not allowed:
                issues.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (
            (node.module or "") == "tests"
            or (node.module or "").startswith("tests._w1353")
            or any("issue" in (alias.name, alias.asname) for alias in node.names)
        ):
            issues.add(node.lineno)
        elif isinstance(node, ast.Import) and any(
            alias.name == "tests" or alias.name.startswith("tests._w1353") for alias in node.names
        ):
            issues.add(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr in {
            "_w1353_allow_rowreg",
            "issue",
            "freeze",
            "thaw",
            "verify_rows",
        }:
            issues.add(node.lineno)
        if isinstance(node, ast.Name) and node.id in {"issue", "freeze", "thaw", "verify_rows"}:
            if isinstance(node.ctx, ast.Load):
                parent = parents.get(node)
                fn = _function(node, parents)
                allowed_call = (
                    stem == "_w1353_probe"
                    and node.id == "issue"
                    and isinstance(parent, ast.Call)
                    and parent.func is node
                    and isinstance(parents.get(parent), ast.Return)
                    and fn is not None
                    and fn.name == "row"
                )
                if not allowed_call:
                    issues.add(node.lineno)
    return issues


def _cache_target(node):
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_CACHE"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    )


def row_rule_findings(tree, path):
    stem = Path(path).stem
    if not stem.startswith("_w1353_") or stem.startswith("_w1353_allow"):
        return []
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    findings = _registry_findings(tree, stem, parents)
    for node in ast.walk(tree):
        fn = _function(node, parents)
        if isinstance(node, ast.Dict):
            keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
            if keys & ROW_KEYS and not (stem == "_w1353_probe" and fn and fn.name == "row"):
                findings.add(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
        ):
            bad_keys = any(keyword.arg in ROW_KEYS for keyword in node.keywords)
            bad_positional = any(isinstance(arg, (ast.Name, ast.Call)) for arg in node.args)
            empty_dict_compare = (
                not node.args and not node.keywords and isinstance(parents.get(node), ast.Compare)
            )
            if bad_keys or bad_positional or empty_dict_compare:
                findings.add(node.lineno)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            if isinstance(node, (ast.Assign, ast.Delete)):
                targets = node.targets
            else:
                targets = [node.target]
            for target in targets:
                for item in ast.walk(target):
                    if (
                        isinstance(item, ast.Name)
                        and item.id == "_CACHE"
                        and isinstance(item.ctx, (ast.Store, ast.Del))
                    ):
                        cache_init = (
                            isinstance(node, ast.Assign)
                            and len(node.targets) == 1
                            and isinstance(target, ast.Name)
                            and target.id == "_CACHE"
                            and isinstance(node.value, ast.Dict)
                            and not node.value.keys
                            and node in tree.body
                        )
                        if not _cache_target(target) and not cache_init:
                            findings.add(node.lineno)
                    if (
                        isinstance(item, ast.Subscript)
                        and _cache_target(item)
                        and isinstance(item.ctx, (ast.Store, ast.Del))
                    ):
                        key = getattr(item.slice, "value", None)
                        allowed = (
                            isinstance(node, ast.Assign)
                            and len(node.targets) == 1
                            and target is item
                            and isinstance(node.value, ast.Name)
                            and fn is not None
                            and (fn.name, key)
                            in {("core_rows", "core_rows"), ("extra_rows", "extra_rows")}
                        )
                        if not allowed:
                            findings.add(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_CACHE"
                and node.func.attr in {"update", "setdefault", "pop", "clear"}
            ):
                findings.add(node.lineno)
    return sorted(findings)
