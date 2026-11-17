"""Blackbox, policy and receipt probes for the #1353 baseline."""

from __future__ import annotations

import importlib.util
import json
import types
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from _w1353_probe import (
    blackbox_classification,
    blackbox_result,
    declaration,
    evasion_sources,
    repo,
    row,
    verify,
)

import cairnwork.blackbox_judge as blackbox_judge
import cairnwork.verify_cli as verify_cli
from cairnwork import blackbox
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.intake import Intake
from cairnwork.pipeline import TaskRejected

_CACHE = {}


def cli_route(*args):
    try:
        return verify_cli._run(*args)
    except TaskRejected as exc:
        return exc.code


def _normal_row(cid, n, root, state, base, candidate):
    result = verify(root, state, base, candidate)
    status = result.get("status", result.get("error", "FAILED"))
    expected = (
        "HARNESS_TIMEOUT"
        if status == "TIMEOUT"
        else ("VERIFIED_WRONG" if status.startswith("VERIFIED") else "REFUSED")
    )
    name = f"verify-cli-{n}"
    if cid == "C1":
        return row(
            "C1", name, "verify_cli run", result, "VERIFIED", expected, result.get("reason") or ""
        )
    if cid == "C2":
        return row(
            "C2", name, "verify_cli run", result, "VERIFIED", expected, result.get("reason") or ""
        )
    if cid == "C3":
        return row(
            "C3", name, "verify_cli run", result, "VERIFIED", expected, result.get("reason") or ""
        )
    return row(
        "C4", name, "verify_cli run", result, "VERIFIED", expected, result.get("reason") or ""
    )


def _blackbox_row(cid, n, root, state, base, candidate, decl):
    result = verify(root, state, base, candidate, decl)
    status, reason = blackbox_result(result)
    observed_status = result.get("status", result.get("error", "FAILED"))
    expected = blackbox_classification(status, reason)
    name = f"blackbox-{n}"
    if cid == "C1":
        return row(
            "C1",
            name,
            "verify_cli run --blackbox",
            observed_status,
            "NOT_RUN",
            expected,
            reason,
        )
    if cid == "C2":
        return row(
            "C2",
            name,
            "verify_cli run --blackbox",
            observed_status,
            "NOT_RUN",
            expected,
            reason,
        )
    if cid == "C3":
        return row(
            "C3",
            name,
            "verify_cli run --blackbox",
            observed_status,
            "NOT_RUN",
            expected,
            reason,
        )
    return row(
        "C4", name, "verify_cli run --blackbox", observed_status, "NOT_RUN", expected, reason
    )


def _mutant_row(cid, parent, state, n, src, evasion, bad_test):
    root = parent / f"{cid}-pass-{n}"
    base, candidate = repo(root, src.replace(evasion, "pass"), bad_test)
    result = verify(root, state, base, candidate)
    status = result.get("status", result.get("error", "FAILED"))
    expected = (
        "HARNESS_TIMEOUT"
        if status == "TIMEOUT"
        else ("HONEST_VERIFIED" if status.startswith("VERIFIED") else "HONEST_FAILED")
    )
    name = f"evasion-{n}-replaced-by-pass" if cid == "C3" else "evasion-replaced-by-pass"
    if cid == "C1":
        return row(
            "C1_MUTANT",
            name,
            "verify_cli run",
            status,
            "FAILED",
            expected,
            result.get("reason") or "",
        )
    if cid == "C2":
        return row(
            "C2_MUTANT",
            name,
            "verify_cli run",
            status,
            "FAILED",
            expected,
            result.get("reason") or "",
        )
    if cid == "C3":
        return row(
            "C3_MUTANT",
            name,
            "verify_cli run",
            status,
            "FAILED",
            expected,
            result.get("reason") or "",
        )
    return row(
        "C4_MUTANT",
        name,
        "verify_cli run",
        status,
        "FAILED",
        expected,
        result.get("reason") or "",
    )


def c1_c4_rows(parent, state, decl):
    rows = []
    bad_test = "from calc import add\ndef test_deferred(): assert add(2, 3) == 6\n"
    for cid, variants in evasion_sources().items():
        for n, evasion in enumerate(variants, 1):
            root = parent / f"{cid}-{n}"
            src = "import sys\n" + evasion + "\ndef add(a,b): return a+b\n"
            base, candidate = repo(root, src, bad_test)
            rows.append(_normal_row(cid, n, root, state, base, candidate))
            rows.append(_blackbox_row(cid, n, root, state, base, candidate, decl))
            if (cid in {"C1", "C4", "C2"} and n == 1) or cid == "C3":
                rows.append(_mutant_row(cid, parent, state, n, src, evasion, bad_test))
    return rows


def _c6():

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
        (verdict_first,) = blackbox_judge.judge_run(spec, "c" * 40, run)
        rows.append(
            row(
                "C6",
                f"low-entropy-{name}",
                "blackbox_judge.judge_run",
                verdict_first.reason,
                "PASS",
                "VERIFIED_WRONG" if "PASS" in verdict_first.reason else "REFUSED",
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
        (verdict_second,) = blackbox_judge.judge_run(spec, "c" * 40, run)
        rows.append(
            row(
                "CONTROL",
                f"owner-{name}",
                "blackbox_judge.judge_run",
                verdict_second.reason,
                "blackbox:add: PASS [matched 1/1] low-entropy"
                if value
                else "blackbox:add: MISMATCH [matched 0/1] low-entropy",
                "HONEST_VERIFIED" if value else "HONEST_FAILED",
                "owner mode: direct judge only; matched count is observable",
            )
        )
    return rows


def _c9(decl):

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

    rows = []
    raw_result = cli_route(
        Intake(Path("/no-such-intake")),
        types.SimpleNamespace(
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
    with TemporaryDirectory(prefix="cw" + "1353-policy-") as td:
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
            reason = result.get("reason", "")
            rows.append(
                row(
                    "C7",
                    name,
                    "verify_cli run --require-protection",
                    status,
                    "NOT_RUN",
                    blackbox_classification(status, reason),
                    reason,
                )
            )
    return rows


def extra_rows():
    if "extra_rows" in _CACHE:
        return _CACHE["extra_rows"]
    with TemporaryDirectory(prefix="cw" + "1353-extra-") as td:
        parent = Path(td).resolve()
        st = parent / "state"
        st.mkdir(mode=0o700)
        st.chmod(0o700)
        (st / "config.json").write_text('{"verifier_account":false}')
        (st / "ACCEPTING").touch()
        d = declaration(parent / "decl.json")
        path = Path(__file__).with_name("_w" + "1353_c10.py")
        spec = importlib.util.spec_from_file_location("_w" + "1353_c10", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        receipt_spec = importlib.util.spec_from_file_location(
            "_w" + "1353_receipt", Path(__file__).with_name("_w" + "1353_receipt.py")
        )
        assert receipt_spec is not None and receipt_spec.loader is not None
        receipt = importlib.util.module_from_spec(receipt_spec)
        receipt_spec.loader.exec_module(receipt)
        result = _c6() + _c7(st, d) + _c9(d) + module.c10_rows() + receipt.c8_rows(parent)
        _CACHE["extra_rows"] = result
        return result
