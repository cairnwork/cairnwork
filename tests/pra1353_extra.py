"""Blackbox, policy and receipt probes for the #1353 baseline."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pra1353_probe import blackbox_classification, declaration, repo, row, verify


def _c6():
    from cairnwork import blackbox, blackbox_judge
    from cairnwork.blackbox_exec import BlackboxRun

    rows = []
    for name, value in (("true", True), ("zero", 0), ("empty", "")):
        raw = json.dumps(
            {"checks": {"add": {"file": "/co/calc.py", "cases": {"one": {"ok": value}}}}}
        ).encode()
        run = BlackboxRun(raw, 0, False, 0, (), (), Path("/tmp/run"), "/co", "p", True)
        spec = blackbox.parse(
            json.dumps(
                {
                    "salt": "ab" * 32,
                    "checks": [
                        {
                            "id": "add",
                            "module": "calc",
                            "function": "add",
                            "cases": [{"id": "one", "args": [2, 3], "expected": value}],
                        }
                    ],
                }
            ).encode()
        )
        (verdict,) = blackbox_judge.judge_run(spec, "c" * 40, run)
        rows.append(
            row(
                "C6",
                f"low-entropy-{name}",
                "blackbox_judge.judge_run",
                verdict.reason,
                "PASS",
                "VERIFIED_WRONG" if "PASS" in verdict.reason else "REFUSED",
            )
        )
    for name, value in (("correct", True), ("wrong", False)):
        raw = json.dumps(
            {"checks": {"add": {"file": "/co/calc.py", "cases": {"one": {"ok": value}}}}}
        ).encode()
        run = BlackboxRun(raw, 0, False, 0, (), (), Path("/tmp/run"), "/co", "p", True)
        payload = {
            "salt": "ab" * 32,
            "checks": [
                {
                    "id": "add",
                    "module": "calc",
                    "function": "add",
                    "cases": [{"id": "one", "args": [2, 3], "expected": True}],
                }
            ],
        }
        spec = blackbox.parse(json.dumps(payload).encode())
        (verdict,) = blackbox_judge.judge_run(spec, "c" * 40, run)
        rows.append(
            row(
                "CONTROL",
                f"owner-{name}",
                "blackbox_judge.judge_run",
                verdict.reason,
                "blackbox:add: PASS [matched 1/1] low-entropy"
                if value
                else "blackbox:add: MISMATCH [matched 0/1] low-entropy",
                "HONEST_VERIFIED" if value else "HONEST_FAILED",
                "owner mode: direct judge only; matched count is observable",
            )
        )
    return rows


def _c9(decl):
    from dataclasses import replace

    from cairnwork import blackbox, blackbox_judge
    from cairnwork.blackbox_exec import BlackboxRun

    rows = []
    for name, changes in (("timeout", {"timed_out": True}), ("no-account", {"account": False})):
        raw = json.dumps(
            {"checks": {"add": {"file": "/co/calc.py", "cases": {"one": {"ok": True}}}}}
        ).encode()
        run = BlackboxRun(raw, 0, False, 0, (), (), Path("/tmp/run"), "/co", "p", True)
        (verdict,) = blackbox_judge.judge_run(
            blackbox.load(decl), "c" * 40, replace(run, **changes)
        )
        rows.append(
            row(
                "C9",
                name,
                "blackbox_judge.judge_run",
                verdict.reason,
                "VERIFICATION_INCOMPLETE",
                "NOT_RUN" if name == "no-account" else "REFUSED",
            )
        )
    return rows


def _c7(state, decl):
    from cairnwork import verify_cli
    from cairnwork.intake import Intake
    from cairnwork.pipeline import TaskRejected

    rows = []
    try:
        raw_result = verify_cli._run(
            Intake(Path("/no-such-intake")),
            SimpleNamespace(
                require_protection=True,
                blackbox=None,
                verifier=(),
                repo_path=".",
                base="0" * 40,
                candidate="1" * 40,
                allowed_file=["src/calc.py"],
                test_commit="",
                import_path=[],
            ),
        )
    except TaskRejected as exc:
        raw_result = exc.code
    rows.append(
        row(
            "C7",
            "a-no-blackbox",
            "verify_cli._run --require-protection",
            raw_result,
            "UNPROTECTED",
            "REFUSED",
        )
    )
    with TemporaryDirectory(prefix="cw1353-policy-") as td:
        p = Path(td)
        st = p / "state"
        st.mkdir(mode=0o700)
        st.chmod(0o700)
        (st / "config.json").write_text('{"verifier_account":false}')
        (st / "ACCEPTING").touch()
        d = declaration(p / "decl.json")
        cases = (
            (
                "b-one-tampered-pytest",
                (
                    "import pytest\n"
                    "for target in (pytest.Function,): "
                    "setattr(target, 'runtest', lambda self: None)\n"
                    "def add(a,b): return a+b\n"
                ),
                "from calc import add\ndef test_deferred(): assert add(2,3)==6\n",
            ),
        )
        for name, source, testsrc in cases:
            root = p / name
            base, cand = repo(root, source, testsrc)
            result = verify(root, st, base, cand, d, strict=True)
            status = result.get("status", result.get("error", "FAILED"))
            rows.append(
                row(
                    "C7",
                    name,
                    "verify_cli run --require-protection",
                    result,
                    "NOT_RUN",
                    blackbox_classification(status, result.get("reason", "")),
                    result.get("reason", ""),
                )
            )
    return rows


@cache
def extra_rows():
    with TemporaryDirectory(prefix="cw1353-extra-") as td:
        parent = Path(td).resolve()
        st = parent / "state"
        st.mkdir(mode=0o700)
        st.chmod(0o700)
        (st / "config.json").write_text('{"verifier_account":false}')
        (st / "ACCEPTING").touch()
        d = declaration(parent / "decl.json")
        path = Path(__file__).with_name("pra1353_c10.py")
        spec = importlib.util.spec_from_file_location("pra1353_c10", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["pra1353_c10"] = module
        spec.loader.exec_module(module)
        receipt_spec = importlib.util.spec_from_file_location(
            "pra1353_receipt", Path(__file__).with_name("pra1353_receipt.py")
        )
        assert receipt_spec is not None and receipt_spec.loader is not None
        receipt = importlib.util.module_from_spec(receipt_spec)
        sys.modules["pra1353_receipt"] = receipt
        receipt_spec.loader.exec_module(receipt)
        return _c6() + _c7(st, d) + _c9(d) + module.c10_rows() + receipt.c8_rows(parent)
