"""Pure AST static analysis and path evaluation for committed_sys_path.

Detects in-process search-path modifications and statically evaluates whether
their target paths resolve to base-committed repository directories without
shadow modules.
"""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass

from experimental.committed_sys_path.aliases import (
    _AliasState,
    collect_aliases_and_env,
    is_sys_expr,
    is_sys_path_expr,
)

__all__ = [
    "PathEdit",
    "StaticPathEvaluator",
    "collect_aliases_and_env",
    "extract_new_path_targets",
    "find_path_edits",
    "is_sys_expr",
    "is_sys_path_expr",
]


@dataclass(frozen=True)
class PathEdit:
    """Represents a detected in-process search path edit."""

    lineno: int
    kind: str
    target_nodes: tuple[ast.AST, ...]
    raw_stmt: str


class StaticPathEvaluator:
    """Evaluates AST expressions resolving to repository-relative paths."""

    def __init__(self, env: dict[str, ast.AST], file_path: str) -> None:
        self.env = env
        self.file_path = file_path

    def evaluate(self, node: ast.AST, depth: int = 0) -> tuple[str, list[str] | str] | None:
        """Evaluate an AST expression into ('file', comps), ('dir', comps), or ('lit', str)."""
        if depth > 10:
            return None
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return ("file", [])
            return self.evaluate(self.env[node.id], depth + 1) if node.id in self.env else None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ("lit", node.value)
        if isinstance(node, ast.Call):
            return self._eval_call(node, depth)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self._up(self.evaluate(node.value, depth + 1), 1)
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node, depth)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node, depth)
        return None

    def _eval_call(self, node: ast.Call, depth: int) -> tuple[str, list[str] | str] | None:
        fn_name = ast.unparse(node.func)
        tail = fn_name.split(".")[-1]
        if tail == "dirname" and node.args:
            return self._up(self.evaluate(node.args[0], depth + 1), 1)
        if tail in (
            "abspath",
            "realpath",
            "resolve",
            "absolute",
            "str",
            "fspath",
            "Path",
            "PurePath",
        ):
            if isinstance(node.func, ast.Attribute) and tail in ("resolve", "absolute"):
                return self.evaluate(node.func.value, depth + 1)
            if node.args:
                if tail in ("Path", "PurePath") and len(node.args) > 1:
                    return self._join([self.evaluate(a, depth + 1) for a in node.args])
                return self.evaluate(node.args[0], depth + 1)
        if tail == "join" and node.args and ("path" in fn_name or "join" in fn_name):
            return self._join([self.evaluate(a, depth + 1) for a in node.args])
        if tail == "joinpath" and isinstance(node.func, ast.Attribute):
            parts = [self.evaluate(node.func.value, depth + 1)]
            parts.extend(self.evaluate(a, depth + 1) for a in node.args)
            return self._join(parts)
        return None

    def _eval_subscript(
        self, node: ast.Subscript, depth: int
    ) -> tuple[str, list[str] | str] | None:
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            k = node.slice
            if isinstance(k, ast.Constant) and isinstance(k.value, int):
                return self._up(self.evaluate(node.value.value, depth + 1), k.value + 1)
        return None

    def _eval_binop(self, node: ast.BinOp, depth: int) -> tuple[str, list[str] | str] | None:
        if isinstance(node.op, ast.Div):
            return self._join(
                [self.evaluate(node.left, depth + 1), self.evaluate(node.right, depth + 1)]
            )
        if isinstance(node.op, ast.Add):
            lv, rv = self.evaluate(node.left, depth + 1), self.evaluate(node.right, depth + 1)
            if lv and rv and lv[0] == "lit" and rv[0] == "lit":
                return ("lit", str(lv[1]) + str(rv[1]))
        return None

    def _up(self, b: tuple[str, list[str] | str] | None, k: int) -> tuple[str, list[str]] | None:
        if not b or b[0] not in ("file", "dir"):
            return None
        kind, comps_raw = b[0], b[1]
        comps = list(comps_raw) if isinstance(comps_raw, list) else []
        if kind == "file":
            comps = []
            k -= 1
        comps.extend([".."] * k)
        return ("dir", comps)

    def _join(
        self, parts: list[tuple[str, list[str] | str] | None]
    ) -> tuple[str, list[str] | str] | None:
        if any(p is None for p in parts) or parts[0] is None:
            return None
        first = ("dir", []) if parts[0][0] == "file" else parts[0]
        if first[0] == "dir":
            comps = list(first[1]) if isinstance(first[1], list) else []
            for p in parts[1:]:
                if (
                    p is None
                    or p[0] != "lit"
                    or not isinstance(p[1], str)
                    or p[1].startswith(("/", "~"))
                ):
                    return None
                comps.extend(c for c in p[1].split("/") if c not in ("", "."))
            return ("dir", comps)
        if first[0] == "lit" and isinstance(first[1], str) and not first[1].startswith(("/", "~")):
            joined = first[1]
            for p in parts[1:]:
                if (
                    p is None
                    or p[0] != "lit"
                    or not isinstance(p[1], str)
                    or p[1].startswith(("/", "~"))
                ):
                    return None
                joined = posixpath.join(joined, p[1])
            return ("lit", posixpath.normpath(joined))
        return None

    def resolve_to_repo_dir(self, eval_res: tuple[str, list[str] | str]) -> str | None:
        """Resolve evaluated path to a normalized repository-relative directory path."""
        kind, val = eval_res
        if kind == "lit" and isinstance(val, str):
            if val.startswith(("/", "~")):
                return None
            norm = posixpath.normpath(val)
        elif kind == "dir" and isinstance(val, list):
            base = posixpath.dirname(self.file_path)
            norm = posixpath.normpath(posixpath.join(base, *val)) if val else (base or "")
        else:
            return None
        return None if norm.startswith("..") else ("" if norm in (".", "") else norm)


def extract_new_path_targets(value: ast.AST, st: _AliasState) -> list[ast.AST]:
    """Extract individual target path expressions added in an assignment."""
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return extract_new_path_targets(value.left, st) + extract_new_path_targets(value.right, st)
    if is_sys_path_expr(
        value, st.sys_aliases, st.sys_funcs, st.path_aliases, st.path_funcs, st.env
    ):
        return []
    if isinstance(value, (ast.List, ast.Tuple)):
        return [elt.value if isinstance(elt, ast.Starred) else elt for elt in value.elts]
    return [value]


def find_path_edits(tree: ast.AST, source_text: str) -> list[PathEdit]:
    """Find all in-process search path modification statements in the AST."""
    sys_aliases, path_aliases, sys_funcs, path_funcs, env = collect_aliases_and_env(tree)
    st = _AliasState(sys_aliases, path_aliases, sys_funcs, path_funcs, env)
    lines = source_text.splitlines()
    edits: list[PathEdit] = []
    for node in ast.walk(tree):
        _detect_call_edits(node, st, lines, edits)
        _detect_assign_edits(node, st, lines, edits)
    return edits


def _detect_call_edits(
    node: ast.AST, st: _AliasState, lines: list[str], edits: list[PathEdit]
) -> None:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return
    func = node.func
    attr = func.attr
    lineno = node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    if attr in ("insert", "append", "extend") and is_sys_path_expr(
        func.value, st.sys_aliases, st.sys_funcs, st.path_aliases, st.path_funcs, st.env
    ):
        targets: list[ast.AST] = []
        if attr == "insert" and len(node.args) >= 2:
            targets = [node.args[1]]
        elif attr in ("insert", "append") and node.args:
            targets = [node.args[0]]
        elif attr == "extend" and node.args:
            arg = node.args[0]
            targets = list(arg.elts) if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
        edits.append(PathEdit(lineno, f"sys.path.{attr}", tuple(targets), raw))
    elif attr == "addsitedir" and isinstance(func.value, ast.Name) and func.value.id == "site":
        edits.append(PathEdit(lineno, "site.addsitedir", tuple(node.args[:1]), raw))


def _detect_assign_edits(
    node: ast.AST, st: _AliasState, lines: list[str], edits: list[PathEdit]
) -> None:
    if not isinstance(node, (ast.Assign, ast.AugAssign)):
        return
    tgts = [node.target] if isinstance(node, ast.AugAssign) else node.targets
    lineno = node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    for t in tgts:
        if (
            isinstance(t, ast.Name)
            and t.id not in ("__path__", "submodule_search_locations")
            and t.id not in st.path_aliases
        ):
            continue
        if is_sys_path_expr(
            t, st.sys_aliases, st.sys_funcs, st.path_aliases, st.path_funcs, st.env
        ):
            targets = extract_new_path_targets(node.value, st)
            kind = "sys.path+=" if isinstance(node, ast.AugAssign) else "sys.path="
            edits.append(PathEdit(lineno, kind, tuple(targets), raw))
