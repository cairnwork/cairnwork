"""Small whole-file syntax language for the #1353 witness modules."""

from __future__ import annotations

import ast

ALLOWED_NODES = frozenset(
    {
        ast.Module,
        ast.Import,
        ast.ImportFrom,
        ast.alias,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Assign,
        ast.AugAssign,
        ast.Expr,
        ast.Return,
        ast.If,
        ast.For,
        ast.With,
        ast.Try,
        ast.ExceptHandler,
        ast.Assert,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.Attribute,
        ast.Subscript,
        ast.Slice,
        ast.Call,
        ast.keyword,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.UnaryOp,
        ast.Not,
        ast.USub,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.Compare,
        ast.Eq,
        ast.In,
        ast.NotIn,
        ast.IsNot,
        ast.IfExp,
        ast.JoinedStr,
        ast.FormattedValue,
        ast.Tuple,
        ast.List,
        ast.Set,
        ast.Dict,
        ast.ListComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        ast.Starred,
        ast.withitem,
    }
)
_SAFE_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.Is, ast.IsNot)
_RAW_CONVERSIONS = {"str", "int", "float", "bool", "len", "repr", "blackbox_result"}


def _constant_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_constant_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _constant_expression(key) and _constant_expression(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set))
            and _constant_expression(node.args[0])
        )
    if isinstance(node, ast.JoinedStr):
        return all(
            isinstance(item, ast.Constant)
            or isinstance(item, ast.FormattedValue)
            and _constant_expression(item.value)
            for item in node.values
        )
    if isinstance(node, ast.BinOp):
        return _constant_expression(node.left) and _constant_expression(node.right)
    return False


def _annotation_allowed(node: ast.AST) -> bool:
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _annotation_allowed(node.value)
    if isinstance(node, ast.Subscript):
        return _annotation_allowed(node.value) and _annotation_allowed(node.slice)
    return False


def _raw_expression(node: ast.AST, raw: set[str], oracle) -> bool:
    if isinstance(node, ast.Name):
        return isinstance(node.ctx, ast.Load) and node.id in raw
    if isinstance(node, ast.Attribute):
        return isinstance(node.ctx, ast.Load) and _raw_expression(node.value, raw, oracle)
    if isinstance(node, ast.Subscript):
        return isinstance(node.ctx, ast.Load) and _raw_expression(node.value, raw, oracle)
    if isinstance(node, ast.Call):
        if oracle(node):
            return True
        if isinstance(node.func, ast.Name):
            return (
                node.func.id in _RAW_CONVERSIONS
                and len(node.args) == 1
                and _raw_expression(node.args[0], raw, oracle)
            )
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            return _raw_expression(node.func.value, raw, oracle)
    return False


def _constant_compare_operand(node: ast.AST) -> bool:
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        return _constant_expression(node)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(isinstance(item, ast.Constant) for item in node.elts)
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set))
            and all(isinstance(item, ast.Constant) for item in node.args[0].elts)
        )
    return False


def _raw_context(tree: ast.Module):
    from tests._w1353_allow import (
        _binders,
        _call_is_oracle,
        _module_oracles,
        _valid_adapters,
    )
    from tests._w1353_allow_positions import _collect_raw

    binders = _binders(tree)
    oracles, modules, _, _ = _module_oracles(tree, binders, "witness.py")
    adapters = _valid_adapters(tree, "witness.py", oracles, modules, binders)

    def is_oracle(node):
        return _call_is_oracle(node, oracles, modules, binders, adapters)

    raw: set[str] = set()
    for scope in (tree, *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))):
        names, _, _ = _collect_raw(scope, is_oracle, adapters)
        raw.update(names)
    return raw, is_oracle


def syntax_findings(tree: ast.Module) -> list[int]:
    """Return lines that violate the witness file's syntax and raw-compare rules."""
    findings: set[int] = set()
    module_functions = {node for node in tree.body if isinstance(node, ast.FunctionDef)}
    has_future_annotations = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        if type(node) not in ALLOWED_NODES:
            findings.add(line)
        if isinstance(node, ast.Starred) and isinstance(node.ctx, (ast.Store, ast.Del)):
            findings.add(line)
        if isinstance(node, ast.ClassDef):
            findings.add(line)
        if isinstance(node, ast.FunctionDef):
            if node not in module_functions or node.decorator_list:
                findings.add(line)
            defaults = (*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None))
            if any(not isinstance(value, ast.Constant) for value in defaults):
                findings.add(line)
            annotations = [
                arg.annotation
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.annotation is not None
            ]
            if node.args.vararg and node.args.vararg.annotation:
                annotations.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                annotations.append(node.args.kwarg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)
            if not has_future_annotations and any(
                not _annotation_allowed(annotation) for annotation in annotations
            ):
                findings.add(line)
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Lambda, ast.AsyncFunctionDef)):
            findings.add(line)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node is tree.body[0]
        ):
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _constant_expression(node.value)
        ):
            continue
        findings.add(getattr(node, "lineno", 1))

    raw, is_oracle = _raw_context(tree)
    for node in (item for item in ast.walk(tree) if isinstance(item, ast.Compare)):
        operands = (node.left, *node.comparators)
        raw_operands = [_raw_expression(value, raw, is_oracle) for value in operands]
        if not any(raw_operands):
            continue
        if any(not isinstance(op, _SAFE_COMPARE_OPS) for op in node.ops):
            findings.add(node.lineno)
            continue
        if sum(raw_operands) > 1:
            findings.add(node.lineno)
            continue
        if any(
            not is_raw and not _constant_compare_operand(value)
            for value, is_raw in zip(operands, raw_operands, strict=True)
        ):
            findings.add(node.lineno)
    return sorted(findings)
