"""Pure AST static analysis and component-stack evaluation for committed_sys_path."""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass

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

_PATHLIB_NAMES = frozenset({"Path", "PurePath", "PurePosixPath"})
_MAX_EVAL_DEPTH = 32


@dataclass(frozen=True)
class EvalPath:
    """Statically evaluated path value represented as repo-relative components."""

    parts: tuple[str, ...]
    anchored: bool
    is_file: bool = False
    is_pathlib: bool = False
    descended: bool = False
    visited_dirs: tuple[str, ...] = ()
    raw_str: str = ""


def _invalid_segment(seg: str) -> bool:
    if seg.startswith(("/", "~", "\\", ":")):
        return True
    if "\x00" in seg or "\\" in seg or "_CW_ROOT_" in seg:
        return True
    if len(seg) >= 2 and seg[1] == ":" and seg[0].isalpha():
        return True
    return False


class StaticPathEvaluator:
    """Evaluates AST expressions resolving to __file__-anchored repository paths."""

    def __init__(self, st: AliasState, file_path: str) -> None:
        self.st = st
        self.file_path = file_path

    def evaluate(self, node: ast.AST, depth: int = 0) -> EvalPath | None:
        """Evaluate an AST expression into an EvalPath or None if dynamic."""
        if depth > _MAX_EVAL_DEPTH:
            return None
        if isinstance(node, ast.Name):
            return self._eval_name(node.id, depth)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return EvalPath(parts=(), anchored=False, raw_str=node.value)
        if isinstance(node, ast.Call):
            return self._eval_call(node, depth)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = self.evaluate(node.value, depth + 1)
            if base and base.is_pathlib:
                return self._pop_parent(base, 1, is_pathlib=True)
            return None
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node, depth)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node, depth)
        return None

    def _eval_name(self, name: str, depth: int) -> EvalPath | None:
        if name == "__file__":
            if self.st.file_rebound:
                return None
            clean = posixpath.normpath(self.file_path).lstrip("/")
            if not clean or clean in (".", "..") or clean.startswith("../"):
                return None
            if _invalid_segment(clean):
                return None
            return EvalPath(parts=tuple(clean.split("/")), anchored=True, is_file=True)
        if name in self.st.env:
            return self.evaluate(self.st.env[name], depth + 1)
        return None

    def _is_verified_ospath(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.st.ospath_aliases
        if isinstance(node, ast.Attribute) and node.attr == "path":
            return isinstance(node.value, ast.Name) and node.value.id in self.st.os_aliases
        return False

    def _eval_call(self, node: ast.Call, depth: int) -> EvalPath | None:
        if node.keywords:
            return None
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id == "str" and self.st.str_trusted and len(node.args) == 1:
                res = self.evaluate(node.args[0], depth + 1)
                if res is None:
                    return None
                return EvalPath(
                    parts=res.parts,
                    anchored=res.anchored,
                    is_file=res.is_file,
                    is_pathlib=False,
                    descended=res.descended,
                    visited_dirs=res.visited_dirs,
                    raw_str=res.raw_str,
                )
            if fn.id in self.st.path_cls_aliases and node.args:
                return self._eval_path_cls(node.args, depth)
            if fn.id in self.st.path_fn_aliases:
                return self._eval_path_op(self.st.path_fn_aliases[fn.id], node.args, depth)
            return None
        if isinstance(fn, ast.Attribute):
            return self._eval_attr_call(fn, node.args, depth)
        return None

    def _eval_attr_call(
        self, fn: ast.Attribute, args: list[ast.expr], depth: int
    ) -> EvalPath | None:
        if fn.attr in ("dirname", "join", "abspath", "realpath") and self._is_verified_ospath(
            fn.value
        ):
            return self._eval_path_op(fn.attr, args, depth)
        if (
            fn.attr == "fspath"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in self.st.os_aliases
        ):
            return self._eval_path_op("fspath", args, depth)
        if (
            fn.attr in _PATHLIB_NAMES
            and isinstance(fn.value, ast.Name)
            and fn.value.id in self.st.pathlib_aliases
        ):
            return self._eval_path_cls(args, depth)
        base = self.evaluate(fn.value, depth + 1)
        if base and base.is_pathlib:
            if fn.attr in ("resolve", "absolute") and not args:
                return base
            if fn.attr == "joinpath" and args:
                return self._join_paths(
                    base, [self.evaluate(a, depth + 1) for a in args], is_pathlib=True
                )
        return None

    def _eval_path_cls(self, args: list[ast.expr], depth: int) -> EvalPath | None:
        if not args:
            return None
        first = self.evaluate(args[0], depth + 1)
        if first is None:
            return None
        base = EvalPath(
            parts=first.parts,
            anchored=first.anchored,
            is_file=first.is_file,
            is_pathlib=True,
            descended=first.descended,
            visited_dirs=first.visited_dirs,
            raw_str=first.raw_str,
        )
        if len(args) == 1:
            return base
        return self._join_paths(
            base, [self.evaluate(a, depth + 1) for a in args[1:]], is_pathlib=True
        )

    def _eval_path_op(self, op: str, args: list[ast.expr], depth: int) -> EvalPath | None:
        if op == "dirname" and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            return self._pop_parent(p, 1, is_pathlib=False) if p else None
        if op in ("abspath", "realpath") and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            if not (p and p.anchored):
                return None
            return EvalPath(
                parts=p.parts,
                anchored=True,
                is_file=p.is_file,
                is_pathlib=False,
                descended=p.descended,
                visited_dirs=p.visited_dirs,
            )
        if op == "fspath" and len(args) == 1:
            p = self.evaluate(args[0], depth + 1)
            if p is None:
                return None
            return EvalPath(
                parts=p.parts,
                anchored=p.anchored,
                is_file=p.is_file,
                is_pathlib=False,
                descended=p.descended,
                visited_dirs=p.visited_dirs,
                raw_str=p.raw_str,
            )
        if op == "join" and args:
            first = self.evaluate(args[0], depth + 1)
            if first is None:
                return None
            return self._join_paths(
                first, [self.evaluate(a, depth + 1) for a in args[1:]], is_pathlib=False
            )
        return None

    def _pop_parent(self, base: EvalPath, steps: int, *, is_pathlib: bool) -> EvalPath | None:
        if not base.anchored or base.descended:
            return None
        if steps < 1 or steps > len(base.parts):
            return None
        new_parts = base.parts[:-steps]
        visited = (*base.visited_dirs, "/".join(new_parts))
        return EvalPath(
            parts=new_parts,
            anchored=True,
            is_file=False,
            is_pathlib=is_pathlib,
            descended=False,
            visited_dirs=visited,
        )

    def _join_paths(
        self, first: EvalPath, rest: list[EvalPath | None], *, is_pathlib: bool
    ) -> EvalPath | None:
        if not first.anchored or first.is_file:
            return None
        parts = list(first.parts)
        descended = first.descended
        visited = list(first.visited_dirs)
        for p in rest:
            if p is None or p.anchored or _invalid_segment(p.raw_str):
                return None
            for comp in p.raw_str.split("/"):
                if comp in ("", "."):
                    continue
                if comp == "_CW_ROOT_":
                    return None
                if comp == "..":
                    if descended or not parts:
                        return None
                    parts.pop()
                    visited.append("/".join(parts))
                else:
                    parts.append(comp)
                    descended = True
                    visited.append("/".join(parts))
        return EvalPath(
            parts=tuple(parts),
            anchored=True,
            is_file=False,
            is_pathlib=is_pathlib,
            descended=descended,
            visited_dirs=tuple(visited),
        )

    def _eval_subscript(self, node: ast.Subscript, depth: int) -> EvalPath | None:
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            base = self.evaluate(node.value.value, depth + 1)
            k = node.slice
            if (
                base
                and base.is_pathlib
                and isinstance(k, ast.Constant)
                and isinstance(k.value, int)
                and not isinstance(k.value, bool)
            ):
                return self._pop_parent(base, k.value + 1, is_pathlib=True)
        return None

    def _eval_binop(self, node: ast.BinOp, depth: int) -> EvalPath | None:
        lv, rv = self.evaluate(node.left, depth + 1), self.evaluate(node.right, depth + 1)
        if lv is None or rv is None:
            return None
        if isinstance(node.op, ast.Div) and lv.is_pathlib:
            return self._join_paths(lv, [rv], is_pathlib=True)
        if isinstance(node.op, ast.Add) and not lv.anchored and not rv.anchored:
            return EvalPath(parts=(), anchored=False, raw_str=lv.raw_str + rv.raw_str)
        return None

    def resolve_to_repo_dir(self, eval_res: EvalPath) -> str | None:
        """Resolve an __file__-anchored EvalPath to a repo-relative directory."""
        if not eval_res.anchored or eval_res.is_file:
            return None
        return "/".join(eval_res.parts)
