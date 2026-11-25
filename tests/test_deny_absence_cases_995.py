"""#995 step 2: the cases the deny-evidence guard must refuse and must let through.

Each row is written into a temp tree shaped like ``src/`` and run through ``scan``; the rows are
what keeps a rule from being removed unnoticed (see test_deny_absence_guard_995 for the rules).
"""

import pytest
from test_deny_absence_guard_995 import scan, stale


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
    ("cairnwork/pipeline_judge.py", "ok = len(f'{run.deny_evidence}') == 0\n"),
    ("cairnwork/pipeline_judge.py", "ok = bool(f'{run.deny_evidence}')\n"),
    (
        "cairnwork/pipeline_judge.py",
        "import re\nok = re.search('DENIED', f'{run.deny_evidence}')\n",
    ),
    ("cairnwork/verifiers.py", "ok = re.search(pattern='DENIED', string=run.deny_evidence)\n"),
    ("cairnwork/verifier_run.py", "ok = str(object=self.deny_evidence) == ''\n"),
    ("cairnwork/verifiers.py", "ok = sorted([1], key=ev.denials)\n"),
    ("cairnwork/verifiers.py", "ok = dict(a=ev.denials)['a']\n"),
    ("cairnwork/verifiers.py", "ok = frozenset(iterable=ev.denials)\n"),
    ("cairnwork/pipeline_judge.py", "if run.deny_evidence:\n    state.mark(False)\n"),
    (
        "cairnwork/pipeline_judge.py",
        "if run.deny_evidence:\n    ok.__setitem__(0, False)\npassed = True\n",
    ),
    (A, "match run:\n    case Run(deny_evidence=''):\n        ok = True\n"),
    (A, "match run:\n    case Run(deny_denials=frozenset()):\n        ok = True\n"),
    (A, "ok = ev.state == 'CL' + 'E' + 'AN'\n"),
    ("cairnwork/verifiers.py", "x = run(k=1 if ev.denials else 0)\n"),
    (
        "cairnwork/pipeline_judge.py",
        "if run.deny_evidence:\n    log('x')\nelse:\n    passed = True\n",
    ),
    ("cairnwork/verifier_run.py", "held: str = self.deny_evidence\n"),
    ("cairnwork/verifier_run.py", "ok = getattr(self, 'deny_evidence') == ''\n"),
    ("cairnwork/verifier_run.py", "ok = vars(self)['deny_evidence']\n"),
    (A, "ok = str(ev).startswith('CLEAN:')\n"),
    (A, "from x import CLEAN as GOOD\nok = ev.state == GOOD\n"),
    (A, "GOOD: str = 'CLEAN'\nok = ev.state == GOOD\n"),
    (A, "if c:\n    y = ev.state\nx = y\nok = x == 'CLEAN'\n"),
    (A, "fn = getattr(ev, 'denied')\nok = fn(1, 2)\n"),
    (A, "ok = hasattr(ev, 'denied')\n"),
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


@pytest.mark.parametrize(
    "code",
    [
        "if run.deny_evidence:\n    logs.append('x')\n    ok = False\n",
        "if run.deny_evidence:\n    logs.append('x')\n    return\n",
    ],
)
def test_an_if_body_that_does_more_than_log_is_refused(tmp_path, code):
    _write(tmp_path, "cairnwork/pipeline_judge.py", code)
    assert scan(tmp_path), code


@pytest.mark.parametrize(
    "code",
    [
        "x = Foo(k=run.deny_evidence) == 1\n",
        "x = Foo(k=run.deny_evidence).value\n",
        "x = Foo(k=run.deny_evidence)[0]\n",
        "x = bool(Foo(k=run.deny_evidence))\n",
    ],
)
def test_a_builder_result_that_is_read_on_is_refused(tmp_path, code):
    _write(tmp_path, "cairnwork/verifiers.py", code)
    assert scan(tmp_path), code
