"""CLI failure drills for strict blackbox protection (#1353 S4)."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test_protection import FIX, VERIFY
from verify_helpers import CASES, base_of, candidate, declaration

from cairnwork import blackbox, blackbox_exec, p2p, pipeline_judge, verify_cli
from cairnwork.blackbox_once import declaration_key
from cairnwork.blackbox_runner import INPUT_NAME
from cairnwork.core_request import VerifierObservation
from cairnwork.intake import Intake
from cairnwork.notary import Verdict
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import VerifierRun
from cairnwork.workspace import create_attempt_clone


class _Account:
    """A verifier account stand-in: only the lock, the availability check and run_root matter."""

    user = "verifier"
    python = "python3"

    def __init__(self, usable, run_root=None):
        self.usable, self.run_root = usable, run_root

    @contextmanager
    def exclusive(self, timeout):
        yield

    def available(self, python):
        return self.usable

    def require_idle(self):
        pass

    def scrub_stale_runs(self):
        pass

    def share_run(self, *args):
        pass

    def remove(self, path):
        shutil.rmtree(path, ignore_errors=True)


def _verdict(task, attempt, base, candidate, passed):
    status = "VERIFIED" if passed else "FAILED_VERIFICATION"
    return Verdict(
        task, attempt, base, candidate, "sha256:" + "b" * 64, 200, status,
        "OK" if passed else "FAILED", passed, {},
    )  # fmt: skip


class _Notary:
    def __init__(self):
        self.verdict_value: Any = _verdict("", "", "", "", False)

    def notarize(self, task, attempt, spec, repo, base, candidate, observations, binding):
        passed = all(observation.passed for observation in observations)
        self.verdict_value = _verdict(task, attempt, base, candidate, passed)
        return self.verdict_value

    def verdict(self, *args, **kwargs):
        return self.verdict_value


def _fake_verifier_runs(ctx, candidate_commit, specs, run_id, **kwargs):
    spec = VerifierSpec("pytest-0", ("tests/test_calc.py",))
    return (
        VerifierRun(
            spec,
            0,
            False,
            (),
            Path(ctx.run_root) / "verifier.log",
            VerifierObservation("pytest-0", "pass", "sha256:" + "a" * 64, True),
            {"tests/test_calc.py::test_add": "passed"},
            ran=True,
            recorded=True,
        ),
    )


def _correct_run(timed_out):
    """Stand in for the sandboxed runner: write the right answers (so the blackbox check
    alone would PASS) and report timed_out. The strict path claims the declaration first."""

    def execute(argv, profile_path, checkout, env, timeout, log_path, account, backend):
        spec = json.loads((Path(profile_path).parent / "guard" / INPUT_NAME).read_text())
        cases = {case["id"]: {"ok": case["expected"]} for case in CASES}
        file = os.path.join(os.path.realpath(checkout), "src", "calc.py")
        Path(spec["out"]).write_text(
            json.dumps({"checks": {"add": {"file": file, "cases": cases}}})
        )
        return 0, timed_out, 0

    return execute


def _setup(monkeypatch, state, project, tmp_path, *, backend=True, account=None,
           timed_out=False):  # fmt: skip
    base = base_of(project)
    head = candidate(project, base, {"src/calc.py": FIX})
    declaration_path = declaration(tmp_path)
    key = declaration_key(blackbox.load(declaration_path))
    monkeypatch.setattr(verify_cli.cairnwork_sandbox, "platform_backend", lambda: None)
    monkeypatch.setattr(blackbox_exec, "checkout_differences", lambda *args: ())
    monkeypatch.setattr(blackbox_exec, "bytecode_findings", lambda *args: ())
    if backend:
        profile_backend = SimpleNamespace(
            name="test-backend",
            policy=lambda **kwargs: SimpleNamespace(text="profile", sha256="profile-sha"),
        )
        monkeypatch.setattr(blackbox_exec.cairnwork_sandbox, "select", lambda: profile_backend)
        monkeypatch.setattr(blackbox_exec, "_execute", _correct_run(timed_out))
    else:
        monkeypatch.setattr(blackbox_exec.cairnwork_sandbox, "select", lambda: None)
    monkeypatch.setattr(pipeline_judge, "run_verifiers", _fake_verifier_runs)
    monkeypatch.setattr(p2p, "run_verifiers", _fake_verifier_runs)
    fake_notary = _Notary()
    monkeypatch.setattr(Intake, "notary", property(lambda self: fake_notary))
    intake = Intake(state)
    if account is not None:
        monkeypatch.setattr(Intake, "verifier_account", lambda self: account)
        monkeypatch.setattr(verify_cli, "_intake", lambda args: intake)
        monkeypatch.setattr(verify_cli, "account_can_read", lambda *args: False)
    argv = [
        "--state",
        str(state),
        "run",
        "--repo-path",
        str(project),
        "--base",
        base,
        "--candidate",
        head,
        "--allowed-file",
        "src/calc.py",
        "--verifier",
        VERIFY,
        "--blackbox",
        str(declaration_path),
        "--require-protection",
    ]
    record = state / "blackbox-exposed" / f"{key}.used"
    return argv, record


def _owner_checkout(account, python, repo, commit, work, clone_id):
    return create_attempt_clone(repo, commit, work, clone_id).path


def _usable(monkeypatch, state, project, tmp_path, **options):
    """_setup under a usable verifier account: the only mode in which a blackbox run can pass,
    so a failure in the drills below comes from their own cause and not from the owner mode."""
    account: Any = _Account(True, tmp_path / "account-runs")
    made = _setup(monkeypatch, state, project, tmp_path, account=account, **options)
    monkeypatch.setattr("cairnwork.account_check.blocked", lambda *args: "")
    monkeypatch.setattr(blackbox_exec, "make_account_checkout", _owner_checkout)
    monkeypatch.setattr(blackbox_exec, "close_private", lambda *args: None)
    return made


def _invoke(argv, capsys):
    code = verify_cli.main(argv)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    result = json.loads(captured.out)
    assert "VERIFIED" not in output
    assert "error" not in result, result  # a real verdict, not INTERNAL
    assert "status" in result, result
    assert result.get("status") != "VERIFIED", result
    assert result.get("verdict") != "VERIFIED"
    return code, result


def test_timeout_fails_strict_cli_and_consumes_declaration(
    monkeypatch, state, project, tmp_path, capsys
):
    argv, record = _usable(monkeypatch, state, project, tmp_path, timed_out=True)
    code, result = _invoke(argv, capsys)
    assert code != 0
    assert result["status"] != "VERIFIED", result
    assert record.is_file(), result


def test_unavailable_account_fails_before_consuming_declaration(
    monkeypatch, state, project, tmp_path, capsys
):
    account = _Account(False)
    monkeypatch.setattr("cairnwork.account_check.blocked", lambda *args: "unavailable")
    argv, record = _setup(monkeypatch, state, project, tmp_path, account=account)
    code, result = _invoke(argv, capsys)
    assert code != 0
    assert result.get("status") == "UNCOMPARABLE", result
    assert not record.exists()


def test_missing_sandbox_profile_fails_without_consuming_declaration(
    monkeypatch, state, project, tmp_path, capsys
):
    argv, record = _usable(monkeypatch, state, project, tmp_path, backend=False)
    code, result = _invoke(argv, capsys)
    assert code != 0
    assert result["status"] != "VERIFIED", result
    assert not record.exists()


def test_core_exception_fails_strict_cli_after_consuming_declaration(
    monkeypatch, state, project, tmp_path, capsys
):
    argv, record = _usable(monkeypatch, state, project, tmp_path)
    monkeypatch.setattr(
        _Notary,
        "notarize",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Core unavailable")),
    )
    code = verify_cli.main(argv)
    result = json.loads(capsys.readouterr().out)
    assert code != 0
    assert result["error"] == "INTERNAL"
    assert record.is_file()


def test_positive_control_same_setup_without_timeout_is_verified(
    monkeypatch, state, project, tmp_path, capsys
):
    """Correct answers, no timeout, backend, usable account: the drills fail for their cause."""
    argv, record = _usable(monkeypatch, state, project, tmp_path)
    code = verify_cli.main(argv)
    result = json.loads(capsys.readouterr().out)
    assert code == 0, result
    assert "error" not in result, result
    assert result.get("status") == "VERIFIED", result
    assert record.is_file()
