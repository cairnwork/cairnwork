"""#303 blackbox declarations beyond plain functions: methods and call sequences, expected raises,
floats with a tolerance. The whole worker may be the candidate's, so each kind is tested with a
correct and a wrong implementation, a malicious return value, a malicious report and a forged run:
only the owner's comparison, on answers that never enter the run, may decide a pass."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cairnwork import blackbox, blackbox_judge
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.blackbox_runner import MODULE, install_runner

SALT = "ab" * 32
ACCOUNT = """\
class Account:
    def __init__(self, owner, balance=0):
        self.owner, self.balance = owner, balance

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError(f"bad amount {amount}")
        self.balance += amount
        return self.balance
"""
MEAN = """\
def mean(values, *, scale=1):
    if not values:
        raise ValueError("no values")
    return scale * sum(values) / len(values)
"""
RAISES = {"type": "ValueError", "message": "no values"}
TOL = {"rel_tol": 1e-9, "abs_tol": 1e-12}


def calls(*steps, init=None):
    case = {"id": "c1", "calls": list(steps)}
    return case | ({"init": init} if init else {})


ACCOUNT_CHECK = {
    "id": "acct", "module": "calc", "class": "Account",
    "cases": [
        calls({"method": "deposit", "args": [5], "expected": 15},
              {"method": "deposit", "args": [-1], "raises": {"type": "ValueError",
                                                             "message": "bad amount -1"}},
              {"method": "deposit", "args": [], "kwargs": {"amount": 2}, "expected": 17},
              init={"args": ["ann"], "kwargs": {"balance": 10}}),
    ],
}  # fmt: skip
MEAN_CHECK = {
    "id": "mean", "module": "calc", "function": "mean",
    "cases": [
        {"id": "third", "args": [[1, 1, 2]], "kwargs": {"scale": 0.5}, "expected": 2 / 3,
         "tolerance": TOL},
        {"id": "empty", "args": [[]], "raises": RAISES},
        {"id": "pair", "args": [[0.1, 0.2]], "expected": 0.15, "tolerance": TOL},  # gets 0.15...02
    ],
}  # fmt: skip


def declare(*checks):
    return blackbox.parse(json.dumps({"salt": SALT, "checks": list(checks)}).encode())


def run_source(tmp_path: Path, source: str, *checks):
    """The real runner on source as calc.py, minus the sandbox, judged as a clean account run."""
    checkout, guard = tmp_path / "co", tmp_path / "guard"
    checkout.mkdir()
    guard.mkdir()
    (checkout / "calc.py").write_text(source)
    declared = declare(*checks)
    out, real = tmp_path / "out.json", os.path.realpath(checkout)
    install_runner(guard, declared.runner_input([real], str(out)))
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(guard), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-P", "-m", MODULE], cwd=checkout, env=env, check=False)
    run = BlackboxRun(out.read_bytes() if out.exists() else None, done.returncode, False, 0, (), (),
                      tmp_path / "log", real, "sha256", True)  # fmt: skip
    report = json.loads(run.report or b"null")
    return [v.reason for v in blackbox_judge.judge_run(declared, "c" * 40, run)], report


def mean_source(body: str) -> str:
    return f"def mean(values, *, scale=1):\n{body}\n"


# --- correct and wrong implementations -------------------------------------------------------


def test_a_correct_class_and_function_pass(tmp_path):
    reasons, report = run_source(tmp_path, ACCOUNT + MEAN, ACCOUNT_CHECK, MEAN_CHECK)
    assert reasons == ["blackbox:acct: PASS [matched 1/1]", "blackbox:mean: PASS [matched 3/3]"]
    steps = report["checks"]["acct"]["cases"]["c1"]["steps"]
    assert steps[1] == {"error": "ValueError", "message": "bad amount -1"}  # and the next ran
    assert report["checks"]["mean"]["cases"]["third"] == {"ok": {"$float": (2 / 3).hex()}}


@pytest.mark.parametrize(
    "source",
    [
        ACCOUNT.replace("self.balance += amount", "self.balance = amount"),  # forgets state
        ACCOUNT.replace("if amount <= 0:", "if amount < -1:"),  # no raise for -1
        ACCOUNT.replace("raise ValueError", "raise TypeError"),
        ACCOUNT.replace('f"bad amount {amount}"', '"bad amount"'),
        ACCOUNT.replace("= owner, balance", "= owner, 0"),  # ignores the constructor's balance
    ],
    ids=["no-state", "no-raise", "wrong-type", "wrong-message", "wrong-init"],
)
def test_a_wrong_class_is_a_mismatch(tmp_path, source):
    (reason,), _ = run_source(tmp_path, source, ACCOUNT_CHECK)
    assert reason == "blackbox:acct: MISMATCH [matched 0/1]"


def test_a_raising_constructor_is_a_mismatch(tmp_path):
    source = ACCOUNT.replace("self.owner, self.balance", "raise KeyError(owner)\n        x, y")
    (reason,), report = run_source(tmp_path, source, ACCOUNT_CHECK)
    assert report["checks"]["acct"]["cases"]["c1"] == {"init": {"error": "KeyError",
                                                                "message": "'ann'"}}  # fmt: skip
    assert reason == "blackbox:acct: MISMATCH [matched 0/1]"


@pytest.mark.parametrize(
    "body,matched",
    [
        ("    return scale * sum(values) / len(values) + 1e-6", 0),  # outside; [] divides by 0
        ("    return round(scale * sum(values) / len(values), 3)", 1),
        ("    if not values:\n        raise ValueError('none')\n    return 2 / 3", 1),
        ("    if not values:\n        raise LookupError('no values')\n    return 1", 0),
        ("    return 0 if not values else scale * sum(values) / len(values)", 2),
    ],
    ids=["off", "rounded", "wrong-message", "wrong-type", "no-raise"],
)
def test_a_wrong_float_or_raise_is_a_mismatch(tmp_path, body, matched):
    (reason,), _ = run_source(tmp_path, mean_source(body), MEAN_CHECK)
    assert reason == f"blackbox:mean: MISMATCH [matched {matched}/3]"


def test_float_arguments_arrive_exactly(tmp_path):
    given = [0.1, {"k": [1e-300]}]
    case = {"id": "c1", "args": given, "expected": given, "tolerance": {"abs_tol": 0}}
    check = {"id": "echo", "module": "calc", "function": "echo", "cases": [case]}
    source = "def echo(a, b):\n    assert type(a) is float and a == 0.1\n    return [a, b]\n"
    (reason,), _ = run_source(tmp_path, source, check)
    assert reason == "blackbox:echo: PASS [matched 1/1]"


# --- malicious return values: the candidate's objects never reach a comparison ---------------

MALICIOUS = {
    "always-eq": "class _V:\n    def __eq__(self, other):\n        return True\n",
    "str-subclass": "class _V(str):\n    def __eq__(self, other):\n        return True\n",
    "float-subclass": "class _V(float):\n    def __eq__(self, other):\n        return True\n",
    "int-subclass": "class _V(int):\n    def __eq__(self, other):\n        return True\n",
    "dict-subclass": "class _V(dict):\n    def __eq__(self, other):\n        return True\n",
}


@pytest.mark.parametrize("kind", sorted(MALICIOUS))
def test_a_value_that_equals_everything_is_a_mismatch(tmp_path, kind):
    """A runner that compared (result == expected) would pass all of these."""
    source = MALICIOUS[kind] + "def mean(values, *, scale=1):\n    return _V()\n"
    source += "class Account:\n    def __init__(self, *a, **k):\n        pass\n"
    source += "    def deposit(self, amount=0):\n        return _V()\n"
    reasons, report = run_source(tmp_path, source, MEAN_CHECK, ACCOUNT_CHECK)
    assert reasons == ["blackbox:mean: MISMATCH [matched 0/3]",
                       "blackbox:acct: MISMATCH [matched 0/1]"]  # fmt: skip
    assert report["checks"]["mean"]["cases"]["third"]["error"].startswith("UNSERIALIZABLE:")


def test_a_forged_exception_name_is_not_the_builtin(tmp_path):
    source = (
        "class ValueError(Exception):\n    pass\nValueError.__module__ = 'builtins'\n"
        "def mean(values, *, scale=1):\n    raise ValueError('no values')\n"
    )
    (reason,), report = run_source(tmp_path, source, MEAN_CHECK)
    assert report["checks"]["mean"]["cases"]["empty"]["error"] == "builtins.ValueError"
    assert reason == "blackbox:mean: MISMATCH [matched 0/3]"


def test_an_exception_whose_message_raises_has_no_message(tmp_path):
    source = (
        "class E(ValueError):\n    def __str__(self):\n        raise RuntimeError\n"
        "def mean(values, *, scale=1):\n    raise E()\n"
    )
    (reason,), report = run_source(tmp_path, source, MEAN_CHECK)
    assert report["checks"]["mean"]["cases"]["empty"] == {"error": "calc.E"}
    assert reason == "blackbox:mean: MISMATCH [matched 0/3]"


def test_a_dict_with_the_float_key_is_unserializable(tmp_path):
    source = "def mean(values, *, scale=1):\n    return {'$float': (2 / 3).hex()}\n"
    (reason,), report = run_source(tmp_path, source, MEAN_CHECK)
    assert report["checks"]["mean"]["cases"]["third"] == {"error": "UNSERIALIZABLE:dict"}
    assert reason == "blackbox:mean: MISMATCH [matched 0/3]"


# --- a forged run: the candidate takes the runner over and writes the report itself -----------

FORGE = """\
import json, os, sys
_main = getattr(sys.modules.get('__main__'), '__file__', '') or ''
if _main.endswith('_cairnwork_blackbox_runner.py'):
    with open(os.path.join(os.path.dirname(_main), 'blackbox-input.json')) as _h:
        _spec = json.load(_h)
    def _guess(given):  # an answer that reached the run would be copied, else a guess
        if 'expected' in given:
            return {'ok': given['expected']}
        if 'raises' in given:
            return {'error': given['raises']['type'], 'message': given['raises'].get('message')}
        return GUESS
    _checks = {}
    for _c in _spec['checks']:
        _cases = {}
        for _k in _c['cases']:
            _cases[_k['id']] = ({'steps': [_guess(s) for s in _k['calls']]} if 'calls' in _k
                                else _guess(_k))
        _checks[_c['id']] = {'file': __file__, 'cases': _cases}
    with open(_spec['out'], 'w') as _h:
        json.dump({'checks': _checks}, _h)
    os.chmod(_spec['out'], 0o644)
    getattr(os, '_ex' + 'it')(0)
"""


@pytest.mark.parametrize(
    "guess",
    [
        {"ok": {"$float": (2 / 3).hex()}},
        {"error": "ValueError", "message": "bad amount"},
        {"ok": 15},
        {"ok": 15, "match": True},
        {"match": True},
        {"ok": {"$float": "inf"}},
    ],
    ids=["float", "raise", "int", "match-flag", "only-flag", "inf"],
)
def test_a_forged_report_matches_only_by_knowing_the_answers(tmp_path, guess):
    source = f"GUESS = {guess!r}\n" + FORGE.replace("GUESS = ", "") + ACCOUNT + MEAN
    reasons, _ = run_source(tmp_path, source, MEAN_CHECK, ACCOUNT_CHECK)
    assert all(" MISMATCH " in r or r.endswith(": BAD_OUTPUT") for r in reasons), reasons


def test_a_forged_report_with_the_answers_passes_which_is_the_limit(tmp_path):
    """Knowing the answers is the general limit: the forger here holds them (as constants)."""
    known = {
        "third": {"ok": {"$float": (2 / 3 + 1e-12).hex()}},
        "pair": {"ok": {"$float": "0x1.3333333333333p-3"}},
        "empty": {"error": "ValueError", "message": "no values"},
    }
    forged = (
        "import json, os, sys\n"
        "_main = getattr(sys.modules.get('__main__'), '__file__', '') or ''\n"
        "_spec = json.load(open(os.path.join(os.path.dirname(_main), 'blackbox-input.json')))\n"
        f"_out = {{'checks': {{'mean': {{'file': __file__, 'cases': {known!r}}}}}}}\n"
        "json.dump(_out, open(_spec['out'], 'w'))\n"
        "getattr(os, '_ex' + 'it')(0)\n"
    )
    (reason,), _ = run_source(tmp_path, forged, MEAN_CHECK)
    assert reason == "blackbox:mean: PASS [matched 3/3]"


# --- a malicious report, judged by the owner ---------------------------------------------------


def report_of(cases: dict, check="mean") -> bytes:
    return json.dumps({"checks": {check: {"file": "/co/calc.py", "cases": cases}}}).encode()


RUN = BlackboxRun(None, 0, False, 0, (), (), Path("/x/blackbox.log"), "/co", "p", True)
MEAN_OK = {"third": {"ok": {"$float": (2 / 3).hex()}}, "empty": RAISES | {"error": "ValueError"},
           "pair": {"ok": {"$float": (0.15).hex()}}}  # fmt: skip
MEAN_OK["empty"] = {"error": "ValueError", "message": "no values"}


def judged(cases: dict, check=MEAN_CHECK) -> str:
    run = replace(RUN, report=report_of(cases, check["id"]))
    (verdict,) = blackbox_judge.judge_run(declare(check), "c" * 40, run)
    return verdict.reason


HALF = {"id": "mean", "module": "calc", "function": "mean", "cases": [
    {"id": "one", "args": [[1]], "expected": 1.0, "tolerance": {"abs_tol": 0.5}},
    {"id": "two", "args": [[2]], "expected": [2.0], "tolerance": {"rel_tol": 0.1}},
]}  # fmt: skip


@pytest.mark.parametrize(
    "one,two,matched",
    [
        ({"ok": {"$float": "0x1.1p+0"}}, {"ok": [{"$float": "0x1.0p+1"}]}, 2),  # honest, close
        ({"ok": 1}, {"ok": [2]}, 0),  # ints within the tolerance: not floats
        ({"ok": True}, {"ok": [{"$float": "0x1.0p+1"}, 0]}, 0),
        ({"ok": {"$float": "0x1.9p+0"}}, {"ok": {"$float": "0x1.0p+1"}}, 0),  # off; not a list
    ],
    ids=["close", "ints", "bool-and-longer", "off-and-shape"],
)
def test_only_a_float_is_compared_within_the_tolerance(one, two, matched):
    want = f"blackbox:mean: {'PASS' if matched == 2 else 'MISMATCH'} [matched {matched}/2]"
    assert judged({"one": one, "two": two}, HALF) == want


def test_the_honest_report_passes():
    assert judged(MEAN_OK) == "blackbox:mean: PASS [matched 3/3]"


@pytest.mark.parametrize(
    "third",
    [
        {"ok": 1},  # an int where a float is declared
        {"ok": True},
        {"ok": (2 / 3).hex()},  # a hex string, not a tag
        {"ok": {"$float": "nan"}},
        {"ok": {"$float": "inf"}},
        {"ok": {"$float": (2 / 3).hex(), "x": 1}},
        {"ok": [{"$float": (2 / 3).hex()}]},
        {"error": "ValueError", "message": "no values"},
    ],
    ids=["int", "bool", "hex-string", "nan", "inf", "extra-key", "list", "raise"],
)
def test_a_malicious_float_report_is_a_mismatch(third):
    assert judged(MEAN_OK | {"third": third}) == "blackbox:mean: MISMATCH [matched 2/3]"


@pytest.mark.parametrize(
    "change",
    [
        {"third": {"ok": {"$float": "not hex"}}},
        {"third": {"ok": {"$float": "0x1p99999"}}},
        {"empty": {"error": "ValueError", "message": None}},
        {"empty": {"error": 1}},
        {"empty": {"error": "ValueError", "message": "no values", "match": True}},
        {"third": {"ok": 1, "match": True}},
        {"third": {"match": True}},
    ],
    ids=["bad-hex", "overflow", "null-message", "int-error", "match-flag", "ok-flag", "flag"],
)
def test_a_malformed_report_is_bad_output(change):
    assert judged(MEAN_OK | change).startswith("blackbox:mean: VERIFICATION_INCOMPLETE: BAD_OUTPUT")


@pytest.mark.parametrize(
    "c1,code",
    [
        (
            {"steps": [{"ok": 15}, {"error": "ValueError", "message": "bad amount -1"}]},
            "BAD_OUTPUT",
        ),
        ({"steps": [{"ok": 15}] * 4}, "BAD_OUTPUT"),
        ({"steps": "all"}, "BAD_OUTPUT"),
        ({"init": {"ok": 1}}, "BAD_OUTPUT"),
        ({"init": {"error": "KeyError"}, "steps": []}, "BAD_OUTPUT"),
        ({"ok": 17}, "BAD_OUTPUT"),
        ({"init": {"error": "KeyError"}}, "MISMATCH [matched 0/1]"),
    ],
    ids=["short", "long", "not-list", "init-ok", "init-and-steps", "function-shape", "init-error"],
)
def test_a_class_report_must_have_one_outcome_per_call(c1, code):
    assert code in judged({"c1": c1}, ACCOUNT_CHECK)


def test_a_class_report_passes_only_when_every_call_matches():
    steps = [{"ok": 15}, {"error": "ValueError", "message": "bad amount -1"}, {"ok": 17}]
    assert judged({"c1": {"steps": steps}}, ACCOUNT_CHECK) == "blackbox:acct: PASS [matched 1/1]"
    steps[1] = {"error": "ValueError"}
    assert (
        judged({"c1": {"steps": steps}}, ACCOUNT_CHECK) == "blackbox:acct: MISMATCH [matched 0/1]"
    )
