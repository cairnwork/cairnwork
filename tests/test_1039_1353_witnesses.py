"""Measured production-route witnesses for Issue #1353."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests import _w1353_allow_round5_tests as round5
from tests import _w1353_allow_round6_tests as round6_pin_tests
from tests import _w1353_allow_round8_tests as round8_pin_tests
from tests._w1353_allow import check as allow_check
from tests._w1353_allow_cases import (
    EXECUTED_MUST_BLOCK,
    MUST_BLOCK,
    MUST_PASS,
    ROUND_FIVE_MUST_BLOCK,
    ROUND_FIVE_MUST_PASS,
    ROUND_FOUR_MUST_BLOCK,
    ROUND_THREE_MUST_BLOCK,
)
from tests._w1353_allow_round10_tests import *  # noqa: F403
from tests._w1353_allow_round13_tests import *  # noqa: F403
from tests._w1353_allow_round14_tests import *  # noqa: F403
from tests._w1353_allow_round14_tests import registered, thaw

r6 = round6_pin_tests
test_byte_edits_trip_pin_and_name_the_file = r6.test_byte_edits_trip_pin_and_name_the_file
test_every_trusted_helper_pin_entry_exists = r6.test_every_trusted_helper_pin_entry_exists
test_missing_pinned_file_is_a_mismatch = round6_pin_tests.test_missing_pinned_file_is_a_mismatch
test_new_pin_helper_passes_witness_grammar = (
    round6_pin_tests.test_new_pin_helper_passes_witness_grammar
)
test_path_spellings_do_not_skip_pin_check = (
    round6_pin_tests.test_path_spellings_do_not_skip_pin_check
)
test_round_six_mutations_trip_whole_module_pin = (
    round6_pin_tests.test_round_six_mutations_trip_whole_module_pin
)
test_trusted_helper_modules_match_reviewed_pins = (
    round6_pin_tests.test_trusted_helper_modules_match_reviewed_pins
)
test_one_file_edit_has_exactly_one_mismatching_path = (
    round6_pin_tests.test_one_file_edit_has_exactly_one_mismatching_path
)
test_w1353_namespace_has_no_shadow_directories = (
    round8_pin_tests.test_w1353_namespace_has_no_shadow_directories
)
test_w1353_shadow_directory_mutation_control = (
    round8_pin_tests.test_w1353_shadow_directory_mutation_control
)
test_w1353_pin_modules_import_from_their_reviewed_files = (
    round8_pin_tests.test_w1353_pin_modules_import_from_their_reviewed_files
)
test_w1353_module_origin_rejects_foreign_sys_modules_entry = (
    round8_pin_tests.test_w1353_module_origin_rejects_foreign_sys_modules_entry
)
test_w1353_witness_objects_come_from_pinned_modules = (
    round8_pin_tests.test_w1353_witness_objects_come_from_pinned_modules
)
test_witness_exposes_every_round_eight_guard = (
    round8_pin_tests.test_witness_exposes_every_round_eight_guard
)


def _load(name, *, register=True):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("_w1353_probe")
extra = _load("_w1353_extra", register=False)
_ACCOUNT_GAP = probe._ACCOUNT_GAP


def test_c1_c4_evasions_are_runnable_and_live():
    """C1-C4; each source compiles, imports, calls add, and its removal changes C2's verdict."""
    rows = registered(probe.core_rows())
    got = [
        r
        for r in rows
        if r["class_id"] in {"C1", "C2", "C3", "C4"} and r["probe"].startswith("verify-cli")
    ]
    assert len(got) == 9
    sources = [source for variants in probe.evasion_sources().values() for source in variants]
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
    if sys.platform == "linux" or probe.sandbox_available():
        # Linux runs the normal verifier without blackbox; the C2 pytest mutation passes.
        assert baseline.startswith("VERIFIED")
        assert all(r["observed"] == "VERIFIED" for r in got if r["class_id"] == "C2")
    else:
        assert baseline == "UNCOMPARABLE"
    mutant = next(r["observed"] for r in rows if r["class_id"] == "C2_MUTANT")
    assert mutant == (
        "FAILED" if sys.platform == "linux" or probe.sandbox_available() else "UNCOMPARABLE"
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
    if sys.platform == "linux" or probe.sandbox_available():
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
        assert c1 == ["REFUSED"] * 3 and probe.c2_unavailable(rows) == ["UNCOMPARABLE"] * 2
        assert c3 == ["UNCOMPARABLE"] * 4 and c3_mutants == ["UNCOMPARABLE"] * 2
        assert c4 == ["REFUSED"] * 2
        assert other_mutants == ["UNCOMPARABLE", "UNCOMPARABLE"]


def test_observed_values_are_recomputed_from_raw_route_returns():
    """C1-C10; every record stores the raw production return and derives observed from it."""
    rows = registered(probe.core_rows() + extra.extra_rows())
    for item in rows:
        assert item["route"] and "raw_return" in item
        raw = thaw(item["raw_return"])
        derived = (
            str(raw.get("status", raw.get("error", raw.get("code", raw))))
            if isinstance(raw, dict)
            else str(raw)
        )
        assert item["observed"] == derived
        assert isinstance(item["matches_spec"], bool)
    sources = [
        path
        for path in Path(__file__).parent.glob("_w1353_*.py")
        if not path.name.startswith("_w1353_allow")
    ]
    assert all(not allow_check(path.read_text(), str(path)) for path in sources)
    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"insert", "append"}
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "path"
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize("source", MUST_BLOCK)
def test_literal_observation_checker_rejects_constant_raw_values(source):
    assert allow_check(source)


@pytest.mark.parametrize(("case_id", "source"), EXECUTED_MUST_BLOCK.items())
def test_allow_checker_blocks_executed_round_two_bypasses(case_id, source):
    assert allow_check(source), case_id


@pytest.mark.parametrize(("case_id", "source"), ROUND_THREE_MUST_BLOCK.items())
def test_allow_checker_blocks_round_three_bypasses(case_id, source):
    assert allow_check(source), case_id


@pytest.mark.parametrize(("case_id", "source"), ROUND_FOUR_MUST_BLOCK.items())
def test_allow_checker_blocks_round_four_reachability_bypasses(case_id, source):
    assert allow_check(source), case_id


@pytest.mark.parametrize(("case_id", "source"), ROUND_FIVE_MUST_BLOCK.items())
def test_allow_checker_blocks_round_five_bypasses(case_id, source):
    round5.round_five_block(case_id, source)


@pytest.mark.parametrize("source", MUST_PASS)
def test_literal_observation_checker_allows_route_derived_raw_values(source):
    assert not allow_check(source)


@pytest.mark.parametrize("source", ROUND_FIVE_MUST_PASS)
def test_allow_checker_allows_non_lookup_w1353_string(source):
    round5.round_five_pass(source)


@pytest.mark.parametrize(
    ("case_id", "source", "mutation"),
    [
        ("b8_reduce_getattr", ROUND_FIVE_MUST_BLOCK["b8_reduce_getattr"], "builtin"),
        ("a10_sys_modules_escape", ROUND_FIVE_MUST_BLOCK["a10_sys_modules_escape"], "modules"),
        ("a15_unpinned_observed_sink", ROUND_FIVE_MUST_BLOCK["a15_unpinned_observed_sink"], "pin"),
    ],
)
def test_round_five_guard_mutations_are_live(case_id, source, mutation, monkeypatch):
    round5.round_five_mutation(case_id, source, mutation, monkeypatch)


def test_literal_observation_checker_accepts_current_probe_call_sites():
    sources = [
        path
        for path in Path(__file__).parent.glob("_w1353_*.py")
        if not path.name.startswith("_w1353_allow")
    ]
    findings = {str(path): allow_check(path.read_text(), str(path)) for path in sources}
    assert not any(findings.values()), findings


@pytest.mark.parametrize(("source", "mutation"), round5.LEGACY_MUTATIONS)
def test_guard_mutation_controls(source, mutation, monkeypatch):
    round5.legacy_mutation(source, mutation, monkeypatch)


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
    assert probe.blackbox_classification(status, reason) == expected


def test_candidate_routes_match_pinned_main_outputs():
    """C1-C5; real verify_cli run outputs are pinned, including both known C2/C5 bypasses."""
    rows = registered(probe.core_rows())
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        selected = [r for r in rows if r["class_id"] == cid]
        assert len(selected) >= 2
        assert all(r["observed"] for r in selected)
    c2 = [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]
    c2_blackbox = [r for r in rows if r["class_id"] == "C2" and r["probe"].startswith("blackbox-")]
    assert len(c2_blackbox) == 2
    if sys.platform == "linux" or probe.sandbox_available():
        assert all(r["classification"] == "NOT_RUN" for r in c2_blackbox)
    else:
        # Darwin without a usable Seatbelt (observed under Core's verifier, itself sandboxed): the
        # base probe cannot run, so the blackbox route fails closed before any account gap.
        assert [(r["observed"], r["classification"]) for r in c2_blackbox] == [
            ("UNCOMPARABLE", "REFUSED")
        ] * 2
        assert all(probe._NO_SANDBOX_BASE in r["reason"] for r in c2_blackbox)
    expected = ["VERIFIED", "VERIFIED"]
    if not (sys.platform == "linux" or probe.sandbox_available()):
        expected = ["UNCOMPARABLE", "UNCOMPARABLE"]
    assert c2 == expected
    # Linux: derived from src/cairnwork/verify_candidate.py:130-140, not yet observed on CI
    c5 = [r["observed"] for r in rows if r["class_id"] == "C5"]
    assert c5 == expected
    honest = [r for r in rows if r["class_id"] == "HONEST"]
    assert len(honest) == 10 and all(r["observed"] == expected[0] for r in honest)


def test_class_results_are_pinned():
    """C6-C10; raw current judge, policy, runner and receipt outcomes are pinned."""
    rows = registered(extra.extra_rows())
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
    if sys.platform == "linux" or probe.sandbox_available():
        assert c7_b["classification"] == "NOT_RUN"
    else:  # as C2 above: observed under Core's verifier
        assert (c7_b["observed"], c7_b["classification"]) == ("UNCOMPARABLE", "REFUSED")
        assert probe._NO_SANDBOX_BASE in c7_b["reason"]
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
