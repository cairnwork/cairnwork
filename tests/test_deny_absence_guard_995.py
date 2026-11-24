"""#995 step 2: no verdict reads the deny channel, and nothing reads a missing line as "clear".

Deny evidence proves a denial, never its absence (a busy host can lose the run's own line). The
tables below list every file under ``src/`` that may touch it today, and for what. A new reader
fails here on purpose: ask ``Evidence.denial_of(operation, target)`` (DENIED or UNKNOWN) and add
the file to the table with a reason, in the change that lets a verdict read the channel.
The scan is syntactic: it cannot follow a value through a variable, so it is a tripwire for the
usual shapes, not a proof.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
EXEMPT = {"cairnwork/deny_channel.py", "cairnwork/deny_judge.py"}  # the recorder and the judge
RECORDERS = {"cairnwork/verifier_exec.py", "cairnwork/verifiers.py", "cairnwork/verifier_run.py"}
CARRIERS = {"cairnwork/verifiers.py", "cairnwork/verifier_run.py"}  # held on the run, not hashed
NAMES = {  # name -> files that may use it: carriers and one log line, never a verdict
    "deny_denials": CARRIERS,
    "denials": {"cairnwork/verifiers.py"},
    "deny_evidence": CARRIERS | {"cairnwork/pipeline_judge.py"},  # pipeline_judge: logs only
    "denied": set(),  # called with (operation, target): answer through denial_of
}
IMPORTS = RECORDERS | {"cairnwork/pipeline_judge.py"}  # who may import a deny_* module
FIX = (
    "deny evidence proves a denial, never its absence (#995): read it only through "
    "Evidence.denial_of(operation, target) (DENIED or UNKNOWN), and add the file to the tables "
    "in tests/test_deny_absence_guard_995.py with the reason"
)


def _is_clean(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and "CLEAN" in node.value


def _mentions_state(node):
    return any(
        (isinstance(n, ast.Attribute) and n.attr == "state")
        or (isinstance(n, ast.Constant) and n.value == "state")
        for n in ast.walk(node)
    )


def _findings(tree, rel):
    """What in one parsed file reads deny evidence where it should not."""
    found = []
    clean_names = {  # NAME = "CLEAN" at any depth, so a comparison with NAME is a CLEAN one
        t.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and _is_clean(n.value)
        for t in n.targets
        if isinstance(t, ast.Name)
    }

    def named(node, name):
        allowed = NAMES[name]
        if rel not in allowed:
            found.append(f"{rel}:{node.lineno} uses {name}")

    for node in ast.walk(tree):
        names = (
            [node.attr]
            if isinstance(node, ast.Attribute)
            else [node.id]
            if isinstance(node, ast.Name)
            else [node.arg]
            if isinstance(node, ast.keyword | ast.arg)
            else []
        )
        for name in names:
            if name in NAMES and name != "denied":
                if rel not in NAMES[name]:
                    found.append(f"{rel}:{getattr(node, 'lineno', 0)} uses {name}")
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "denied" and len(node.args) >= 2:
                named(node, "denied")
            if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and arg.value in NAMES:
                    named(node, str(arg.value))
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr in ("startswith", "endswith")
                and node.args
                and _is_clean(node.args[0])
            ):
                found.append(f"{rel}:{node.lineno} tests a text for CLEAN")
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            clean = any(
                _is_clean(s)
                or (isinstance(s, ast.Name) and s.id in clean_names)
                or (isinstance(s, ast.Tuple | ast.List | ast.Set) and any(map(_is_clean, s.elts)))
                for s in sides
            )
            if clean and any(_mentions_state(s) for s in sides):
                found.append(f"{rel}:{node.lineno} compares a state with CLEAN")
        if isinstance(node, ast.MatchValue) and _is_clean(node.value):
            found.append(f"{rel}:{node.lineno} matches CLEAN")
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or "", *[a.name for a in node.names]]
        if any(p.startswith("deny_") for m in modules for p in m.split(".")) and rel not in IMPORTS:
            found.append(f"{rel}:{getattr(node, 'lineno', 0)} imports a deny module")
    return found


def scan(root):
    """Every finding under ``root`` (a tree shaped like ``src/``), exempt files skipped."""
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel not in EXEMPT:
            out += _findings(ast.parse(path.read_text(encoding="utf-8")), rel)
    return out


def test_the_real_tree_has_no_reader_outside_the_tables():
    assert scan(SRC) == [], FIX


def test_every_table_entry_is_still_used():
    seen = {}
    for rel in sorted(IMPORTS | CARRIERS):
        text = (SRC / rel).read_text(encoding="utf-8")
        seen[rel] = text
    stale = [
        f"{rel}: {name}" for name, files in NAMES.items() for rel in files if name not in seen[rel]
    ] + [f"{rel}: deny_ import" for rel in IMPORTS if "deny_" not in seen[rel]]
    assert not stale, f"drop these entries from the tables: {stale}"


CATCH = [  # (file, line): must be reported
    ("cairnwork_sandbox/__init__.py", "x = ev.denied(1, 2)\n"),
    ("cairnwork_attest/keys.py", "ok = ev.state == 'CLEAN'\n"),
    ("cairnwork/a.py", "ok = ev.state is 'CLEAN'\n"),
    ("cairnwork/a.py", "ok = ev.state in ('CLEAN',)\n"),
    ("cairnwork/a.py", "ok = ev.state not in ('CLEAN', 'X')\n"),
    ("cairnwork/a.py", "ok = 'CLEAN' == ev.state\n"),
    ("cairnwork/a.py", "C = 'CLEAN'\nok = ev.state == C\n"),
    ("cairnwork/a.py", "match ev.state:\n    case 'CLEAN':\n        pass\n"),
    ("cairnwork/a.py", "ok = str(ev).startswith('CLEAN')\n"),
    ("cairnwork/a.py", "ok = getattr(ev, 'state') == 'CLEAN'\n"),
    ("cairnwork/a.py", "ok = getattr(ev, 'denied')(1, 2)\n"),
    ("cairnwork/a.py", "ok = ev.denied (1, 2)\n"),
    ("cairnwork/a.py", "ok = x in ev.denials\n"),
    ("cairnwork/a.py", "ok = run.deny_denials\n"),
    ("cairnwork/a.py", "from cairnwork import deny_watch\n"),
    ("cairnwork/a.py", "from cairnwork.deny_sweep import sweep_stale\n"),
    ("cairnwork/a.py", "import cairnwork.deny_judge\n"),
    ("cairnwork/workers/deny_judge.py", "ok = ev.denied(1, 2)\n"),  # same name, no exemption
    ("cairnwork/pipeline_judge.py", "ok = run.deny_denials\n"),  # a log line, not the pairs
]
PASS = [
    ("cairnwork/a.py", "ok = job.state == 'RUNNING'\n"),
    ("cairnwork/a.py", "ok = sweep.get('wrapper') == 'CLEAN'\n"),
    ("cairnwork/a.py", "ok = verdict.denied\n"),  # an attribute read, not the evidence question
    ("cairnwork/verifiers.py", "x = run(deny_evidence=text, deny_denials=pairs)\n"),
    ("cairnwork/pipeline_judge.py", "log(run.deny_evidence)\n"),
]


@pytest.mark.parametrize("rel, code", CATCH)
def test_a_reader_is_refused(tmp_path, rel, code):
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(code, encoding="utf-8")
    assert scan(tmp_path), code


@pytest.mark.parametrize("rel, code", PASS)
def test_an_honest_line_is_let_through(tmp_path, rel, code):
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(code, encoding="utf-8")
    assert scan(tmp_path) == [], code
