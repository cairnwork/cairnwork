"""tools/blackbox_suggest (#370): a value taken from a project module stays in known-safe uses.

modstore follows where such a value goes (assignments, containers, parameters, returns). Each
place it is used must be one of the uses listed here: moved on, read, called as an imported
function or module function, a read-only method, or passed to this module's own functions, to
a project function or to a function that only reads it. Any other use, a syntax position not
listed here included, refuses the draft.
"""

from __future__ import annotations

import ast

from .modstore import DISPLAYS, _callee, _chain, _dotted

SAFE_CALLEES = frozenset({  # builtins and library functions that only read their arguments
    "len", "isinstance", "issubclass", "str", "repr", "int", "float", "bool", "hash", "id",
    "type", "print", "sorted", "frozenset", "tuple", "format", "min", "max", "sum", "any",
    "all", "abs", "round", "divmod", "ord", "chr", "callable", "hasattr", "getattr",
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


def escapes(
    tree: ast.Module, derived: set[str], seeds: set[str], parents: dict[int, ast.AST]
) -> bool:
    """A held name is used outside the known-safe uses. The use is that of the whole receiver
    chain it starts (attribute and subscript lookups on it)."""
    held = {m for m in derived if not m.startswith("[]")}
    local = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and _chain(node.target) & held:
            return True  # an in-place operator on it, through any alias
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in held:
            use: ast.expr = node
            while True:
                parent = parents.get(id(use))
                if not (isinstance(parent, ast.Attribute | ast.Subscript) and parent.value is use):
                    break
                use = parent
            if not _safe_use(use, parents, held | seeds, seeds, local):
                return True
    return False


def _safe_use(
    use: ast.expr, parents: dict[int, ast.AST], held: set[str], seeds: set[str], local: set[str]
) -> bool:
    parent = parents.get(id(use))
    if isinstance(parent, ast.Call) and parent.func is use:  # calling it
        if isinstance(use, ast.Name) or _dotted(getattr(use, "value", None) or use) in seeds:
            return True  # an imported function or class, or module.function(...)
        return isinstance(use, ast.Attribute) and use.attr in READ_METHODS
    if isinstance(parent, ast.Starred | ast.keyword):  # *value or name=value in a call
        call = parents.get(id(parent))
        if isinstance(call, ast.Call):
            return _safe_argument(call, use, held, seeds, local)
        return True  # *value in a display moves on; a class keyword (metaclass=) is read
    if isinstance(parent, ast.Call):
        return _safe_argument(parent, use, held, seeds, local)
    if isinstance(parent, ast.AugAssign):
        return parent.target is not use
    if isinstance(parent, ast.Attribute | ast.Subscript):
        return True  # a subscript key
    return isinstance(parent, PASSES + READS)


def _safe_argument(
    call: ast.Call, use: ast.expr, held: set[str], seeds: set[str], local: set[str]
) -> bool:
    """Passed to this module's own functions, to a project function (followed there by that
    module's own check), to a function that only reads it, or as a function to call later."""
    name = _callee(call.func)
    if name in SAFE_CALLEES or name in local:
        return True
    if isinstance(call.func, ast.Name | ast.Attribute) and _chain(call.func) & seeds:
        return True
    plain = isinstance(use, ast.Name) or (
        isinstance(use, ast.Attribute) and _dotted(use.value) in seeds
    )
    if name in LATER and plain:
        return True
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in READ_METHODS
        and not _chain(func.value) & held
    )
