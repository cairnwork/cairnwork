"""Import allow-list checks for witness sources."""

from __future__ import annotations

import ast
import builtins
import hashlib
from pathlib import Path

# Builtins observed in the real witness sources. Reflective names (including
# getattr/hasattr/vars and dynamic execution helpers) are intentionally absent.
SAFE_BUILTINS = {
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "isinstance",
    "range",
    "str",
    "zip",
}

# Adapter-only builtins, constrained by exact AST call shapes. These calls are
# retained in the pinned row sink to preserve its measured output behavior.
BUILTIN_ADAPTER_CALLS = {
    ("_w1353_probe", "row"): {
        "hasattr": {("raw", "status"), ("raw", "code"), ("raw", "reason"), ("raw", "detail")}
    }
}

# Exact module attributes available to each adapter. Dotted strings represent
# the complete chain after the imported module name.
ADAPTER_ATTRIBUTES = {
    ("_w1353_probe", "sandbox_available", "sys"): {"platform"},
    ("_w1353_probe", "row", "sys"): {"platform"},
    ("_w1353_probe", "verify", "sys"): {"executable"},
    ("_w1353_probe", "_cairnwork_src", "importlib"): {"util.find_spec"},
    ("_w1353_extra", "extra_rows", "importlib"): {
        "util.spec_from_file_location",
        "util.module_from_spec",
    },
    ("_w1353_extra", "_c7", "types"): {"SimpleNamespace"},
    ("_w1353_receipt", "_c8_policy_cases", "sys"): {"platform"},
    ("_w1353_receipt", "_c8_mutations", "sys"): {"platform"},
    ("_w1353_receipt", "c8_rows", "sys"): {"platform"},
    ("_w1353_receipt", "_c8_v01", "sys"): {"platform"},
    ("_w1353_c10", "c10_rows", "runpy"): {"run_path"},
    ("_w1353_c10", "c10_rows", "sys"): {"modules", "modules.update", "modules.pop"},
    ("_w1353_c10", "c10_rows", "types"): {"ModuleType"},
}

# run_path returns a namespace mapping. It may only be indexed by these keys.
ADAPTER_RESULT_KEYS = {("_w1353_c10", "c10_rows", "runpy", "run_path"): {"_call", "add"}}


def _parents(tree):
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _module_aliases(tree):
    aliases = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name.split(".")[0]
    return aliases


def _function_for(node, tree):
    found = [
        fn
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in ast.walk(fn)
    ]
    return min(found, key=lambda fn: sum(1 for _ in ast.walk(fn))) if found else None


def _attribute_chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return (node.id, ".".join(reversed(parts))) if isinstance(node, ast.Name) else (None, "")


def _builtin_findings(tree, path, parents, bound_names):
    findings = set()
    stem = Path(path).stem
    builtin_names = set(dir(builtins))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id not in builtin_names or node.id in bound_names:
            continue
        if node.id in SAFE_BUILTINS:
            continue
        fn = _function_for(node, tree)
        allowed = BUILTIN_ADAPTER_CALLS.get((stem, fn.name if fn else ""), {}).get(node.id, set())
        parent = parents.get(node)
        if (
            node.id == "hasattr"
            and isinstance(parent, ast.Call)
            and parent.func is node
            and len(parent.args) == 2
            and not parent.keywords
            and isinstance(parent.args[0], ast.Name)
            and isinstance(parent.args[1], ast.Constant)
            and (parent.args[0].id, parent.args[1].value) in allowed
        ):
            continue
        findings.add(node.lineno)
    return findings


def _adapter_module_findings(tree, path, parents, aliases):
    findings = set()
    stem = Path(path).stem
    for node in ast.walk(tree):
        fn = _function_for(node, tree)
        if fn is None:
            continue
        if isinstance(node, ast.Attribute):
            if isinstance(parents.get(node), ast.Attribute):
                continue
            root, chain = _attribute_chain(node)
            module = aliases.get(root)
            if module not in {"sys", "runpy", "types", "importlib"}:
                continue
            table = ADAPTER_ATTRIBUTES.get((stem, fn.name, module), set())
            if chain not in table:
                findings.add(node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            module = aliases.get(node.id)
            if module not in {"sys", "runpy", "types", "importlib"}:
                continue
            parent = parents.get(node)
            if not isinstance(parent, ast.Attribute) or parent.value is not node:
                findings.add(node.lineno)
    return findings


def _result_findings(tree, path, parents, aliases):
    findings = set()
    stem = Path(path).stem
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        root, chain = _attribute_chain(node.func)
        module = aliases.get(root)
        fn = _function_for(node, tree)
        if fn is None or not module:
            continue
        keys = ADAPTER_RESULT_KEYS.get((stem, fn.name, module, chain))
        if keys is None:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Subscript) and parent.value is node:
            if not (isinstance(parent.slice, ast.Constant) and parent.slice.value in keys):
                findings.add(parent.lineno)
            continue
        if not isinstance(parent, ast.Assign) or parent.value is not node:
            findings.add(node.lineno)
            continue
        targets = parent.targets
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            findings.add(node.lineno)
            continue
        name = targets[0].id
        loads = [
            item
            for item in ast.walk(fn)
            if isinstance(item, ast.Name) and item.id == name and isinstance(item.ctx, ast.Load)
        ]
        if not loads:
            findings.add(node.lineno)
        for item in loads:
            use = parents.get(item)
            if not isinstance(use, ast.Subscript) or use.value is not item:
                findings.add(item.lineno)
            elif not (isinstance(use.slice, ast.Constant) and use.slice.value in keys):
                findings.add(use.lineno)
    return findings


def advanced_findings(tree, path, bound_names):
    parents = _parents(tree)
    aliases = _module_aliases(tree)
    findings = (
        _builtin_findings(tree, path, parents, bound_names)
        | _adapter_module_findings(tree, path, parents, aliases)
        | _result_findings(tree, path, parents, aliases)
    )
    findings.update(_sys_modules_findings(tree, path, parents, aliases))
    return findings


def _sys_modules_findings(tree, path, parents, aliases):
    """Constrain sys.modules mutation to literal operations and the real keys."""
    if Path(path).stem != "_w1353_c10":
        return set()
    findings = set()
    keys = {"_cairnwork_blackbox_runner", "pytest"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        root, chain = _attribute_chain(node.func)
        if aliases.get(root) != "sys" or chain not in {"modules.update", "modules.pop"}:
            continue
        if chain == "modules.pop":
            key = node.args[0] if node.args else None
            valid_key = isinstance(key, ast.Constant) and key.value in keys
            valid_key = valid_key or isinstance(key, ast.Name) and key.id == "MODULE"
            if not valid_key:
                findings.add(node.lineno)
        elif (
            len(node.args) != 1
            or not isinstance(node.args[0], ast.Dict)
            or node.args[0].keys
            and any(
                not isinstance(key, ast.Constant) or key.value not in keys
                for key in node.args[0].keys
            )
            or node.keywords
        ):
            findings.add(node.lineno)
    return findings


def sink_findings(tree, path, pins):
    if Path(path).stem != "_w1353_probe":
        return set()
    findings = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "row":
            expected = pins.get("_w1353_probe.row")
            actual = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            if expected is not None and actual != expected:
                findings.add(node.lineno)
    return findings


def target_names(node: ast.AST) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del))
    }


def literal_string(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and literal_string(node.left)
        and literal_string(node.right)
    )


def import_findings(tree, path, allowed, adapter_imports, reflective):
    findings = set()
    stem = Path(path).stem
    allowed_imports = allowed.get(stem, set().union(*allowed.values()) - reflective)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name.split(".")[0] for alias in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            for module in modules:
                if isinstance(node, ast.ImportFrom) and module in reflective:
                    findings.add(node.lineno)
                if module not in allowed_imports and module not in reflective:
                    findings.add(node.lineno)
                if module in reflective and module not in allowed_imports:
                    if not any(
                        file_stem == stem and module in names
                        for (file_stem, _), names in adapter_imports.items()
                    ):
                        findings.add(node.lineno)
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        module = node.id if node.id in reflective else None
        for item in tree.body:
            if isinstance(item, ast.ImportFrom) and item.module:
                if any((alias.asname or alias.name) == node.id for alias in item.names):
                    top = item.module.split(".")[0]
                    if top in reflective:
                        module = top
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    local = alias.asname or alias.name.split(".")[0]
                    if local == node.id and alias.name.split(".")[0] in reflective:
                        module = alias.name.split(".")[0]
        if module is not None and not any(
            file_stem == stem
            and module in names
            and (fn := _function_for(node, tree)) is not None
            and fn.name == func
            for (file_stem, func), names in adapter_imports.items()
        ):
            findings.add(node.lineno)
    return findings
