"""tools/blackbox_suggest (#370): a value taken from a project module stays in known-safe uses.

modstore follows where such a value goes (assignments, containers, parameters, returns). Each
place it is used must be one of the uses listed here: moved on, read, called as an imported
function or module function, a read-only method, or passed to this module's own functions, to
a project function or to a function that only reads it. Any other use, a syntax position not
listed here included, refuses the draft. An element taken out of it (an index, a read method
or builtin that hands elements back) is held too. A builtin or standard-library name counts
only when this module does not bind that name to anything else.
"""

from __future__ import annotations

import ast
import sys
from typing import NamedTuple

from .modstore import DISPLAYS, HANDS_BACK, TAKES_ONE, VIEWS, _callee, _chain, _dotted

SAFE_CALLEES = frozenset({  # builtins and library functions that only read their arguments
    "len", "isinstance", "issubclass", "str", "repr", "int", "float", "bool", "hash", "id",
    "type", "print", "sorted", "frozenset", "tuple", "format", "min", "max", "sum", "any",
    "all", "abs", "round", "divmod", "ord", "chr", "callable", "hasattr",
    "enumerate", "zip", "reversed", "range", "iter", "next", "bytes", "set", "list", "dict",
    "Path", "PurePosixPath", "escape", "fnmatch", "samefile", "fspath", "join", "basename",
    "dirname", "dumps", "quote", "compile", "b64encode", "Popen",
})  # fmt: skip
READ_METHODS = frozenset({
    "get", "keys", "values", "items", "copy", "count", "index", "startswith", "endswith",
    "format", "join", "split", "strip", "lower", "upper", "encode", "decode", "replace", "find",
    "rstrip", "lstrip", "splitlines", "match", "fullmatch", "search", "finditer", "findall",
    "sub", "is_dir", "is_file", "exists", "read_text", "read_bytes", "relative_to",
    "with_suffix", "resolve", "issubset", "issuperset", "union", "intersection", "difference",
    "isdigit", "isidentifier", "partition", "rpartition", "casefold", "hex", "group", "groups",
    "groupdict",
})  # fmt: skip
LATER = frozenset({  # calls that take a function to call later
    "map", "filter", "partial", "Thread", "Process", "Timer", "submit", "run_in_executor",
    "to_thread", "create_task", "register", "signal",
})  # fmt: skip
COPIES = HANDS_BACK | {"dict", "set", "frozenset"}  # builtins handing elements back
HANDED = frozenset({"get", "values", "items", "keys", "copy"})  # read methods handing elements
PASSES = (  # the value moves on; _derived follows where it goes
    *(ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.comprehension),
    *(ast.withitem, ast.Return, ast.Yield, ast.YieldFrom, ast.Await, ast.IfExp, ast.BoolOp),
    *DISPLAYS,
    *(ast.arguments, ast.match_case, ast.Match),
)
READS = (  # the result is a new value, or the value is only looked at
    *(ast.Compare, ast.BinOp, ast.UnaryOp, ast.FormattedValue, ast.JoinedStr, ast.If),
    *(ast.While, ast.Assert, ast.Expr, ast.Raise, ast.Slice, ast.Delete, ast.ExceptHandler),
    *(ast.arg, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),  # annotation, decorator
)
LITERALS = (ast.Constant, ast.JoinedStr, *DISPLAYS)


class Scope(NamedTuple):
    held: set[str]
    seeds: set[str]
    local: set[str]
    modules: set[str]
    std: set[str]  # names bound only to the standard library, under the library's own name
    bound: set[str]  # every name this module binds

    def library(self, func: ast.expr) -> bool:
        """func is a builtin, a standard-library function, or a method of a literal."""
        if isinstance(func, ast.Name):
            return func.id in self.std or func.id not in self.bound
        root = func
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, LITERALS):
            return True
        return isinstance(root, ast.Name) and root.id in self.std


def escapes(
    tree: ast.Module,
    derived: set[str],
    seeds: set[str],
    parents: dict[int, ast.AST],
    modules: set[str],
) -> bool:
    """A held name, or an element of a container holding one, is used outside the known-safe
    uses. The use is that of the whole chain it starts (lookups and elements handed back)."""
    held = {m for m in derived if not m.startswith("[]")}
    boxes = {m.lstrip("[]") for m in derived if m.startswith("[]")}
    local = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    s = Scope(held | seeds, seeds, local, modules, *_library_names(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and _chain(node.target) & held:
            return True  # an in-place operator on it, through any alias
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        if node.id in held and not _climbed_safe(node, parents, s):
            return True
        if node.id in boxes - held and not _box_safe(node, parents, s):
            return True
    return False


def _climbed_safe(use: ast.expr, parents: dict[int, ast.AST], s: Scope) -> bool:
    while True:
        parent = parents.get(id(use))
        if isinstance(parent, ast.Attribute | ast.Subscript) and parent.value is use:
            use = parent
        elif isinstance(parent, ast.Call) and _hands_back(parent, use, s):
            if not _one_element(parent):
                return _box_safe(parent, parents, s)  # a new container of its elements
            use = parent
        else:
            return _safe_use(use, parents, s)


def _hands_back(call: ast.Call, use: ast.expr, s: Scope) -> bool:
    """The call's result is, or holds, an element of use: a read method such as get or values,
    or a builtin such as list, sorted or next."""
    if call.func is use:
        return isinstance(use, ast.Attribute) and use.attr in HANDED
    name = _callee(call.func)
    return name in COPIES and s.library(call.func) and any(a is use for a in call.args)


def _one_element(call: ast.Call) -> bool:
    """next(v), min(v) or v.get(k): the result is one element itself, not a new container."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr == "get"
    return isinstance(func, ast.Name) and func.id in TAKES_ONE


def _box_safe(name: ast.expr, parents: dict[int, ast.AST], s: Scope) -> bool:
    """A new container holding a held value: an element taken out of it is held; the container
    itself is a new value."""
    parent = parents.get(id(name))
    if isinstance(parent, ast.Subscript) and parent.value is name:
        return _climbed_safe(parent, parents, s)
    call = parents.get(id(parent))
    if isinstance(parent, ast.Attribute) and parent.attr in VIEWS:
        return not (isinstance(call, ast.Call) and call.func is parent) or _climbed_safe(
            call, parents, s
        )
    if isinstance(parent, ast.Call) and _hands_back(parent, name, s):
        one = _one_element(parent)
        return _climbed_safe(parent, parents, s) if one else _box_safe(parent, parents, s)
    if isinstance(parent, ast.Attribute | ast.Subscript):
        return True  # another attribute of the container, or a key
    # an argument (also *x, name=x), or a position that only moves it on or looks at it;
    # any other (a lambda body, an in-place operator target) is refused
    return isinstance(parent, (ast.Call, ast.keyword, ast.Starred, *PASSES, *READS))


def _safe_use(use: ast.expr, parents: dict[int, ast.AST], s: Scope) -> bool:
    parent = parents.get(id(use))
    if isinstance(parent, ast.Call) and parent.func is use:  # calling it
        if isinstance(use, ast.Name) or _dotted(getattr(use, "value", None) or use) in s.seeds:
            return True  # an imported function or class, or module.function(...)
        return isinstance(use, ast.Attribute) and use.attr in READ_METHODS
    if _method_as_value(use, parent, s.modules) or _method_as_callback(use, parents, s):
        return False
    if isinstance(parent, ast.Starred | ast.keyword):  # *value or name=value in a call
        call = parents.get(id(parent))
        if isinstance(call, ast.Call):
            return _safe_argument(call, use, s)
        return True  # *value in a display moves on; a class keyword (metaclass=) is read
    if isinstance(parent, ast.Call):
        return _safe_argument(parent, use, s)
    if isinstance(parent, ast.AugAssign):
        return parent.target is not use
    if isinstance(parent, ast.Attribute | ast.Subscript):
        return True  # a subscript key
    return isinstance(parent, PASSES + READS)


def _may_be_method(use: ast.expr, modules: set[str]) -> bool:
    return (
        isinstance(use, ast.Attribute)
        and use.attr not in READ_METHODS
        and _dotted(use.value) not in modules
    )


def _method_as_value(use: ast.expr, parent: ast.AST | None, modules: set[str]) -> bool:
    """An attribute of a held object that may be a method, moved on without being called: the
    method would be called later on the held object."""
    stored = (ast.Assign, ast.AnnAssign, ast.NamedExpr, *DISPLAYS)
    return _may_be_method(use, modules) and isinstance(parent, stored)


def _method_as_callback(use: ast.expr, parents: dict[int, ast.AST], s: Scope) -> bool:
    """A possible method of a held object given to a call that is not a reader: it may be
    called there."""
    parent = parents.get(id(use))
    call = parents.get(id(parent)) if isinstance(parent, ast.Starred | ast.keyword) else parent
    if not (isinstance(call, ast.Call) and call.func is not use):
        return False
    reader = _callee(call.func) in SAFE_CALLEES and s.library(call.func)
    return _may_be_method(use, s.modules) and not reader


def _safe_argument(call: ast.Call, use: ast.expr, s: Scope) -> bool:
    """Passed to this module's own functions, to a project function (followed there by that
    module's own check), to a library function that only reads it, or as a function to call
    later."""
    func = call.func
    name = _callee(func)
    own = isinstance(func, ast.Name) or _dotted(func) in {f"self.{name}", f"cls.{name}"}
    if own and name in s.local:
        return True
    if name in SAFE_CALLEES and s.library(func):
        return True
    if isinstance(func, ast.Name | ast.Attribute) and _chain(func) & s.seeds:
        return True
    plain = isinstance(use, ast.Name) or (
        isinstance(use, ast.Attribute) and _dotted(use.value) in s.seeds
    )
    if name in LATER and plain and s.library(func):
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in READ_METHODS
        and not _chain(func.value) & s.held
    )


def _library_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(names bound only by importing the standard library under its own name, every name
    this module binds)."""
    std: set[str] = set()
    other: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                root = a.name.split(".")[0]
                ok = root in sys.stdlib_module_names and a.asname in (None, root)
                (std if ok else other).add(a.asname or root)
        elif isinstance(n, ast.ImportFrom):
            ok = not n.level and (n.module or "").split(".")[0] in sys.stdlib_module_names
            for a in n.names:
                (std if ok and a.asname in (None, a.name) else other).add(a.asname or a.name)
        elif isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            other.add(n.id)
        elif isinstance(n, ast.arg):
            other.add(n.arg)
        elif isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            other.add(n.name)
        elif isinstance(n, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and n.name:
            other.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            other.add(n.rest)
    return std - other, std | other
