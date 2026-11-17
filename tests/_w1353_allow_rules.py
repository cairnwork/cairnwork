"""AST finding pass for witness syntax."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

from tests._w1353_allow import (
    BLOCK_NESTED_IMPORTS,
    BLOCKED_NODES,
    FORBIDDEN,
    ORACLES,
    _arg_ok,
    _call_is_oracle,
)
from tests._w1353_allow_alias import function_for as _function_for
from tests._w1353_allow_imports import advanced_findings, import_findings, sink_findings

# ast.dump of a function differs between CPython minor versions, so the row() pin is
# keyed by the running interpreter. An interpreter with no pin gets "" and fails closed.
_SINK_ROW_PINS = {
    (3, 11): "efd8dce450611350f94804065f7dd0e6ed16e373b65ed3e132991ed43a0132e9",
    (3, 12): "7c84f013210405e6239f3b59be5e1163eaa224248bf5b00bc137dbf0b16d096a",
    (3, 13): "5c6f12ad59a79790b4b72a2e9f0a5e3778f582f9fc8f08b46729e6f0dbd5b13b",
}
SINK_PINS = {"_w1353_probe.row": _SINK_ROW_PINS.get(sys.version_info[:2], "")}

# Top-level package names used by today's witness files. Reflective and loader
# imports are deliberately absent; selected harness helpers are listed below.
ALLOWED_IMPORTS = {
    "_w1353_allow_pins": {"__future__", "hashlib", "pathlib", "typing"},
    "_w1353_probe": {
        "__future__",
        "json",
        "re",
        "subprocess",
        "functools",
        "pathlib",
        "tempfile",
        "cairnwork_sandbox",
        "_w1353_probe",
        "_w1353_extra",
    },
    "_w1353_extra": {
        "__future__",
        "json",
        "dataclasses",
        "functools",
        "pathlib",
        "tempfile",
        "_w1353_probe",
        "cairnwork",
    },
    "_w1353_receipt": {
        "__future__",
        "base64",
        "json",
        "_w1353_probe",
        "cairnwork_attest",
        "cairnwork",
        "cairnwork_protocol",
        "tests",
    },
    "_w1353_c10": {"__future__", "json", "_w1353_probe", "cairnwork", "pathlib", "tempfile"},
}
ADAPTER_IMPORTS = {
    ("_w1353_probe", "sandbox_available"): {"sys"},
    ("_w1353_probe", "row"): {"sys"},
    ("_w1353_probe", "verify"): {"sys"},
    ("_w1353_probe", "_cairnwork_src"): {"importlib"},
    ("_w1353_extra", "extra_rows"): {"importlib"},
    ("_w1353_extra", "_c7"): {"types"},
    ("_w1353_receipt", "_c8_policy_cases"): {"sys"},
    ("_w1353_receipt", "_c8_mutations"): {"sys"},
    ("_w1353_receipt", "c8_rows"): {"sys"},
    ("_w1353_c10", "c10_rows"): {"runpy", "sys", "types"},
}
REFLECTIVE_IMPORTS = {
    "importlib",
    "runpy",
    "inspect",
    "sys",
    "builtins",
    "types",
    "gc",
    "ctypes",
    "imp",
    "pkgutil",
    "code",
    "codeop",
    "marshal",
    "pickle",
}
BLOCK_ROW_ATTRIBUTES = True


def _shape(node, tree, path, oracle_names, row_names, modules, parents, findings):
    if BLOCK_NESTED_IMPORTS and isinstance(node, (ast.Import, ast.ImportFrom)):
        if not isinstance(parents.get(node), ast.Module):
            findings.add(node.lineno)
    if type(node) in BLOCKED_NODES:
        findings.add(node.lineno)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        findings.add(node.lineno)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
        node.name == "row" or node.name in oracle_names
    ):
        if isinstance(node, ast.ClassDef):
            findings.add(node.lineno)
        nested = _function_for(node, tree) is not None
        own_row = Path(path).stem == "_w1353_probe" and node.name == "row" and node in tree.body
        if nested and not own_row:
            findings.add(node.lineno)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "row" and alias.asname is not None:
                    findings.add(node.lineno)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
        if (
            value == "row"
            or value in {name for _, name in ORACLES}
            or "_w1353_probe" in value
            or "w1353" in value
        ):
            findings.add(node.lineno)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        watched = row_names | oracle_names
        if node.id in watched:
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                findings.add(node.lineno)
    if isinstance(node, ast.Attribute):
        if BLOCK_ROW_ATTRIBUTES and node.attr == "row":
            findings.add(node.lineno)
        if node.attr.startswith("__") and node.attr.endswith("__"):
            parent = parents.get(node)
            allowed_main = (
                node.attr == "__name__"
                and isinstance(parent, ast.Compare)
                and isinstance(parent.left, ast.Attribute)
                and parent.left is node
                and any(
                    isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in parent.comparators
                )
            )
            if not allowed_main:
                findings.add(node.lineno)
        if node.attr in {name for _, name in ORACLES}:
            if (
                not isinstance(node.value, ast.Name)
                or (modules.get(node.value.id), node.attr) not in ORACLES
            ):
                findings.add(node.lineno)
    if isinstance(node, ast.Dict) and any(not isinstance(k, ast.Constant) for k in node.keys):
        findings.add(node.lineno)
    if isinstance(node, ast.Dict) and any(
        isinstance(item, ast.Constant) and item.value == "observed"
        for item in (*node.keys, *node.values)
    ):
        fn = _function_for(node, tree)
        if not (Path(path).stem == "_w1353_probe" and fn is not None and fn.name == "row"):
            findings.add(node.lineno)
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
        if not isinstance(node.slice, ast.Constant):
            findings.add(node.lineno)


def _calls(
    node,
    tree,
    bindings,
    oracle_names,
    modules,
    row_names,
    trusted_rows,
    adapters,
    raw_by_fn,
    findings,
):
    if not isinstance(node, ast.Call):
        return
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "row" and fn.id not in row_names:
        findings.add(node.lineno)
    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    if name in FORBIDDEN or (isinstance(fn, ast.Attribute) and name == "__setattr__"):
        findings.add(node.lineno)
    if isinstance(fn, ast.Attribute) and name in {
        "update",
        "setdefault",
        "clear",
        "pop",
        "__setitem__",
    }:
        if isinstance(fn.value, ast.Name) and fn.value.id in {"dict", "object"}:
            findings.add(node.lineno)
    observed_data = any(
        isinstance(item, ast.Constant)
        and isinstance(item.value, str)
        and item.value in {"observed", "obs", "erved"}
        for arg in node.args + [kw.value for kw in node.keywords]
        for item in ast.walk(arg)
    )
    if (name in {"dict", "zip", "fromkeys"} and observed_data) or (
        isinstance(fn, ast.Attribute) and name == "__setitem__" and observed_data
    ):
        findings.add(node.lineno)
    if (
        isinstance(fn, ast.Subscript)
        and isinstance(fn.slice, ast.Constant)
        and fn.slice.value == "row"
    ):
        findings.add(node.lineno)
    if isinstance(fn, ast.Attribute) and name == "row" and isinstance(fn.value, ast.Name):
        if fn.value.id in modules:
            findings.add(node.lineno)
    if not isinstance(fn, ast.Name) or fn.id not in row_names:
        return
    raw_names = raw_by_fn.get(_function_for(node, tree) or tree, set())
    bad_shape = (
        fn.id not in trusted_rows
        or bool(node.keywords)
        or any(isinstance(arg, ast.Starred) for arg in node.args)
        or len(node.args) not in {6, 7}
    )
    bad_args = any(
        not _arg_ok(arg, i, raw_names, oracle_names, row_names, modules, bindings, adapters)
        for i, arg in enumerate(node.args)
    )
    if bad_shape or bad_args:
        findings.add(node.lineno)


def _observed_binding(
    node,
    tree,
    path,
    parents,
    bindings,
    oracle_names,
    modules,
    raw_by_fn,
    adapters,
    findings,
):
    if isinstance(node, ast.alias) and (node.asname or node.name) == "observed":
        parent = parents.get(node)
        findings.add(parent.lineno if parent is not None else 1)
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == "observed"
    ):
        findings.add(node.lineno)
    if isinstance(node, ast.ExceptHandler) and node.name == "observed":
        findings.add(node.lineno)
    if isinstance(node, ast.MatchAs) and node.name == "observed":
        findings.add(node.lineno)
    if isinstance(node, ast.MatchStar) and node.name == "observed":
        findings.add(node.lineno)
    if isinstance(node, ast.MatchMapping) and node.rest == "observed":
        findings.add(node.lineno)
    if (
        isinstance(node, ast.Name)
        and node.id == "observed"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ) or (isinstance(node, ast.arg) and node.arg == "observed"):
        fn = _function_for(node, tree)
        in_row = Path(path).stem == "_w1353_probe" and fn is not None and fn.name == "row"
        if not in_row:
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)
            ):
                parent = parents.get(parent)
            value = (
                parent.value
                if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                else None
            )
            valid = value is not None and (
                _call_is_oracle(value, oracle_names, modules, bindings, adapters)
                or (isinstance(value, ast.Name) and value.id in raw_by_fn.get(fn, set()))
            )
            if not valid:
                findings.add(node.lineno)


def _bindings(
    node,
    tree,
    path,
    parents,
    bindings,
    oracle_names,
    modules,
    row_names,
    raw_by_fn,
    adapters,
    findings,
):
    _observed_binding(
        node, tree, path, parents, bindings, oracle_names, modules, raw_by_fn, adapters, findings
    )
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        value: Any = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        unpacked = any(isinstance(target, (ast.Tuple, ast.List)) for target in targets)
        if unpacked:
            local_parents = {
                child: parent
                for parent in ast.walk(value)
                for child in ast.iter_child_nodes(parent)
            }
            for item in ast.walk(value):
                if not isinstance(item, ast.Name) or item.id not in row_names | oracle_names:
                    continue
                parent = local_parents.get(item)
                if not (isinstance(parent, ast.Call) and parent.func is item):
                    findings.add(node.lineno)
    if isinstance(node, ast.Raise) and _function_for(node, tree) is not None:
        findings.add(node.lineno)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    ):
        raw = raw_by_fn.get(_function_for(node, tree) or tree, set())
        if node.func.value.id in raw and node.func.attr in {
            "update",
            "setdefault",
            "clear",
            "pop",
            "__setitem__",
        }:
            findings.add(node.lineno)
    if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
        node.ctx, (ast.Store, ast.Del)
    ):
        if isinstance(node, ast.Attribute):
            findings.add(node.lineno)
        raw = raw_by_fn.get(_function_for(node, tree) or tree, set())
        base = node.value
        while isinstance(base, (ast.NamedExpr, ast.Attribute, ast.Subscript)):
            base = base.value
        if (
            isinstance(base, ast.Name)
            and base.id in row_names | oracle_names | modules.keys() | raw
        ):
            findings.add(node.lineno)
    if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
        raw = raw_by_fn.get(_function_for(node, tree) or tree, set())
        if isinstance(node.target, ast.Name) and node.target.id in raw:
            findings.add(node.lineno)


def scan(tree, path, bindings, oracle_names, modules, row_names, trusted_rows, adapters, raw_by_fn):
    findings: set[int] = set()
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    tree._parents = parents
    findings.update(
        import_findings(tree, path, ALLOWED_IMPORTS, ADAPTER_IMPORTS, REFLECTIVE_IMPORTS)
    )
    findings.update(advanced_findings(tree, path, set(bindings)))
    findings.update(sink_findings(tree, path, SINK_PINS))
    for node in ast.walk(tree):
        _shape(node, tree, path, oracle_names, row_names, modules, parents, findings)
        _calls(
            node,
            tree,
            bindings,
            oracle_names,
            modules,
            row_names,
            trusted_rows,
            adapters,
            raw_by_fn,
            findings,
        )
        _bindings(
            node,
            tree,
            path,
            parents,
            bindings,
            oracle_names,
            modules,
            row_names,
            raw_by_fn,
            adapters,
            findings,
        )
    return sorted(findings)
