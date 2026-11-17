"""Independent consumer expectations for the readable #1353 recipe table."""

# ruff: noqa: E501
import hashlib
import runpy
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pra1353_recipes import RECIPES

# Duplicate literals keep recipe changes visible to consumer assertions.
EXPECTED = (
    (
        0,
        "C1",
        "verify-cli-1",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (1, "C1", "blackbox-1", "NOT_RUN", "@rule:blackbox_classification"),
    (
        2,
        "C1_MUTANT",
        "evasion-replaced-by-pass",
        "FAILED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        3,
        "C1",
        "verify-cli-2",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (4, "C1", "blackbox-2", "NOT_RUN", "@rule:blackbox_classification"),
    (
        5,
        "C1",
        "verify-cli-3",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (6, "C1", "blackbox-3", "NOT_RUN", "@rule:blackbox_classification"),
    (
        7,
        "C2",
        "verify-cli-1",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (8, "C2", "blackbox-1", "NOT_RUN", "@rule:blackbox_classification"),
    (
        9,
        "C2_MUTANT",
        "evasion-replaced-by-pass",
        "FAILED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        10,
        "C2",
        "verify-cli-2",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (11, "C2", "blackbox-2", "NOT_RUN", "@rule:blackbox_classification"),
    (
        12,
        "C3",
        "verify-cli-1",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (13, "C3", "blackbox-1", "NOT_RUN", "@rule:blackbox_classification"),
    (
        14,
        "C3_MUTANT",
        "evasion-1-replaced-by-pass",
        "FAILED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        15,
        "C3",
        "verify-cli-2",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (16, "C3", "blackbox-2", "NOT_RUN", "@rule:blackbox_classification"),
    (
        17,
        "C3_MUTANT",
        "evasion-2-replaced-by-pass",
        "FAILED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        18,
        "C4",
        "verify-cli-1",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (19, "C4", "blackbox-1", "NOT_RUN", "@rule:blackbox_classification"),
    (
        20,
        "C4_MUTANT",
        "evasion-replaced-by-pass",
        "FAILED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        21,
        "C4",
        "verify-cli-2",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (22, "C4", "blackbox-2", "NOT_RUN", "@rule:blackbox_classification"),
    (
        23,
        "C5",
        "liar-int-1",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (
        24,
        "C5",
        "liar-int-2",
        "VERIFIED",
        "@rule:verification_classification:VERIFIED_WRONG,REFUSED",
    ),
    (
        25,
        "HONEST",
        "correct-01",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        26,
        "HONEST",
        "correct-02",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        27,
        "HONEST",
        "correct-03",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        28,
        "HONEST",
        "correct-04",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        29,
        "HONEST",
        "correct-05",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        30,
        "HONEST",
        "correct-06",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        31,
        "HONEST",
        "correct-07",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        32,
        "HONEST",
        "correct-08",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        33,
        "HONEST",
        "correct-09",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (
        34,
        "HONEST",
        "correct-10",
        "VERIFIED",
        "@rule:verification_classification:HONEST_VERIFIED,HONEST_FAILED",
    ),
    (35, "C6", "low-entropy-true", "PASS", "@rule:pass_classification"),
    (36, "C6", "low-entropy-zero", "PASS", "@rule:pass_classification"),
    (37, "C6", "low-entropy-empty", "PASS", "@rule:pass_classification"),
    (38, "CONTROL", "owner-correct", "@rule:control_expected", "HONEST_VERIFIED"),
    (39, "CONTROL", "owner-wrong", "@rule:control_expected", "HONEST_FAILED"),
    (40, "C7", "a-no-blackbox", "UNPROTECTED", "REFUSED"),
    (41, "C7", "b-one-tampered-pytest", "NOT_RUN", "@rule:blackbox_classification"),
    (42, "C9", "timeout", "VERIFICATION_INCOMPLETE", "@rule:c9_classification"),
    (43, "C9", "no-account", "VERIFICATION_INCOMPLETE", "@rule:c9_classification"),
    (44, "C10", "runner-sniff-1", "blackbox:add: PASS [matched 1/1]", "KNOWN_RESIDUAL"),
    (45, "C10", "runner-sniff-2", "blackbox:add: PASS [matched 1/1]", "KNOWN_RESIDUAL"),
    (46, "C10", "runner-sniff-3", "blackbox:add: PASS [matched 1/1]", "KNOWN_RESIDUAL"),
    (47, "C8", "valid", "OK", "@rule:c8_valid_classification"),
    (48, "C8", "head", "HEAD_MISMATCH", "REFUSED"),
    (49, "C8", "base", "BASE_MISMATCH", "REFUSED"),
    (50, "C8", "test_commit", "TEST_SOURCE_MISMATCH", "REFUSED"),
    (51, "C8", "executed_tree", "TREE_MISMATCH", "REFUSED"),
    (52, "C8", "blackbox_commitment", "BLACKBOX_COMMITMENT_MISMATCH", "REFUSED"),
    (53, "C8", "blackbox_checks", "BLACKBOX_CHECKS_MISSING", "REFUSED"),
    (54, "C7", "c-receiver-requires-blackbox", "REJECTED", "REFUSED"),
    (55, "C8", "v0.1-under-protected-policy", "TEST_SOURCE_MISMATCH", "REFUSED"),
)


def test_recipe_meaning_matches_independent_literal_table():
    assert _matches_consumer_table(RECIPES)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [(4, "OK"), (5, "HONEST_VERIFIED")],
    ids=["expected", "classification"],
)
def test_repinning_a_changed_recipe_does_not_change_consumer_expectation(field, replacement):
    changed: list[Any] = list(RECIPES)
    recipe = list(changed[48])
    recipe[field] = replacement
    changed[48] = tuple(recipe)
    with tempfile.TemporaryDirectory(prefix="cw1353-recipe-mutant-") as directory:
        copy = Path(directory) / "pra1353_recipes.py"
        copy.write_text("RECIPES = " + repr(tuple(changed)) + "\n")
        repinned_digest = hashlib.sha256(copy.read_bytes()).hexdigest()
        assert hashlib.sha256(copy.read_bytes()).hexdigest() == repinned_digest
        mutant = runpy.run_path(str(copy))["RECIPES"]
        assert not _matches_consumer_table(mutant)


def test_matches_spec_is_not_unconditionally_true():
    assert not _matches("MISMATCH", "PASS", "")
    assert not _matches("VERIFIED", "NOT_RUN", "")
    assert not _matches("OK", "REJECTED", "")


def _matches(observed, expected, reason):
    if expected == "REJECTED":
        return observed not in {"OK", "ACCEPTED"}
    if expected == "NOT_RUN":
        return any(
            x in observed or x in reason for x in ("NO_SANDBOX", "BLACKBOX_NEEDS_VERIFIER_ACCOUNT")
        ) or (observed == "UNCOMPARABLE" and "sandbox" in reason.lower())
    if expected == "VERIFICATION_INCOMPLETE":
        return "VERIFICATION_INCOMPLETE:" in observed
    if expected == "PASS":
        return observed == "PASS" or ": PASS " in observed
    return observed == expected


def _matches_consumer_table(recipes):
    actual = tuple((r[0], r[1], r[2], r[4], r[5]) for r in recipes)
    return actual == EXPECTED
