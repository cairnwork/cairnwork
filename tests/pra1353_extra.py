"""Blackbox, policy and receipt probes for the #1353 baseline."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

from pra1353_launch import judge, launch_route
from pra1353_probe import blackbox_classification, declaration, repo, row, verify


def _c6():
    rows = []
    for name, value in (("true", True), ("zero", 0), ("empty", "")):
        reason = judge(_spec(value), _raw(value))
        rows.append(
            row(
                "C6",
                f"low-entropy-{name}",
                "blackbox_judge.judge_run",
                reason,
                "PASS",
                "VERIFIED_WRONG" if "PASS" in reason else "REFUSED",
            )
        )
    for name, value in (("correct", True), ("wrong", False)):
        reason = judge(_spec(True), _raw(value))
        rows.append(
            row(
                "CONTROL",
                f"owner-{name}",
                "blackbox_judge.judge_run",
                reason,
                "blackbox:add: PASS [matched 1/1] low-entropy"
                if value
                else "blackbox:add: MISMATCH [matched 0/1] low-entropy",
                "HONEST_VERIFIED" if value else "HONEST_FAILED",
                "owner mode: direct judge only; matched count is observable",
            )
        )
    return rows


def _raw(value):
    return json.dumps({"checks": {"add": {"file": "/co/calc.py", "cases": {"one": {"ok": value}}}}})


def _spec(expected):
    check = {"id": "add", "module": "calc", "function": "add"}
    case = {"id": "one", "args": [2, 3], "expected": expected}
    return json.dumps({"salt": "ab" * 32, "checks": [{**check, "cases": [case]}]})


def _c9(decl):
    rows = []
    for name, changes in (("timeout", {"timed_out": True}), ("no-account", {"account": False})):
        reason = judge(decl, _raw(True), changes)
        rows.append(
            row(
                "C9",
                name,
                "blackbox_judge.judge_run",
                reason,
                "VERIFICATION_INCOMPLETE",
                "NOT_RUN" if name == "no-account" else "REFUSED",
            )
        )
    return rows


def _c7(state, decl):
    rows = []
    raw_result = launch_route("c7_unprotected", {})["value"]
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
        # strict mode refuses one-case declarations (#1353 S4-2)
        d = declaration(p / "decl.json", second=True)
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
        c8 = parent / "c8"
        c8.mkdir()
        return (
            _c6()
            + _c7(st, d)
            + _c9(d)
            + module.c10_rows()
            + launch_route("c8_rows", {"parent": str(c8)})["rows"]
        )
