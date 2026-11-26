"""Measured production-route witnesses for Issue #1353."""

# The consumer-side digest literals are intentionally full SHA-256 values.
# ruff: noqa: E501

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pra1353_consume as consume
import pra1353_seal as seal
import pytest
from pra1353_probe import evasion_sources
from test_1039_1353_recipes import _matches
from test_1039_1353_static import _literal_observations

PINS = {
    "tests/attest/binding_helpers.py": "0b3eeb204048e1c5d41ef700338e6f71fb0eee74207f24978f953408c539d98e",
    "tests/attest/test_verify.py": "156795f6ee20d46c692b4a27e15ab36525b7ab7fce945722b39a3067c68a9b83",
    "tests/conftest.py": "a6f527a6ae989b47ef7cd10b270206d7e8c0e391064eac8eef03f9f3693219fd",
    "tests/pra1353_c10.py": "80975b94fec7d2dae6cd70d403bbe331805a182cd7c510b1fa4df3ff1a105ef0",
    "tests/pra1353_consume.py": "9f9524d859eb75ec7df7127319d45235fe681d2dededd064d4ee2c50d1e81750",
    "tests/pra1353_derive.py": "3e301ec245fba896b12798554ccb8391f2facaf6409567772882aad7592d534e",
    "tests/pra1353_extra.py": "f28c9d6acc84fa89649230b299499ca764e2e22d9aae88553813209f6fa853b5",
    "tests/pra1353_launch.py": "7e70f8b170af81aefad5a4b9b92604abf4ca7a520d50a08759964d4ef5dd6d25",
    "tests/pra1353_probe.py": "f9de25dee72f70f7e9c9033d3d5550593e6460bdadbeededbdacc6f6f7eda56a",
    "tests/pra1353_receipt.py": "d3121468ba6b46d12c466c1d79bd5bb49e832262a9fe36b49a36ff332bb054ae",
    "tests/pra1353_recipes.py": "87b8cf6e1d3c3618bc797ea9511b347e8d413dd752197ab5b368663efa2440b6",
    "tests/pra1353_runner.py": "e009c2b30f7cadd05ec5b990aaabded8c5bb9e8733f524e39011c4d2d02bc385",
    "tests/pra1353_gate.py": "88282f8e1f4312edfb9e30eb6925eeb899e76204f95ad0be94709feea8bd1dc4",
    "tests/pra1353_seal.py": "7a9a5378172308ad8b1f5cc3247d0fa4a2b83ab7d99b0f1868433c19715a0ec8",
    "tests/xdist_groups.py": "f25d2963ffb9125fd00da9d32823b841fdfa6b79fb1deac5c3907ed4347f02e9",
    "tests/test_1039_1353_recipes.py": "a11e647deef2d050dc75506595d9a889a08b70e84287153da420f56aa9f4a4d5",
    "tests/test_1039_1353_static.py": "0fc8b3428e5d312a396c4c74c4365933ea15577a0f762bee759d2342b82cf76d",
}

_ACCOUNT_GAP = consume._ACCOUNT_GAP


def _with_runner_witnesses(run):
    """A fake runner's report names the witnesses a real runner always loads (cw#1468)."""

    def call(args, **kwargs):
        done = run(args, **kwargs)
        try:
            report = json.loads(done.stdout)
        except (TypeError, ValueError):
            return done
        if done.returncode != 0 or not isinstance(report, dict):
            return done
        modules = report.get("modules")
        if not isinstance(modules, list):
            return done
        sealed = Path(args[4]).parents[1]
        seen = {m.get("name") for m in modules if isinstance(m, dict)}
        for name in seal.RUNNER_WITNESSES:
            if name not in seen:
                file = str(sealed / "tests" / f"{name}.py")
                modules.append(
                    {"name": name, "file": file, "origin": file, "loader": "SourceFileLoader"}
                )
        return subprocess.CompletedProcess(args, 0, json.dumps(report), done.stderr)

    return call


@pytest.fixture(autouse=True)
def fake_runner_reports_witnesses(monkeypatch):
    real = consume.launch

    def launch(pins, *, run=subprocess.run, **kwargs):
        if run is not subprocess.run:
            run = _with_runner_witnesses(run)
        return real(pins, run=run, **kwargs)

    monkeypatch.setattr(consume, "launch", launch)


def _sandbox_expected() -> bool:
    """#1488: True where the real sandbox runs (Linux, or a working Seatbelt). On macOS under
    CAIRNWORK_REQUIRE_SANDBOX=1 a failed Seatbelt probe fails the gate instead of taking the weaker
    UNCOMPARABLE branch."""
    if sys.platform == "linux" or consume.sandbox_available():
        return True
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1":
        pytest.fail("CAIRNWORK_REQUIRE_SANDBOX=1 but the Seatbelt probe failed: not UNCOMPARABLE")
    return False


@pytest.fixture(scope="session")
def broker_report():
    import os

    return consume.launch(
        PINS, source_repo=Path(os.environ.get("PRA1353_SOURCE_REPO", consume.ROOT))
    )


@pytest.fixture
def core_rows(broker_report):
    return [row for row in broker_report["rows"] if row["seq"] < 35]


@pytest.fixture
def extra_rows(broker_report):
    return [row for row in broker_report["rows"] if row["seq"] >= 35]


def test_c1_c4_evasions_are_runnable_and_live(core_rows):
    """C1-C4; each source compiles, imports, calls add, and its removal changes C2's verdict."""
    rows = core_rows
    got = [
        r
        for r in rows
        if r["class_id"] in {"C1", "C2", "C3", "C4"} and r["probe"].startswith("verify-cli")
    ]
    assert len(got) == 9
    sources = [source for variants in evasion_sources().values() for source in variants]
    for source in sources:
        with tempfile.TemporaryDirectory(prefix="cw1353-valid-") as cwd:
            (Path(cwd) / "tests").mkdir()
            (Path(cwd) / "src").mkdir()
            code = "import sys; sys.path[0:0]=['src']\n" + source
            code += "\ndef add(a,b): return a+b\nassert add(2,3)==5"
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert completed.returncode == 0, completed.stderr

    baseline = next(
        r["observed"] for r in got if r["class_id"] == "C2" and r["probe"] == "verify-cli-1"
    )
    if _sandbox_expected():
        # Linux runs the normal verifier without blackbox; the C2 pytest mutation passes.
        assert baseline.startswith("VERIFIED")
        assert all(r["observed"] == "VERIFIED" for r in got if r["class_id"] == "C2")
    else:
        assert baseline == "UNCOMPARABLE"
    mutant = next(r["observed"] for r in rows if r["class_id"] == "C2_MUTANT")
    assert mutant == ("FAILED" if _sandbox_expected() else "UNCOMPARABLE")
    c3_rows = [r for r in rows if r["class_id"] == "C3"]
    c3 = [r["observed"] for r in c3_rows]
    c3_mutants = [r["observed"] for r in rows if r["class_id"] == "C3_MUTANT"]
    c1 = [
        r["observed"] for r in rows if r["class_id"] == "C1" and r["probe"].startswith("verify-cli")
    ]
    c4 = [
        r["observed"] for r in rows if r["class_id"] == "C4" and r["probe"].startswith("verify-cli")
    ]
    other_mutants = [r["observed"] for r in rows if r["class_id"] in {"C1_MUTANT", "C4_MUTANT"}]
    if _sandbox_expected():
        assert mutant != baseline
        # Linux: derived from src/cairnwork/reasons.py:112, not yet observed on CI
        # Second evasion (a path insert reached through ``os.sys``) is now refused up front by the
        # candidate guard (#1572: any receiver of ``.path``), so its verify row reads REFUSED.
        assert c3[:2] == ["FAILED"] * 2 and c3[2] == "REFUSED" and c3[3] in {"REFUSED", "FAILED"}
        assert c3_mutants == ["FAILED", "FAILED"]
        assert all(
            "checkout changed" in r["reason"].lower() for r in c3_rows if r["observed"] == "FAILED"
        )
        # Linux: derived from src/cairnwork/reasons.py:141, not yet observed on CI
        assert all(
            "checkout changed" not in r["reason"].lower()
            and "pytest exit code 1" in r["reason"].lower()
            for r in rows
            if r["class_id"] == "C3_MUTANT"
        )
        # Linux: derived from src/cairnwork/verify_candidate.py:130-140, not yet observed on CI
        assert c1 == ["REFUSED"] * 3 and c4 == ["REFUSED"] * 2
        assert all(
            "pytest exit code 1" in r["reason"].lower()
            for r in rows
            if r["class_id"] in {"C1_MUTANT", "C2_MUTANT", "C4_MUTANT"}
        )
        assert other_mutants == ["FAILED", "FAILED"]
    else:
        # Darwin without Seatbelt: observed locally; base preflight prevents candidate execution.
        assert c1 == ["REFUSED"] * 3 and consume.c2_unavailable(rows) == ["UNCOMPARABLE"] * 2
        assert c3 == ["UNCOMPARABLE"] * 4 and c3_mutants == ["UNCOMPARABLE"] * 2
        assert c4 == ["REFUSED"] * 2
        assert other_mutants == ["UNCOMPARABLE", "UNCOMPARABLE"]


def test_observed_values_are_recomputed_from_raw_route_returns(broker_report):
    """C1-C10; every record stores the raw production return and derives observed from it."""
    rows = broker_report["rows"]
    for item in rows:
        assert item["route"] and "raw_return" in item
        raw = item["raw_return"]
        derived = (
            str(raw.get("status", raw.get("error", raw.get("code", raw))))
            if isinstance(raw, dict)
            else str(raw)
        )
        assert item["observed"] == derived
        assert isinstance(item["matches_spec"], bool)
        assert item["matches_spec"] == _matches(
            item["observed"], item["expected_on_main"], item["reason"]
        )
    assert all(not item["name"].startswith("_w1353_") for item in broker_report["modules"])
    witness_sources = Path(__file__).parent.glob("pra1353_*.py")
    assert all(not _literal_observations(path.read_text()) for path in witness_sources)
    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"insert", "append"}
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "path"
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: 2 process(es) outlived the run, or "
            "the process list was unreadable] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: timed out] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: items renamed after collection "
            "(1: tests/test_calc.py)] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: DATA_EXEC_REFUSED: declared test data "
            "executed (1: x)] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: process exit status 0 disagrees with the "
            "recorded session status (exit status 1, 1 failed)] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: log over the size cap] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: test report over the size cap] " + _ACCOUNT_GAP,
            "REFUSED",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION " + _ACCOUNT_GAP,
            "NOT_RUN",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION " + _ACCOUNT_GAP + " [protection: PASS [partial_macos]]",
            "NOT_RUN",
        ),
        (
            "FAILED",
            "CORE_FAILED_VERIFICATION [pytest-0: checkout changed "
            "(1: changed tests/test_calc.py)] "
            "[blackbox:add: VERIFICATION_INCOMPLETE: CHECKOUT_CHANGED]",
            "REFUSED",
        ),
        ("REFUSED", "CANDIDATE_SYS_PATH_MUTATION: 'src/calc.py': ...", "REFUSED"),
        ("TIMEOUT", "verify_cli exceeded 120 seconds", "HARNESS_TIMEOUT"),
    ],
)
def test_blackbox_classification_counts_only_account_gaps_as_not_run(status, reason, expected):
    assert consume.blackbox_classification(status, reason) == expected


def test_literal_observation_checker_accepts_current_probe_call_sites():
    sources = Path(__file__).parent.glob("pra1353_*.py")
    assert all(not _literal_observations(path.read_text()) for path in sources)


def test_candidate_routes_match_pinned_main_outputs(core_rows):
    """C1-C5; real verify_cli run outputs are pinned, including both known C2/C5 bypasses."""
    rows = core_rows
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        selected = [r for r in rows if r["class_id"] == cid]
        assert len(selected) >= 2
        assert all(r["observed"] for r in selected)
    c2 = [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]
    c2_blackbox = [r for r in rows if r["class_id"] == "C2" and r["probe"].startswith("blackbox-")]
    assert len(c2_blackbox) == 2
    if _sandbox_expected():
        assert all(r["classification"] == "NOT_RUN" for r in c2_blackbox)
    else:
        # Darwin without a usable Seatbelt (observed under Core's verifier, itself sandboxed): the
        # base probe cannot run, so the blackbox route fails closed before any account gap.
        assert [(r["observed"], r["classification"]) for r in c2_blackbox] == [
            ("UNCOMPARABLE", "REFUSED")
        ] * 2
        assert all(consume._NO_SANDBOX_BASE in r["reason"] for r in c2_blackbox)
    # Linux: C2 is observed on CI as VERIFIED.
    expected = ["VERIFIED", "VERIFIED"]
    if not _sandbox_expected():
        expected = ["UNCOMPARABLE", "UNCOMPARABLE"]
    assert c2 == expected
    # Linux: derived from src/cairnwork/verify_candidate.py:130-140, not yet observed on CI
    c5 = [r["observed"] for r in rows if r["class_id"] == "C5"]
    assert c5 == expected
    honest = [r for r in rows if r["class_id"] == "HONEST"]
    assert len(honest) == 10 and all(r["observed"] == expected[0] for r in honest)


def test_class_results_are_pinned(extra_rows):
    """C6-C10; raw current judge, policy, runner and receipt outcomes are pinned."""
    rows = extra_rows
    assert len([r for r in rows if r["class_id"] == "C6"]) >= 3
    controls = {r["probe"]: r["observed"] for r in rows if r["class_id"] == "CONTROL"}
    assert controls["owner-correct"] == "blackbox:add: PASS [matched 1/1] low-entropy"
    assert controls["owner-wrong"] == "blackbox:add: MISMATCH [matched 0/1] low-entropy"
    assert {r["probe"]: r["observed"] for r in rows if r["class_id"] == "C6"} == {
        "low-entropy-true": "blackbox:add: PASS [matched 1/1] low-entropy",
        "low-entropy-zero": "blackbox:add: PASS [matched 1/1]",
        "low-entropy-empty": "blackbox:add: PASS [matched 1/1]",
    }
    assert len([r for r in rows if r["class_id"] == "C7"]) == 3
    c7_b = next(r for r in rows if r["probe"] == "b-one-tampered-pytest")
    if _sandbox_expected():
        assert c7_b["classification"] == "NOT_RUN"
    else:  # as C2 above: observed under Core's verifier
        assert (c7_b["observed"], c7_b["classification"]) == ("UNCOMPARABLE", "REFUSED")
        assert consume._NO_SANDBOX_BASE in c7_b["reason"]
    if "BLACKBOX_NEEDS_VERIFIER_ACCOUNT" in c7_b["reason"]:
        assert "pytest exit code 1" not in c7_b["reason"]
    c8 = {r["probe"]: r["observed"] for r in rows if r["class_id"] == "C8"}
    assert c8 == {
        "valid": "OK",
        "head": "HEAD_MISMATCH",
        "base": "BASE_MISMATCH",
        "test_commit": "TEST_SOURCE_MISMATCH",
        "executed_tree": "TREE_MISMATCH",
        "blackbox_commitment": "BLACKBOX_COMMITMENT_MISMATCH",
        "blackbox_checks": "BLACKBOX_CHECKS_MISSING",
        "v0.1-under-protected-policy": "TEST_SOURCE_MISMATCH",
    }
    c9 = {r["probe"]: r["observed"] for r in rows if r["class_id"] == "C9"}
    assert c9 == {
        "timeout": "blackbox:add: VERIFICATION_INCOMPLETE: TIMEOUT",
        "no-account": (
            "blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 0/1]"
        ),
    }
    c10 = {r["probe"]: r for r in rows if r["class_id"] == "C10"}
    assert {name: r["observed"] for name, r in c10.items()} == {
        "runner-sniff-1": "blackbox:add: MISMATCH [matched 0/1]",
        "runner-sniff-2": "blackbox:add: PASS [matched 1/1]",
        "runner-sniff-3": "blackbox:add: MISMATCH [matched 0/1]",
    }
    assert "outside-runner return=-1" in c10["runner-sniff-1"]["reason"]
    assert "python -P -m _cairnwork_blackbox_runner" in c10["runner-sniff-1"]["reason"]
