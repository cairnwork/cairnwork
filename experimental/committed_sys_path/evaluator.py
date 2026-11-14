"""Pure AST static analysis and POSIX/pathlib evaluation for committed_sys_path."""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath

from experimental.committed_sys_path.aliases import (
    AliasState,
    collect_aliases_and_env,
    is_sys_expr,
    is_sys_path_expr,
)
from experimental.committed_sys_path.path_edits import PathEdit, find_path_edits

__all__ = [
    "EvalPath",
    "PathEdit",
    "StaticPathEvaluator",
    "collect_aliases_and_env",
    "find_path_edits",
    "is_sys_expr",
    "is_sys_path_expr",
]

_CW_ROOT = "/_CW_ROOT_"


@dataclass(frozen=True)
class EvalPath:
    """Statically evaluated path value with __file__-anchoring flag."""

    path: str
    anchored: bool
    is_pathlib: bool = False


class StaticPathEvaluator:
    """Evaluates AST expressions resolving to __file__-anchored repository paths."""

    def __init__(self, st: AliasState, file_path: str) -> None:
        self.st = st
        self.file_path = file_path

    def evaluate(self, node: ast.AST, depth: int = 0) -> EvalPath | None:
        """Evaluate an AST expression into an EvalPath or None if dynamic."""
        if depth > 12:
            return None
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                if self.st.file_rebound:
                    return None
                clean = posixpath.normpath(self.file_path).lstrip("/")
                return EvalPath(f"{_CW_ROOT}/{clean}", anchored=True)
            if node.id in self.st.env:
                return self.evaluate(self.st.env[node.id], depth + 1)
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return EvalPath(node.value, anchored=False)
        if isinstance(node, ast.Call):
            return self._eval_call(node, depth)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = self.evaluate(node.value, depth + 1)
            if base and base.is_pathlib:
                return EvalPath(
                    str(PurePosixPath(base.path).parent), anchored=base.anchored, is_pathlib=True
                )
            return None
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node, depth)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node, depth)
        return None

    def _is_verified_ospath(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.st.ospath_aliases
        if isinstance(node, ast.Attribute) and node.attr == "path":
            return isinstance(node.value, ast.Name) and node.value.id in self.st.os_aliases
        return False

    def _eval_call(self, node: ast.Call, depth: int) -> EvalPath | None:
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id == "str" and self.st.str_trusted and len(node.args) == 1:
                res = self.evaluate(node.args[0], depth + 1)
                return EvalPath(res.path, anchored=res.anchored, is_pathlib=False) if res else None
            if fn.id in self.st.path_cls_aliases and node.args:
                return self._eval_path_cls(node.args, depth)
            if fn.id in self.st.path_fn_aliases:
                return self._eval_path_op(self.st.path_fn_aliases[fn.id], node.args, depth)
            return None
        if isinstance(fn, ast.Attribute):
            if fn.attr in ("dirname", "join", "abspath", "realpath") and self._is_verified_ospath(
                fn.value
            ):
                return self._eval_path_op(fn.attr, node.args, depth)
            if (
                fn.attr == "fspath"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in self.st.os_aliases
            ):
                return self._eval_path_op("fspath", node.args, depth)
            if (
                fn.attr in _PATHLIB_NAMES
                and isinstance(fn.value, ast.Name)
                and fn.value.id in self.st.pathlib_aliases
            ):
                return self._eval_path_cls(node.args, depth)
            base = self.evaluate(fn.value, depth + 1)
            if base and base.is_pathlib:
                if fn.attr in ("resolve", "absolute") and not node.args:
                    return EvalPath(
                        posixpath.normpath(base.path), anchored=base.anchored, is_pathlib=True
                    )
                if fn.attr == "joinpath" and node.args:
                    return self._join_paths(
                        base, [self.evaluate(a, depth + 1) for a in node.args], is_pathlib=True
                    )
        return None

    def _eval_path_cls(self, args: list[ast.expr], depth: int) -> EvalPath | None:
        if not args:
            return None
        first = self.evaluate(args[0], depth + 1)
        if first is None:
            return None
        base = EvalPath(first.path, anchored=first.anchored, is_pathlib=True)
        if len(args) == 1:
            return base
        return self._join_paths(
            base, [self.evaluate(a, depth + 1) for a in args[1:]], is_pathlib=True
        )

    def _eval_path_op(self, op: str, args: list[ast.expr], depth: int) -> EvalPath | None:
        if op == "dirname" and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            return EvalPath(posixpath.dirname(p.path), anchored=p.anchored) if p else None
        if op in ("abspath", "realpath") and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            return (
                EvalPath(posixpath.normpath(p.path), anchored=True) if (p and p.anchored) else None
            )
        if op == "fspath" and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            return EvalPath(p.path, anchored=p.anchored, is_pathlib=False) if p else None
        if op == "join" and args:
            first = self.evaluate(args[0], depth + 1)
            if first is None:
                return None
            return self._join_paths(
                first, [self.evaluate(a, depth + 1) for a in args[1:]], is_pathlib=False
            )
        return None

    def _join_paths(
        self, first: EvalPath, rest: list[EvalPath | None], *, is_pathlib: bool
    ) -> EvalPath | None:
        parts: list[str] = []
        for p in rest:
            if p is None or p.anchored or p.path.startswith(("/", "~")):
                return None
            parts.append(p.path)
        if is_pathlib:
            joined = str(PurePosixPath(first.path).joinpath(*parts))
        else:
            joined = posixpath.join(first.path, *parts) if parts else first.path
        return EvalPath(joined, anchored=first.anchored, is_pathlib=is_pathlib)

    def _eval_subscript(self, node: ast.Subscript, depth: int) -> EvalPath | None:
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            base = self.evaluate(node.value.value, depth + 1)
            k = node.slice
            if (
                base
                and base.is_pathlib
                and isinstance(k, ast.Constant)
                and isinstance(k.value, int)
            ):
                parents = PurePosixPath(base.path).parents
                if 0 <= k.value < len(parents):
                    return EvalPath(str(parents[k.value]), anchored=base.anchored, is_pathlib=True)
        return None

    def _eval_binop(self, node: ast.BinOp, depth: int) -> EvalPath | None:
        lv, rv = self.evaluate(node.left, depth + 1), self.evaluate(node.right, depth + 1)
        if lv is None or rv is None:
            return None
        if isinstance(node.op, ast.Div) and lv.is_pathlib:
            return self._join_paths(lv, [rv], is_pathlib=True)
        if isinstance(node.op, ast.Add) and not lv.anchored and not rv.anchored:
            return EvalPath(lv.path + rv.path, anchored=False)
        return None

    def resolve_to_repo_dir(self, eval_res: EvalPath) -> str | None:
        """Resolve an __file__-anchored EvalPath to a repo-relative directory."""
        if not eval_res.anchored:
            return None
        norm = posixpath.normpath(eval_res.path)
        if norm == _CW_ROOT:
            return ""
        if norm.startswith(_CW_ROOT + "/"):
            return norm[len(_CW_ROOT) + 1 :]
        return None


_PATHLIB_NAMES = frozenset({"Path", "PurePath", "PurePosixPath"})
