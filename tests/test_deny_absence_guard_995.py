"""#995 step 2: no verdict reads the deny channel, and nothing reads a missing line as "clear".

Deny evidence proves a denial, never its absence (a busy host can lose the run's own line). The
tables below list every file under ``src/`` that may touch it today, and how. A new reader fails
here on purpose: ask ``Evidence.denial_of(operation, target)`` (DENIED or UNKNOWN) and add the file
to the tables with a reason, in the change that lets a verdict read the channel.
The scan is syntactic: it cannot follow a value through every indirection, so it is a tripwire for
the usual shapes (and fails closed on the carrier names), not a proof. Not covered: a name built
at run time (getattr(ev, 'den' + 'ied')), a CLEAN set imported from another module, a helper
that tests its own argument, ``from x import *``, and presence-style reads that name no CLEAN
(``ev.state != 'DENIED'``, ``ev.detail == ''``), evidence turned into text another way
(``f'{ev}'``, ``repr(ev)``, ``format(ev)``, ``'%s' % ev``), a CLEAN held as a default, loop
variable, walrus or class attribute, ``''.join([...])`` or ``f'CL{"EAN"}'``, and a helper that
returns ``e.state``. A carrier name may be passed as a keyword value to a call that builds a
value, held as a field, tested bare in an ``if`` whose body only calls ``append`` or a logger, or
put in an f-string that is an argument of such a logging call; a bare call argument is refused
because the callee could be len, bool or re.match.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
EXEMPT = {"cairnwork/deny_channel.py", "cairnwork/deny_judge.py"}  # the recorder and the judge
NAMES = {  # carrier name -> files that may hold it (record, pass on, log), never test it
    "deny_denials": {"cairnwork/verifiers.py", "cairnwork/verifier_run.py"},
    "denials": {"cairnwork/verifiers.py"},
    "deny_evidence": {
        "cairnwork/verifiers.py",
        "cairnwork/verifier_run.py",
        "cairnwork/pipeline_judge.py",  # logs it only
    },
}
IMPORTS = {"cairnwork/verifier_exec.py"}  # who may import a deny_* module (it records)
LOGGERS = {"log", "append", "warning", "info", "debug", "error", "exception", "critical"}
READERS = {  # calls that read what they are given: a carrier never goes to these by keyword
    *("str", "bool", "len", "sorted", "dict", "frozenset", "set", "list", "tuple", "repr"),
    *("format", "sum", "any", "all", "min", "max", "int", "bytes", "map", "filter", "next"),
    *("search", "match", "fullmatch", "sub", "findall", "split", "find", "count", "index", "join"),
}
EVIDENCE = {"state", *NAMES}  # a value that carries a verdict word or the recorded pairs
FIX = (
    "deny evidence proves a denial, never its absence (#995): read it only through "
    "Evidence.denial_of(operation, target) (DENIED or UNKNOWN), and add the file to the tables "
    "in tests/test_deny_absence_guard_995.py with the reason"
)
BY_NAME = {"getattr", "hasattr", "attrgetter", "methodcaller"}
HIGHER = {"map", "partial", "filter", "starmap"}  # take a method and call it later


def _callee(call):
    fn = call.func
    return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""


def _clean_like(node, clean_names):
    """CLEAN as a literal (alone or in a container), a name, or an attribute of that name."""
    return any(
        (isinstance(n, ast.Constant) and _spells_clean(n.value))
        or (isinstance(n, ast.Name) and (n.id == "CLEAN" or n.id in clean_names))
        or (isinstance(n, ast.Attribute) and n.attr == "CLEAN")
        or _folded_clean(n)
        for n in ast.walk(node)
    )


def _spells_clean(value):
    """The verdict word: CLEAN anywhere (the summary starts with it) or a lower-case whole word."""
    return isinstance(value, str) and (
        "CLEAN" in value or value.strip().lower().rstrip(":") == "clean"
    )


def _fold(n):
    """The text of string literals joined with + from the left, or None for anything else."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        left, right = _fold(n.left), _fold(n.right)
        return None if left is None or right is None else left + right
    return None


def _folded_clean(n):
    """'CL' + 'E' + 'AN': a concatenation of string literals that spells CLEAN."""
    return isinstance(n, ast.BinOp) and _spells_clean(_fold(n))


def _carries(node, aliases):
    return any(
        (isinstance(n, ast.Attribute) and n.attr in EVIDENCE)
        or (isinstance(n, ast.Name) and (n.id in EVIDENCE or n.id in aliases))
        or (isinstance(n, ast.Constant) and n.value == "state")
        or (isinstance(n, ast.Call) and _callee(n) == "str")  # the summary text starts with it
        for n in ast.walk(node)
    )


def _names_denied(node):
    return any(
        (isinstance(n, ast.Attribute) and n.attr == "denied")
        or (isinstance(n, ast.Constant) and n.value == "denied")
        for n in ast.walk(node)
    )


def _targets(node):
    return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]


def _aliases(tree):
    """Names bound from a value that carries evidence (assignment, walrus, loop, comprehension)."""
    found = set()
    while True:
        before = len(found)
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign) and n.value:
                pairs = [
                    (t, n.value) for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                ]
            elif isinstance(n, ast.NamedExpr):
                pairs = [(n.target, n.value)]
            elif isinstance(n, ast.For | ast.comprehension):
                pairs = [(n.target, n.iter)]
            else:
                continue
            for target, value in pairs:
                if _carries(value, found):
                    found.update(_targets(target))
        if len(found) == before:
            return found


def _clean_names(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign | ast.AnnAssign) and n.value and _clean_like(n.value, set()):
            out.update(_targets(n.targets[0] if isinstance(n, ast.Assign) else n.target))
        if isinstance(n, ast.ImportFrom):
            out.update(a.asname or a.name for a in n.names if a.name == "CLEAN")
    return out


def _builds(call, parent):
    """A call that builds a value kept as is (not one that reads its arguments or is read on)."""
    kept = (ast.Assign, ast.AnnAssign, ast.Return, ast.Expr, ast.keyword)
    return _callee(call) not in READERS and isinstance(parent.get(call), kept)


def _logs(node, parent):
    """Only calls named append or a logger, with no else branch: ``if carrier: log(...)``."""
    return all(
        isinstance(b, ast.Expr) and isinstance(b.value, ast.Call) and _callee(b.value) in LOGGERS
        for b in node.body
    )


def _allowed_use(node, parent):
    """A carrier name only as a builder's keyword value, a field, a logging test, or logged text."""
    up = parent.get(node)
    if isinstance(up, ast.keyword) and up.value is node:
        return _builds(parent[up], parent)
    if isinstance(up, ast.IfExp) and isinstance(parent.get(up), ast.keyword):
        return node is not up.test and _builds(parent[parent[up]], parent)
    if isinstance(up, ast.AnnAssign) and up.target is node:
        return True
    if isinstance(up, ast.FormattedValue):  # only text built for a logging call
        call = parent.get(parent.get(up))
        return isinstance(call, ast.Call) and _callee(call) in LOGGERS
    return isinstance(up, ast.If) and up.test is node and not up.orelse and _logs(up, parent)


def _findings(tree, rel):
    """What in one parsed file reads deny evidence where it should not."""
    found = []
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    clean_names, aliases = _clean_names(tree), _aliases(tree)
    kept = {  # d = ev.denied ... d(o, t); fns = [ev.denied] ... fns[0](o, t)
        t
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign | ast.AnnAssign | ast.NamedExpr)
        and n.value is not None
        and any(isinstance(m, ast.Attribute) and m.attr == "denied" for m in ast.walk(n.value))
        for t in _targets(n.targets[0] if isinstance(n, ast.Assign) else n.target)
    }

    def add(node, what):
        found.append(f"{rel}:{getattr(node, 'lineno', 0)} {what}")

    for node in ast.walk(tree):
        name = (
            node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else node.value
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            else node.arg
            if isinstance(node, ast.keyword | ast.arg)
            else None
        )
        if name in NAMES and rel not in NAMES[name]:
            add(node, f"uses {name}")
        elif name in NAMES and isinstance(node, ast.Attribute | ast.Name | ast.Constant):
            if not _allowed_use(node, parent):
                add(node, f"reads {name} beyond passing it on")
        if isinstance(node, ast.Attribute) and node.attr == "denied":
            up = parent.get(node)
            if isinstance(up, ast.keyword):
                add(node, "hands denied to a caller")
            if isinstance(up, ast.Call) and node in up.args and _callee(up) in HIGHER | BY_NAME:
                add(node, "hands denied to a caller")
        if isinstance(node, ast.Call) and (
            _names_denied(node.func)
            or any(isinstance(n, ast.Name) and n.id in kept for n in ast.walk(node.func))
        ):
            add(node, "asks denied()")
        if isinstance(node, ast.Call) and _callee(node) in BY_NAME:
            if any(isinstance(a, ast.Constant) and a.value == "denied" for a in node.args):
                add(node, "fetches denied by name")
        if isinstance(node, ast.Call) and _callee(node) in {"import_module", "__import__"}:
            if any(not isinstance(a, ast.Constant) or "deny_" in str(a.value) for a in node.args):
                add(node, "imports a deny module by name")
        if isinstance(node, ast.Compare | ast.Call | ast.Subscript):
            if _clean_like(node, clean_names) and _carries(node, aliases):
                add(node, "tests evidence against CLEAN")
        if isinstance(node, ast.Match) and _carries(node.subject, aliases):
            if any(_clean_like(c.pattern, clean_names) for c in node.cases):
                add(node, "matches evidence against CLEAN")
        if isinstance(node, ast.MatchClass) and any(k in NAMES for k in node.kwd_attrs):
            add(node, "matches on a carrier")
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or "", *[a.name for a in node.names]]
        if any(p.startswith("deny_") for m in modules for p in m.split(".")) and rel not in IMPORTS:
            add(node, "imports a deny module")
    return found


def scan(root):
    """Every finding under ``root`` (a tree shaped like ``src/``), exempt files skipped."""
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel not in EXEMPT:
            out += _findings(ast.parse(path.read_text(encoding="utf-8")), rel)
    return out


def stale(root, names, imports):
    """Table entries the tree no longer needs (the table must shrink with the code)."""
    out = []
    for rel in sorted({r for files in names.values() for r in files} | imports):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        idents = {
            n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else n.arg
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute | ast.Name | ast.keyword | ast.arg)
        }
        out += [f"{rel}: {k}" for k, files in names.items() if rel in files and k not in idents]
        has = any(
            isinstance(n, ast.Import | ast.ImportFrom)
            and any(
                p.startswith("deny_")
                for m in [getattr(n, "module", "") or "", *[a.name for a in n.names]]
                for p in m.split(".")
            )
            for n in ast.walk(tree)
        )
        if rel in imports and not has:
            out.append(f"{rel}: deny_ import")
    return out


def test_the_real_tree_has_no_reader_outside_the_tables():
    assert scan(SRC) == [], FIX


def test_every_table_entry_is_still_used():
    assert stale(SRC, NAMES, IMPORTS) == [], "drop these entries from the tables"


def test_the_exempt_files_exist():
    assert all((SRC / rel).is_file() for rel in EXEMPT), (
        "a renamed file would hand its exemption on"
    )
