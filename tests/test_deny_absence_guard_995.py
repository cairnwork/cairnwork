"""#995 step 2: no verdict reads the deny channel, and nothing reads a missing line as "clear".

Deny evidence proves a denial, never its absence (a busy host can lose the run's own line). The
tables below list every file under ``src/`` that may touch it today, and how. A new reader fails
here on purpose: ask ``Evidence.denial_of(operation, target)`` (DENIED or UNKNOWN) and add the file
to the tables with a reason, in the change that lets a verdict read the channel.
The scan is syntactic: it cannot follow a value through every indirection, so it is a tripwire for
the usual shapes (and fails closed on the carrier names), not a proof. Not covered: a name built
at run time (getattr(ev, 'den' + 'ied')), a CLEAN set imported from another module, a helper
that tests its own argument, ``from x import *``, and presence-style reads that name no CLEAN
(``ev.state != 'DENIED'``, ``ev.detail == ''``). A carrier name may be passed as a keyword value,
held as a field, tested bare in an ``if`` that only logs, or put in an f-string that is only
logged; a bare call argument is refused because the callee could be len, bool or re.match.
"""

import ast
from pathlib import Path

import pytest

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


def _folded_clean(n):
    """'CL' + 'EAN': a concatenation of string literals that spells CLEAN."""
    parts = [
        m.value for m in ast.walk(n) if isinstance(m, ast.Constant) and isinstance(m.value, str)
    ]
    return isinstance(n, ast.BinOp) and _spells_clean("".join(parts))


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


def _allowed_use(node, parent):
    """A carrier name only as a keyword value, a field, a logging test, or logged text."""
    up = parent.get(node)
    if isinstance(up, ast.keyword) and up.value is node:
        return True
    if isinstance(up, ast.IfExp) and isinstance(parent.get(up), ast.keyword):
        return node is not up.test  # run(deny_denials=ev.denials if ev else frozenset())
    if isinstance(up, ast.AnnAssign) and up.target is node:
        return True
    if isinstance(up, ast.FormattedValue):  # only text built for a call (a log line)
        return isinstance(parent.get(parent.get(up)), ast.Call)
    # if run.deny_evidence: log(...): no else, and nothing in the body but calls
    return (
        isinstance(up, ast.If)
        and up.test is node
        and not up.orelse
        and all(isinstance(b, ast.Expr) for b in up.body)
    )


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


def _write(tmp_path, rel, code):
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(code, encoding="utf-8")


A = "cairnwork/a.py"
CATCH = [  # (file, code): must be reported
    ("cairnwork_sandbox/__init__.py", "x = ev.denied(1, 2)\n"),
    (A, "ok = ev.denied(op, target=t)\n"),
    (A, "ok = ev.denied(operation=o, target=t)\n"),
    (A, "ok = ev.denied(*pair)\n"),
    (A, "d = ev.denied\nok = d(o, t)\n"),
    (A, "ok = list(map(ev.denied, os_, ts))\n"),
    (A, "ok = partial(ev.denied, o)(t)\n"),
    (A, "ok = getattr(ev, 'denied')(1, 2)\n"),
    (A, "import operator\nok = operator.methodcaller('denied', 1, 2)(ev)\n"),
    ("cairnwork_attest/keys.py", "ok = ev.state == 'CLEAN'\n"),
    (A, "ok = ev.state in ('CLEAN',)\n"),
    (A, "ok = 'CLEAN' == ev.state\n"),
    (A, "C = 'CLEAN'\nok = ev.state == C\n"),
    (A, "OK = ('CLEAN',)\nok = ev.state in OK\n"),
    (A, "CLEAN: str = 'CLEAN'\nok = ev.state == CLEAN\n"),
    (A, "from x import CLEAN\nok = ev.state == CLEAN\n"),
    (A, "ok = ev.state == Status.CLEAN\n"),
    (A, "match ev.state:\n    case 'CLEAN':\n        pass\n"),
    (A, "match ev.state:\n    case Status.CLEAN:\n        pass\n"),
    (A, "s = ev.state\nok = s == 'CLEAN'\n"),
    (A, "def f(state):\n    return state == 'CLEAN'\n"),
    (A, "def f(state):\n    return state in ('CLEAN',)\n"),
    (A, "ok = (c := ev.state) == 'CLEAN'\n"),
    (A, "ok = ev.state in {'CLEAN': 1}\n"),
    (A, "r = {'CLEAN': 1}[ev.state]\n"),
    (A, "r = {'CLEAN': 1}.get(ev.state)\n"),
    (A, "ok = ev.state.find('CLEAN') >= 0\n"),
    (A, "ok = ev.state.__eq__('CLEAN')\n"),
    (A, "import operator\nok = operator.eq(ev.state, 'CLEAN')\n"),
    (A, "ok = 'CLEAN' in str(ev)\n"),
    (A, "ok = str(ev).split(':')[0] == 'CLEAN'\n"),
    (A, "ok = str(ev).startswith('CLEAN')\n"),
    (A, "ok = any(s == 'CLEAN' for s in [ev.state])\n"),
    (A, "ok = getattr(ev, 'state') == 'CLEAN'\n"),
    (A, "ok = x in ev.denials\n"),
    (A, "ok = run.deny_denials\n"),
    (A, "ok = hasattr(run, 'deny_denials')\n"),
    (A, "ok = vars(run)['deny_denials']\n"),
    (A, "from cairnwork import deny_watch\n"),
    (A, "from cairnwork.deny_sweep import sweep_stale\n"),
    (A, "import importlib\nm = importlib.import_module('cairnwork.deny_judge')\n"),
    (A, "m = __import__('cairnwork.deny_channel')\n"),
    ("cairnwork/workers/deny_judge.py", "ok = ev.denied(1, 2)\n"),  # same name, no exemption
    ("cairnwork/deny_watch.py", "ok = ev.state == 'CLEAN'\n"),  # widening EXEMPT goes red
    ("cairnwork/deny_sweep.py", "ok = ev.denied(1, 2)\n"),
    ("cairnwork/verifiers.py", "ok = ev.state == 'CLEAN'\n"),
    ("cairnwork/verifiers.py", "ok = (o, t) not in evidence.denials\n"),
    ("cairnwork/verifiers.py", "ok = (o, t) in run.deny_denials\n"),
    ("cairnwork/verifiers.py", "ok = (o, t) in run.deny_denials or ev.denied(o, t)\n"),
    ("cairnwork/verifier_run.py", "ok = (o, t) not in self.deny_denials\n"),
    ("cairnwork/verifier_run.py", "ok = 'DENIED' not in self.deny_evidence\n"),
    ("cairnwork/pipeline_judge.py", "ok = 'CLEAN' in run.deny_evidence\n"),
    ("cairnwork/pipeline_judge.py", "ok = run.deny_evidence.split(':')[0] == 'CLEAN'\n"),
    ("cairnwork/pipeline_judge.py", "ok = run.deny_evidence == 'CLEAN'\n"),
    ("cairnwork/pipeline_judge.py", "import re\nok = re.match('CLEAN', run.deny_evidence)\n"),
    ("cairnwork/pipeline_judge.py", "ok = run.deny_denials\n"),  # a log line, not the pairs
    ("cairnwork/verifier_exec.py", "ok = ev.state == 'CLEAN'\n"),
    ("cairnwork/pipeline_judge.py", "ok = 'DENIED' not in f'{run.deny_evidence}'\n"),
    ("cairnwork/pipeline_judge.py", "if run.deny_evidence:\n    pass\nelse:\n    passed = True\n"),
    ("cairnwork/pipeline_judge.py", "if run.deny_evidence:\n    ok = True\n"),
    ("cairnwork/pipeline_judge.py", "log(run.deny_evidence)\n"),
    ("cairnwork/verifier_run.py", "ok = 1 if self.deny_denials else 0\n"),
    ("cairnwork/workers/x.py", "x = run(deny_evidence=text, deny_denials=pairs)\n"),
    ("cairnwork/workers/x.py", "class R:\n    deny_denials: frozenset = frozenset()\n"),
    ("cairnwork/workers/x.py", "log(f'x {run.deny_evidence}')\n"),
    ("cairnwork/verifier_run.py", "x = run(deny_evidence=ev.denials)\n"),  # denials not its row
    (A, "fn: object = ev.denied\nok = fn(1, 2)\n"),
    (A, "ok = (d := ev.denied)(1, 2)\n"),
    (A, "fns = [ev.denied]\nok = fns[0](1, 2)\n"),
    (A, "ok = apply(cb=ev.denied)\n"),
    (A, "fn = ev.denied if ev else None\nok = fn(1, 2)\n"),
    (A, "ok = ev.__getattribute__('denied')(1, 2)\n"),
    (A, "ok = vars(type(ev))['denied'](ev, 1, 2)\n"),
    (A, "ok = ev.state.lower() == 'clean'\n"),
    (A, "ok = ev.state == 'CL' + 'EAN'\n"),
    (A, "ok = str(ev).startswith('clean')\n"),
    (A, "import importlib\nm = importlib.import_module('cairnwork.' + 'deny_judge')\n"),
]
PASS = [
    ("cairnwork/verifiers.py", "x = run(deny_denials=ev.denials)\n"),
    (
        "cairnwork/pipeline_judge.py",
        "if run.deny_evidence:\n    logs.append(f'x {run.deny_evidence}')\n",
    ),
    (A, "ok = job.state == 'RUNNING'\n"),
    (A, "ok = ev.state == 'UNAVAILABLE'\n"),
    (A, "ok = ev.state == 'DENIED'\n"),
    (A, "ok = sweep.get('wrapper') == 'CLEAN'\n"),  # src/cairnwork/workers/agy_receipts.py
    (A, "ok = verdict.denied\n"),  # an attribute read, not the evidence question
    (A, "x = candidate_denial(outcomes, verdict.denied, roots)\n"),
    ("cairnwork/verifiers.py", "x = run(deny_evidence=text, deny_denials=pairs)\n"),
    ("cairnwork/verifiers.py", "x = run(deny_denials=ev.denials if ev else frozenset())\n"),
    ("cairnwork/verifier_run.py", "class R:\n    deny_denials: frozenset = frozenset()\n"),
    ("cairnwork/verifier_run.py", "class R:\n    deny_evidence: str = ''\n"),
    ("cairnwork/pipeline_judge.py", "if run.deny_evidence:\n    log(f'x {run.deny_evidence}')\n"),
    ("cairnwork/pipeline_judge.py", "log(f'x {run.deny_evidence}')\n"),
    (
        "cairnwork/verifier_exec.py",
        "from cairnwork import deny_channel\nwith deny_channel.watch(d) as s:\n    pass\n",
    ),
]


@pytest.mark.parametrize("rel, code", CATCH)
def test_a_reader_is_refused(tmp_path, rel, code):
    _write(tmp_path, rel, code)
    assert scan(tmp_path), code


@pytest.mark.parametrize("rel, code", PASS)
def test_an_honest_line_is_let_through(tmp_path, rel, code):
    _write(tmp_path, rel, code)
    assert scan(tmp_path) == [], code


def test_the_stale_check_fails_on_an_entry_the_code_dropped(tmp_path):
    _write(tmp_path, "cairnwork/verifiers.py", "x = run(deny_evidence=text)\n")
    _write(tmp_path, "cairnwork/verifier_exec.py", "x = 1\n")
    names = {"deny_evidence": {"cairnwork/verifiers.py"}, "denials": {"cairnwork/verifiers.py"}}
    got = stale(tmp_path, names, {"cairnwork/verifier_exec.py"})
    assert got == ["cairnwork/verifier_exec.py: deny_ import", "cairnwork/verifiers.py: denials"]


def test_the_stale_check_passes_when_every_entry_is_used(tmp_path):
    _write(tmp_path, "cairnwork/verifiers.py", "x = run(deny_evidence=text)\n")
    _write(tmp_path, "cairnwork/verifier_exec.py", "from cairnwork import deny_channel\n")
    names = {"deny_evidence": {"cairnwork/verifiers.py"}}
    assert stale(tmp_path, names, {"cairnwork/verifier_exec.py"}) == []
