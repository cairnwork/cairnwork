"""#303 blackbox checks, piece by piece: the declaration, the runner, and the owner's verdict."""

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
MANY = [{"id": f"c{i}", "args": [], "expected": 1} for i in range(blackbox.MAX_CASES + 1)]


def doc(**over):
    case = {"id": "c1", "args": [2, 3], "expected": 5}
    check = {"id": "add", "module": "calc", "function": "add", "cases": [case]}
    return {"salt": SALT, "checks": [check]} | over


def check_with(**over):
    return doc(checks=[doc()["checks"][0] | over])


def case_with(**over):
    return check_with(cases=[doc()["checks"][0]["cases"][0] | over])


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"salt": "' + SALT.encode() + b'", "salt": "x", "checks": []}',
        json.dumps(case_with(expected=1.5)).encode(),
        json.dumps(case_with(args=[float("nan")])).encode(),
        json.dumps({"checks": doc()["checks"]}).encode(),
        json.dumps(doc(salt="AB" * 32)).encode(),
        json.dumps(doc(salt="ab" * 16)).encode(),
        json.dumps(doc(checks=[])).encode(),
        json.dumps(doc(extra=1)).encode(),
        json.dumps(check_with(id="Add!")).encode(),
        json.dumps(doc(checks=doc()["checks"] * 2)).encode(),
        json.dumps(check_with(module="calc..x")).encode(),
        json.dumps(check_with(function="Calc.add")).encode(),
        json.dumps(check_with(cases=[])).encode(),
        json.dumps(check_with(cases=MANY)).encode(),
        json.dumps(check_with(cases=doc()["checks"][0]["cases"] * 2)).encode(),
        json.dumps(case_with(args={"a": 1})).encode(),
        json.dumps(case_with(kwargs=[1])).encode(),
        json.dumps(case_with(note="x")).encode(),
    ],
    ids=[
        "not-json", "duplicate-key", "float", "nan", "no-salt", "upper-salt", "short-salt",
        "no-checks", "extra-key", "bad-check-id", "duplicate-check", "bad-module",
        "dotted-function", "no-cases", "too-many-cases", "duplicate-case", "args-not-list",
        "kwargs-not-object", "extra-case-key",
    ],
)  # fmt: skip
def test_a_bad_declaration_is_rejected(raw):
    with pytest.raises(blackbox.BlackboxRejected):
        blackbox.parse(raw)


def test_an_oversized_declaration_is_rejected(tmp_path):
    path = tmp_path / "d.json"
    path.write_bytes(b" " * (blackbox.MAX_BYTES + 1))
    path.chmod(0o600)
    with pytest.raises(blackbox.BlackboxRejected, match="over"):
        blackbox.load(path)


def test_the_commitment_binds_everything_but_the_runner_never_sees_answers_or_salt():
    base = blackbox.parse(json.dumps(case_with(expected="SECRET-answer")).encode())
    assert (
        base.commitment()
        == blackbox.parse(json.dumps(case_with(expected="SECRET-answer")).encode()).commitment()
    )
    for other in (doc(salt="cd" * 32), case_with(expected="SECRET-other"), case_with(args=[2, 4])):
        assert blackbox.parse(json.dumps(other).encode()).commitment() != base.commitment()
    given = base.runner_input(["/co"], "/out")
    assert b"SECRET" not in given and SALT.encode() not in given and b"expected" not in given
    assert json.loads(given)["checks"][0]["cases"] == [{"id": "c1", "args": [2, 3], "kwargs": {}}]


def test_low_entropy_means_every_answer_is_a_boolean_or_null():
    guess = [{"id": "a", "args": [], "expected": True}, {"id": "b", "args": [], "expected": None}]
    assert blackbox.parse(json.dumps(check_with(cases=guess)).encode()).checks[0].low_entropy
    mixed = [*guess, {"id": "c", "args": [], "expected": 0}]
    assert not blackbox.parse(json.dumps(check_with(cases=mixed)).encode()).checks[0].low_entropy


MODULE_SOURCE = """\
def f(kind):
    if kind == "int":
        return 5
    if kind == "tuple":
        return (1, 2)
    if kind == "float":
        return 1.0
    if kind == "bool":
        return True
    if kind == "nested":
        return {"a": [1, None, "x"]}
    raise ValueError(kind)
"""
KINDS = {
    "int": 5,
    "tuple": [1, 2],
    "float": 1,
    "bool": 1,
    "nested": {"a": [1, None, "x"]},
    "boom": 0,
}


def run_runner(tmp_path, module="calc", function="f"):
    """The runner as the run starts it, minus the sandbox and the account."""
    checkout, guard = tmp_path / "co", tmp_path / "guard"
    checkout.mkdir()
    guard.mkdir()
    (checkout / "calc.py").write_text(MODULE_SOURCE)
    cases = [{"id": kind, "args": [kind], "expected": value} for kind, value in KINDS.items()]
    declared = blackbox.parse(
        json.dumps(check_with(module=module, function=function, cases=cases)).encode()
    )
    out = tmp_path / "out.json"
    real = os.path.realpath(checkout)
    install_runner(guard, declared.runner_input([real], str(out)))
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(guard), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-P", "-m", MODULE], cwd=checkout, env=env, check=False)
    run = BlackboxRun(out.read_bytes() if out.exists() else None, done.returncode, False, 0, (), (),
                      tmp_path / "log", real, "sha256", True)  # fmt: skip
    return declared, run


def test_the_runner_reports_values_by_exact_json_type(tmp_path):
    declared, run = run_runner(tmp_path)
    report = json.loads(run.report or b"")["checks"]["add"]
    assert report["file"] == os.path.join(run.checkout, "calc.py")
    assert report["cases"] == {
        "int": {"ok": 5}, "tuple": {"error": "UNSERIALIZABLE:tuple"},
        "float": {"error": "UNSERIALIZABLE:float"}, "bool": {"ok": True},
        "nested": {"ok": {"a": [1, None, "x"]}}, "boom": {"error": "ValueError"},
    }  # fmt: skip
    (verdict,) = blackbox_judge.judge_run(declared, "c" * 40, run)
    # only int and nested match: a tuple is not a list, True is not 1, a raise is no answer
    assert verdict.reason == "blackbox:add: MISMATCH [matched 2/6]"
    assert not verdict.observation.passed


def test_a_module_that_does_not_import_is_incomplete(tmp_path):
    declared, run = run_runner(tmp_path, module="nowhere")
    (verdict,) = blackbox_judge.judge_run(declared, "c" * 40, run)
    assert verdict.reason.startswith("blackbox:add: VERIFICATION_INCOMPLETE: NOT_IMPORTED:")


def report(cases, file="/co/calc.py"):
    return json.dumps({"checks": {"add": {"file": file, "cases": cases}}}).encode()


GOOD = BlackboxRun(
    report({"c1": {"ok": 5}}), 0, False, 0, (), (), Path("/x/blackbox.log"), "/co", "p", True
)


def verdict_of(run, declared=None):
    (verdict,) = blackbox_judge.judge_run(
        declared or blackbox.parse(json.dumps(doc()).encode()), "c" * 40, run
    )
    return verdict


def test_a_clean_matching_account_run_passes():
    verdict = verdict_of(GOOD)
    assert verdict.observation.passed and verdict.reason == "blackbox:add: PASS [matched 1/1]"
    assert verdict.observation.verifier_id == "blackbox:add"
    assert verdict.observation.artifact_id == "x/blackbox.log#add"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"profile_sha256": None}, "NO_SANDBOX"),
        ({"timed_out": True}, "TIMEOUT"),
        ({"survivors": 1}, "SURVIVORS"),
        ({"mutated": ("src/a.py",)}, "CHECKOUT_CHANGED"),
        ({"forged": ("x.pyc",)}, "FORGED_BYTECODE"),
        ({"returncode": 1}, "EXIT_1"),
        ({"report": None}, "NO_OUTPUT"),
        ({"report": b"[]"}, "BAD_OUTPUT"),
        ({"report": report({"c1": {"ok": 5}}).replace(b"5", b"5.0")}, "BAD_OUTPUT"),
        ({"report": report({})}, "BAD_OUTPUT"),
        ({"report": report({"c1": {"ok": 5}, "c2": {"ok": 5}})}, "BAD_OUTPUT"),
        ({"report": report({"c1": {"ok": 5, "error": "x"}})}, "BAD_OUTPUT"),
        ({"report": report({"c1": {"ok": 5}}, file="/elsewhere/calc.py")}, "NOT_FROM_CHECKOUT"),
        ({"report": report({"c1": {"ok": 5}}, file="/co-evil/calc.py")}, "NOT_FROM_CHECKOUT"),
        ({"account": False}, "BLACKBOX_NEEDS_VERIFIER_ACCOUNT"),
    ],
)  # fmt: skip
def test_anything_short_of_a_clean_account_run_is_incomplete(change, code):
    verdict = verdict_of(replace(GOOD, **change))
    assert not verdict.observation.passed
    assert verdict.reason.startswith(f"blackbox:add: {blackbox.INCOMPLETE}: {code}"), verdict.reason


@pytest.mark.parametrize("value", [6, "5", True, [5], None], ids=repr)
def test_a_wrong_value_is_a_mismatch(value):
    verdict = verdict_of(replace(GOOD, report=report({"c1": {"ok": value}})))
    assert (
        not verdict.observation.passed and verdict.reason == "blackbox:add: MISMATCH [matched 0/1]"
    )


def test_low_entropy_shows_in_the_artifact_id_and_reason():
    declared = blackbox.parse(json.dumps(case_with(expected=True)).encode())
    verdict = verdict_of(replace(GOOD, report=report({"c1": {"ok": True}})), declared)
    assert verdict.observation.passed and verdict.reason.endswith("low-entropy")
    assert verdict.observation.artifact_id.endswith("#add;low-entropy")


def test_a_run_that_cannot_be_made_is_incomplete(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no checkout")

    monkeypatch.setattr(blackbox_judge, "run_blackbox", broken)
    declared = blackbox.parse(json.dumps(doc()).encode())
    (verdict,), log = blackbox_judge.judge_blackbox(
        declared, Path("/r"), "c" * 40, Path("/x"), "a-v"
    )
    assert log is None and not verdict.observation.passed
    assert verdict.reason == "blackbox:add: VERIFICATION_INCOMPLETE: RUN_ERROR:OSError"


def test_recovery_finds_a_dead_runs_blackbox_dirs(tmp_path):
    from cairnwork.recovery import _run_dirs

    attempt = "a" + "1" * 16
    work, private = tmp_path / f"{attempt}-v-vbb.work", tmp_path / f"{attempt}-v-vbb.x1y2"
    for path in (work, private / "run"):
        path.mkdir(parents=True)
    works, privates, _ = _run_dirs(tmp_path, attempt)
    assert works == [work] and privates == [private / "run"]  # the log stays as evidence


def private_file(tmp_path, data: bytes, mode=0o600):
    path = tmp_path / "d.json"
    path.write_bytes(data)
    path.chmod(mode)
    return path


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_declaration_others_may_read_is_rejected(tmp_path, mode):
    with pytest.raises(blackbox.BlackboxRejected, match="mode 0600"):
        blackbox.load(private_file(tmp_path, json.dumps(doc()).encode(), mode))


def test_a_symlinked_declaration_is_rejected(tmp_path):
    target = private_file(tmp_path, json.dumps(doc()).encode())
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(blackbox.BlackboxRejected, match="cannot open"):
        blackbox.load(link)
    assert blackbox.load(target).checks[0].id == "add"


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(case_with(expected=123456789.987654321)).encode(),
        b'{"salt": "' + SALT.encode() + b'", "checks": [{"id": "add", "module": "calc", '
        b'"function": "add", "cases": [{"id": "c1", "args": [], '
        b'"expected": {"SECRET_KEY": 1, "SECRET_KEY": 2}}]}]}',
        json.dumps(check_with(module="SECRET module")).encode(),
        json.dumps(case_with(id="SECRET!")).encode(),
    ],
    ids=["float-answer", "duplicate-answer-key", "module", "case-id"],
)
def test_a_rejection_never_quotes_the_declaration(raw):
    with pytest.raises(blackbox.BlackboxRejected) as caught:
        blackbox.parse(raw)
    assert "SECRET" not in str(caught.value) and "987654" not in str(caught.value)


def test_a_deeply_nested_declaration_is_rejected():
    deep = b"[" * 100000 + b"]" * 100000
    raw = json.dumps(case_with(expected="X")).encode().replace(b'"X"', deep)
    with pytest.raises(blackbox.BlackboxRejected, match="nested"):
        blackbox.parse(raw)


def test_deeply_nested_output_is_bad_output():
    deep = b"[" * 100000 + b"]" * 100000
    verdict = verdict_of(replace(GOOD, report=report({"c1": {"ok": "X"}}).replace(b'"X"', deep)))
    assert verdict.reason.startswith("blackbox:add: VERIFICATION_INCOMPLETE: BAD_OUTPUT")


def test_a_judging_error_is_still_a_failed_observation_per_check(monkeypatch):
    def boom(*args):
        raise RuntimeError("anything")

    monkeypatch.setattr(blackbox_judge, "run_blackbox", lambda *a, **k: GOOD)
    monkeypatch.setattr(blackbox_judge, "judge_run", boom)
    declared = blackbox.parse(json.dumps(doc()).encode())
    (verdict,), log = blackbox_judge.judge_blackbox(
        declared, Path("/r"), "c" * 40, Path("/x"), "a-v"
    )
    assert log == GOOD.log_path and not verdict.observation.passed
    assert verdict.reason == "blackbox:add: VERIFICATION_INCOMPLETE: JUDGE_ERROR:RuntimeError"


class _Self:
    """Stands in for the account: runs the probe as this user, or a command that fails."""

    def __init__(self, fail=False):
        self.fail = fail

    def wrap(self, argv, profile):
        return ["/usr/bin/false"] if self.fail else argv


def test_the_account_probe_says_readable_unless_it_heard_private(tmp_path):
    from typing import Any

    from cairnwork.blackbox_exec import account_can_read

    readable = private_file(tmp_path, b"{}")
    me: Any = _Self()
    assert account_can_read(me, readable)
    assert not account_can_read(me, tmp_path / "missing.json")
    broken: Any = _Self(fail=True)
    assert account_can_read(broken, tmp_path / "missing.json")  # an error is never "private"


@pytest.mark.parametrize("readable", [True, False])
def test_the_cli_refuses_a_declaration_the_account_can_read(
    tmp_path, monkeypatch, capsys, readable
):
    """verify_cli is where the probe runs (a library caller of verify_candidate must probe too)."""
    from cairnwork import verify_cli
    from cairnwork.intake import Intake

    declared = private_file(tmp_path, json.dumps(doc()).encode())
    ran = []
    monkeypatch.setattr(Intake, "verifier_account", lambda self: "account")
    monkeypatch.setattr(verify_cli, "account_can_read", lambda account, path: readable)
    monkeypatch.setattr(
        verify_cli, "verify_candidate", lambda *a, **k: ran.append(1) or {"verified": True}
    )
    argv = ["--state", str(tmp_path), "run", "--repo-path", str(tmp_path), "--base", "b",
            "--candidate", "c", "--allowed-file", "f", "--verifier", "true",
            "--blackbox", str(declared)]  # fmt: skip
    code = verify_cli.main(argv)
    out = json.loads(capsys.readouterr().out)
    if readable:
        assert (code, out["error"], ran) == (2, blackbox.BAD, []), out
        assert "can read" in out["detail"]
    else:
        assert (code, ran) == (0, [1]), out


def test_the_output_is_read_only_from_a_small_regular_file(tmp_path):
    from cairnwork.blackbox_exec import _read

    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (blackbox.MAX_BYTES + 1))
    assert _read(real) == b"{}"
    assert _read(link) is None and _read(big) is None and _read(tmp_path / "none") is None


def test_the_run_reports_checkout_changes_and_forged_bytecode(repo, tmp_path, monkeypatch):
    from conftest import git

    from cairnwork import blackbox_exec

    monkeypatch.setattr(blackbox_exec, "checkout_differences", lambda *a: ("src/a.py",))
    monkeypatch.setattr(blackbox_exec, "bytecode_findings", lambda *a: ("x.pyc",))
    declared = blackbox.parse(json.dumps(doc()).encode())
    head = git(repo, "rev-parse", "HEAD")
    run = blackbox_exec.run_blackbox(declared, repo, head, tmp_path / "runs", "a1-v")
    assert run.mutated == ("src/a.py",) and run.forged == ("x.pyc",) and not run.account
