"""Strict blackbox declarations are single-use and do not leak case match counts."""

import json
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import blackbox_judge
from cairnwork.blackbox import Case, Check, Declaration
from cairnwork.blackbox_exec import _run_in_open
from cairnwork.blackbox_once import AlreadyExposed, consume, declaration_key
from cairnwork.verify_context import VerifyContext


def declared(salt="ab" * 32):
    return Declaration(
        salt,
        (Check("c", "calc", "add", (Case("x", "[]", "{}", "1"),)),),
    )


def test_key_omits_salt():
    first = declared()
    second = declared("cd" * 32)
    assert declaration_key(first) == declaration_key(second)


def test_claim_is_owner_only_and_existing_record_is_never_overwritten(tmp_path):
    root = tmp_path / "state" / "blackbox-exposed"
    key = declaration_key(declared())
    consume(root, key)
    record = root / f"{key}.used"
    record.write_bytes(b"existing record")
    original = record.read_bytes()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    with pytest.raises(AlreadyExposed):
        consume(root, key)
    assert record.read_bytes() == original


def test_symlink_root_is_refused_and_stale_temp_does_not_block(tmp_path):
    key = declaration_key(declared())
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        consume(link, key)
    store = tmp_path / "store"
    store.mkdir()
    (store / f".{key}.stale.tmp").write_text("stale")
    consume(store, key)


def test_strict_reason_hides_counts_but_non_strict_keeps_them(monkeypatch, tmp_path):
    declaration = declared()
    report = json.dumps(
        {"checks": {"c": {"file": "/checkout/calc.py", "cases": {"x": {"ok": 1}}}}}
    ).encode()
    run = SimpleNamespace(
        report=report,
        returncode=0,
        timed_out=False,
        survivors=0,
        mutated=(),
        forged=(),
        log_path=tmp_path / "log",
        checkout="/checkout",
        profile_sha256="profile",
        account=True,
    )
    monkeypatch.setattr(blackbox_judge, "run_blackbox", lambda *a, **k: run)
    strict = blackbox_judge.judge_blackbox(
        declaration, VerifyContext(Path("/r"), tmp_path), "a" * 40, "run", strict=True
    )[0][0]
    plain = blackbox_judge.judge_blackbox(
        declaration, VerifyContext(Path("/r"), tmp_path), "a" * 40, "run"
    )[0][0]
    assert "[matched" not in strict.reason
    assert "[matched 1/1]" in plain.reason
    assert strict.observation.artifact_id == plain.observation.artifact_id


def test_claim_is_before_execute_and_stops_other_candidate_and_salt(monkeypatch, tmp_path):
    from cairnwork import blackbox_exec

    declaration = declared()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    run_dir, temp = private / "run", private / "tmp"
    run_dir.mkdir()
    temp.mkdir()
    account: Any = SimpleNamespace(share_run=lambda *args: None)
    context = VerifyContext(Path("/repo"), tmp_path / "state" / "runs", account=account)
    backend = SimpleNamespace(
        policy=lambda **kwargs: SimpleNamespace(text="profile", sha256="profile-hash")
    )
    monkeypatch.setattr(
        blackbox_exec, "_prepare_run", lambda *args: (run_dir, private, temp, {}, None, None)
    )
    monkeypatch.setattr(blackbox_exec, "install_runner", lambda *args: None)
    monkeypatch.setattr(blackbox_exec.cairnwork_sandbox, "select", lambda: backend)
    monkeypatch.setattr(blackbox_exec, "_write_new", lambda *args: None)
    monkeypatch.setattr(blackbox_exec, "checkout_differences", lambda *args: ())
    monkeypatch.setattr(blackbox_exec, "bytecode_findings", lambda *args: ())
    calls = []
    monkeypatch.setattr(
        blackbox_exec, "_execute", lambda *args: calls.append(args) or (0, False, 0)
    )
    kwargs = (declaration, context, "python", "a" * 40, checkout, 30, (), private)
    claim_root = tmp_path / "state" / "blackbox-exposed"
    _run_in_open(*kwargs, claim_root)
    no_account = replace(context, account=None)  # strict without an account is still an exposure
    with pytest.raises(AlreadyExposed):
        _run_in_open(declaration, no_account, *kwargs[2:], claim_root)
    attempts = (
        ("a" * 40, "ab" * 32),
        ("b" * 40, "ab" * 32),
        ("c" * 40, "cd" * 32),
    )
    for commit, salt in attempts:
        with pytest.raises(AlreadyExposed):
            _run_in_open(
                *(declared(salt), kwargs[1], kwargs[2], commit, *kwargs[4:]),
                claim_root,
            )
    plain_state = tmp_path / "plain-state"
    plain_kwargs = (
        declaration,
        replace(context, run_root=plain_state / "runs"),
        *kwargs[2:],
    )
    _run_in_open(*plain_kwargs, None)
    _run_in_open(*plain_kwargs, None)
    plain_root = plain_state / "blackbox-exposed"
    assert len(calls) == 3 and not plain_root.exists()


def test_unavailable_account_precheck_does_not_consume(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from cairnwork import blackbox_exec
    from cairnwork.verifier_account import VerifierAccountError

    class Unavailable:
        user = "verifier"
        usable = False

        @contextmanager
        def exclusive(self, timeout):
            yield

        def available(self, python):
            return self.usable

        def require_idle(self):
            pass

        def scrub_stale_runs(self):
            pass

    state = tmp_path / "state"
    account: Any = Unavailable()
    account.run_root = state / "account-runs"
    context = VerifyContext(tmp_path / "repo", state / "runs", account=account, python="python")
    monkeypatch.setattr(
        blackbox_exec.pytest_config,
        "verifier_settings",
        lambda *args: SimpleNamespace(pythonpath=()),
    )
    sentinel = object()
    monkeypatch.setattr(blackbox_exec, "_run", lambda *args: sentinel)
    with pytest.raises(VerifierAccountError):
        blackbox_exec.run_blackbox(declared(), context, "a" * 40, "run", strict=True)
    key = declaration_key(declared())
    assert not (state / "blackbox-exposed" / f"{key}.used").exists()
    account.usable = True
    assert blackbox_exec.run_blackbox(declared(), context, "a" * 40, "run", strict=True) is sentinel


def test_used_declaration_reason_is_incomplete_and_hides_counts(monkeypatch, tmp_path):
    declaration = declared()

    def exposed(*args, **kwargs):
        raise AlreadyExposed

    monkeypatch.setattr(blackbox_judge, "run_blackbox", exposed)
    (verdict,), log = blackbox_judge.judge_blackbox(
        declaration, VerifyContext(Path("/r"), tmp_path), "a" * 40, "run", True
    )
    assert log is None and not verdict.observation.passed
    assert verdict.reason == "blackbox:c: VERIFICATION_INCOMPLETE: DECLARATION_ALREADY_EXPOSED"


@pytest.mark.sandbox
def test_pipeline_passes_strict_only_under_require_protection(state, project, monkeypatch):
    from test_blackbox_core import DECLARED, fixed
    from test_protection import run

    from cairnwork import pipeline_judge, protection

    seen = []

    def judge(*args, strict=False, **kwargs):
        seen.append(strict)
        return fixed(True)(*args, **kwargs)

    monkeypatch.setattr(pipeline_judge, "judge_blackbox", judge)
    run(state, project, blackbox=DECLARED, require_protection=protection.WEAKEST)
    run(state, project, blackbox=DECLARED)
    assert seen == [True, False]
