"""Measured production-route witnesses for Issue #1353."""

# The consumer-side digest literals are intentionally full SHA-256 values.
# ruff: noqa: E501

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

import pra1353_consume as consume
import pytest
from pra1353_probe import evasion_sources
from test_1039_1353_recipes import _matches
from test_1039_1353_static import _literal_observations

PINS = {
    "tests/attest/binding_helpers.py": "0b3eeb204048e1c5d41ef700338e6f71fb0eee74207f24978f953408c539d98e",
    "tests/attest/test_verify.py": "156795f6ee20d46c692b4a27e15ab36525b7ab7fce945722b39a3067c68a9b83",
    "tests/conftest.py": "a6f527a6ae989b47ef7cd10b270206d7e8c0e391064eac8eef03f9f3693219fd",
    "tests/pra1353_c10.py": "d8afb48a82564c10b786eb5b257559309bfc60649934d5ad429ac71245012fce",
    "tests/pra1353_consume.py": "2f9267d60676882868a81d7f3d27d858ce084f97380d50e92b64baf0aefb1d1f",
    "tests/pra1353_derive.py": "3e301ec245fba896b12798554ccb8391f2facaf6409567772882aad7592d534e",
    "tests/pra1353_extra.py": "1ef6960689f633fe9dfa411572091f2bd99e1e0f2435798a9ebe37def50c6d52",
    "tests/pra1353_probe.py": "7a07c4262671f87822622235bda5853a153bb92c146274d5c222b77862bb402f",
    "tests/pra1353_receipt.py": "d3121468ba6b46d12c466c1d79bd5bb49e832262a9fe36b49a36ff332bb054ae",
    "tests/pra1353_recipes.py": "87b8cf6e1d3c3618bc797ea9511b347e8d413dd752197ab5b368663efa2440b6",
    "tests/pra1353_runner.py": "e5c27294f78a4ad3a934cca4c502b1669291fcc9330340ca110574c05bfc1ba9",
    "tests/pra1353_gate.py": "6ba80619b8ba23846368eb921d130936e3fe8b2ad5e8d72a74ac610de78cdc72",
    "tests/pra1353_seal.py": "a0142da6d0499aa0263f1f1a7bba5f57e24840420a8fe51f15295e0f5e180c4e",
    "tests/xdist_groups.py": "08f332bef3401fd38390db53a60a158ebb78686d1ec33a7af1c9d4337692e673",
    "tests/test_1039_1353_recipes.py": "a11e647deef2d050dc75506595d9a889a08b70e84287153da420f56aa9f4a4d5",
    "tests/test_1039_1353_static.py": "0fc8b3428e5d312a396c4c74c4365933ea15577a0f762bee759d2342b82cf76d",
}

_ACCOUNT_GAP = consume._ACCOUNT_GAP


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
    if sys.platform == "linux" or consume.sandbox_available():
        # Linux runs the normal verifier without blackbox; the C2 pytest mutation passes.
        assert baseline.startswith("VERIFIED")
        assert all(r["observed"] == "VERIFIED" for r in got if r["class_id"] == "C2")
    else:
        assert baseline == "UNCOMPARABLE"
    mutant = next(r["observed"] for r in rows if r["class_id"] == "C2_MUTANT")
    assert mutant == (
        "FAILED" if sys.platform == "linux" or consume.sandbox_available() else "UNCOMPARABLE"
    )
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
    if sys.platform == "linux" or consume.sandbox_available():
        assert mutant != baseline
        # Linux: derived from src/cairnwork/reasons.py:112, not yet observed on CI
        assert c3 == ["FAILED"] * 4 and c3_mutants == ["FAILED", "FAILED"]
        assert all("checkout changed" in r["reason"].lower() for r in rows if r["class_id"] == "C3")
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
    if sys.platform == "linux" or consume.sandbox_available():
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
    if not (sys.platform == "linux" or consume.sandbox_available()):
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
    if sys.platform == "linux" or consume.sandbox_available():
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
