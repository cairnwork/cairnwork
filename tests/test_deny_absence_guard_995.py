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
because the callee could be len, bool or re.match. An alias of the ``denied`` attribute is
tracked through assignment, tuple, walrus and loop targets (#1797) and refused as a keyword value;
an attribute store (``self.x = ev.denied``) marks its base name (``self``); also followed: ``async for``, ``with``/``async with`` targets,
``except ... as``, ``match`` captures, ``f(*d)``, and ``.eperm_paths`` (the same dict as ``.denied``,
readable only in the files of EPERM_READERS). Not followed: a class attribute, a function return,
``map``/``partial`` positional hand-off, a default argument, ``vars()``/``__dict__``.
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
# Verdict.eperm_paths is the same dict as .denied under another name (#1781): the same rules apply,
# except in the files that may touch it. Only base_teeth.py reads it (it words BASE_ENV_ERROR).
EPERM_READERS = {
    "cairnwork/base_teeth.py": "words a BASE_ENV_ERROR; never decides a verdict",
    "cairnwork/verdict_record.py": "defines the property",
    "cairnwork/verifiers.py": "copies it into the run record, no judgment",
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


def _has_denied(node, names, attrs=("denied",)):
    """A deny attribute or a name already known to hold it, anywhere inside ``node``."""
    return any(
        (isinstance(n, ast.Attribute) and n.attr in attrs)
        or (isinstance(n, ast.Name) and n.id in names)
        for n in ast.walk(node)
    )


def _captures(match):
    """The names a ``match`` statement's patterns bind."""
    return [
        name
        for n in ast.walk(match)
        for name in (getattr(n, "name", None), getattr(n, "rest", None))
        if isinstance(n, ast.MatchAs | ast.MatchStar | ast.MatchMapping) and isinstance(name, str)
    ]


def _denied_aliases(tree, attrs=("denied",)):
    """Names bound from ``.denied`` or from another such name: assignment, walrus, ``for`` and
    ``async for``, ``with`` and ``async with`` targets, ``except ... as``, ``match`` captures."""
    found = set()
    while True:
        before = len(found)
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr)
                and n.value is not None
            ):
                pairs = [
                    (t, n.value) for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                ]
            elif isinstance(n, ast.For | ast.AsyncFor | ast.comprehension):
                pairs = [(n.target, n.iter)]
            elif isinstance(n, ast.ExceptHandler) and n.name and n.type is not None:
                pairs = [(ast.Name(id=n.name), n.type)]
            elif isinstance(n, ast.Match):
                pairs = [(ast.Name(id=c), n.subject) for c in _captures(n)]
            elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                pairs = [(n.optional_vars, n.context_expr)]
            else:
                continue
            for target, value in pairs:
                if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
                    if len(target.elts) == len(value.elts):  # a, b = x, y: pair them up
                        pairs += list(zip(target.elts, value.elts, strict=True))
                        continue
                if _has_denied(value, found, attrs):
                    found.update(_targets(target))
        if len(found) == before:
            return found


def _findings(tree, rel):
    """What in one parsed file reads deny evidence where it should not."""
    found = []
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    clean_names, aliases = _clean_names(tree), _aliases(tree)
    attrs = ("denied",) if rel in EPERM_READERS else ("denied", "eperm_paths")
    kept = _denied_aliases(tree, attrs)  # d = ev.denied ... d(o, t); e = d; fns = [ev.denied]

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
        if isinstance(node, ast.keyword) and kept and _has_denied(node.value, kept, attrs):
            add(node.value, "hands denied to a caller")  # #1797: an alias as a keyword value
        if isinstance(node, ast.Call) and kept:  # f(*d): a spread reads it too
            if any(
                isinstance(a, ast.Starred) and _has_denied(a.value, kept, attrs) for a in node.args
            ):
                add(node, "hands denied to a caller")
        if rel not in EPERM_READERS and (
            (isinstance(node, ast.Attribute) and node.attr == "eperm_paths")
            or (isinstance(node, ast.Constant) and node.value == "eperm_paths")
        ):
            add(node, "reads eperm_paths (the denied dict) outside its table")
        if isinstance(node, ast.Attribute) and node.attr in attrs:
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
            if any(isinstance(a, ast.Constant) and a.value in attrs for a in node.args):
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


def _flags(source):
    return [f for f in _findings(ast.parse(source), "cairnwork/x.py") if "denied" in f]


MUST_BLOCK = [  # #1797: an alias of the deny-evidence attribute read as a keyword value
    "d = ev.denied\nf(x=d)",
    "d = ev.denied\ne = d\nf(x=e)",  # alias of an alias
    "a, b = ev.denied, 1\nf(x=a)",  # tuple target
    "d = ev.denied if ok else None\nf(x=d)",
    "d = [ev.denied]\nf(x=d[0])",  # held in a list, read by index
    "d = {'k': ev.denied}\nf(x=d['k'])",
    "(d := ev.denied)\nf(x=d)",
    "for d in (ev.denied,):\n    f(x=d)",
    "d = ev.denied\nf(x=d if ok else None)",  # inside a conditional value
    "d = ev.denied\nf(x=(d))",
    "d = ev.denied\nf(x=lambda o, t: d(o, t))",
    "d = ev.denied\nf(x=[d])",  # inside a container value
    "d = ev.denied\ne = (d,)\nf(x=e)",
    "d = ev.denied\nf(**{'x': d})",  # splatted into keywords
    "f(x=v.eperm_paths)",  # the same dict under another name
    "d = v.eperm_paths\nf(x=d)",
    "x = v.eperm_paths",  # outside the table, any read
    "async def g():\n    async for d in src(ev.denied):\n        f(x=d)",
    "async def g():\n    async with hold(ev.denied) as d:\n        f(x=d)",
    "try:\n    pass\nexcept ev.denied as d:\n    f(x=d)",
    "match ev.denied:\n    case d:\n        f(x=d)",
    "d = ev.denied\nf(*d)",
    "d = {}\nd |= ev.denied\nf(x=d)",  # augmented assignment
    "d = []\nd += [ev.denied]\nf(x=d[0])",
    "with hold(ev.denied) as d:\n    f(x=d)",  # a with target
    "e = d\nd = ev.denied\nf(x=e)",  # the alias is written before its source
]
MUST_PASS = [  # today's legal shapes: no alias, or never a keyword value
    "f(ev.denial_of('open', '/x'))",
    "d = ev.denied\nf(d)",  # positional (verifiers.py hands the report plugin's record on so)
    "d = other.value\nf(x=d)",  # an alias of something else
    "f(denied=3)",  # a keyword that is merely named denied
    "x = 1\nd = ev.denied\ny = x\nf(k=y)",  # y never came from the attribute
    "a, b = ev.denied, 1\nf(x=b)",  # the other half of a tuple pair
    "d = 0\nd += 1\nf(x=d)",  # an augmented assignment of something else
    "f(denied=other)",
    "f(x=v.setup_denied)",
    "f(x=ev.denial_of('open', p))",
]


def test_an_alias_of_denied_read_as_a_keyword_value_is_found():
    missed = [s for s in MUST_BLOCK if not _flags(s)]
    assert missed == []


def test_the_legal_shapes_stay_unflagged():
    wrongly = [s for s in MUST_PASS if _flags(s)]
    assert wrongly == []


def test_an_augmented_or_with_alias_is_found():
    for source in (s for s in MUST_BLOCK if "|=" in s or "+=" in s or s.startswith("with")):
        assert _flags(source), source


def test_an_alias_bound_before_its_source_is_found():
    assert _flags("e = d\nd = ev.denied\nf(x=e)")


def test_eperm_paths_is_read_only_where_the_table_says():
    src = "run = rec\nif (path := getattr(run, 'eperm_paths', {}).get(nodeid)):\n    pass\n"
    assert not [f for f in _findings(ast.parse(src), "cairnwork/base_teeth.py") if "denied" in f]
    assert _flags(src)  # the same line anywhere else is refused
    assert all((SRC / rel).is_file() for rel in EPERM_READERS)


def test_async_and_with_and_match_aliases_are_found():
    for source in MUST_BLOCK:
        if "async" in source or "except" in source or "match" in source or "*d" in source:
            assert _flags(source), source
