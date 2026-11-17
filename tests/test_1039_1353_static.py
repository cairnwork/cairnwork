"""Static checks retained from the #1353 witness consumer."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from pra1353_derive import derive
from pra1353_recipes import FIELDS, REASONS, RECIPES, ROUTES


def _literal_observations(source):
    tree = ast.parse(source)
    bindings = {}
    row_names = {"row"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.ImportFrom) and node.module == "_w1353_probe":
            row_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "row"
            )
            continue
        else:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bindings.setdefault(name.id, []).append(node.value)

    def literal_tree(node):
        return isinstance(node, ast.Constant) or (
            isinstance(node, (ast.List, ast.Tuple))
            and all(literal_tree(item) for item in node.elts)
        )

    def allowed(node):
        if isinstance(node, ast.Call):
            return not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and node.args
                and literal_tree(node.args[0])
            )
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return True
        return (
            isinstance(node, ast.Name)
            and bool(bindings.get(node.id))
            and all(
                isinstance(value, (ast.Call, ast.Attribute, ast.Subscript))
                for value in bindings[node.id]
            )
        )

    flagged = []
    for node in ast.walk(tree):
        call = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in row_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "row")
        )
        if call and isinstance(node, ast.Call):
            raw = (
                node.args[3]
                if len(node.args) > 3
                else next((kw.value for kw in node.keywords if kw.arg == "raw"), None)
            )
            if raw is None or not allowed(raw):
                flagged.append(node.lineno)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(name, ast.Name) and name.id == "observed"
                for target in targets
                for name in ast.walk(target)
            ) and not allowed(node.value):
                flagged.append(node.lineno)
    return flagged


@pytest.mark.parametrize(
    "source",
    [
        'R = "blackbox:add: PASS [matched 1/1]"\nrow("C9", "t", "r", R, "x", "y")',
        'def f():\n    r = "PASS"\n    return row("C9", "t", "r", r, "x", "y")',
        'row("C9", "t", "r", "PASS" if flag else "FAIL", "x", "y")',
        'row("C9", "t", "r", "PA" + "SS", "x", "y")',
        'row("C9", "t", "r", "".join(["PASS"]), "x", "y")',
        'row("C9", "t", "r", f"{\'PASS\'}", "x", "y")',
        'probe.row("C9", "t", "r", "PASS", "x", "y")',
        'from _w1353_probe import row as rec\nrec("C9", "t", "r", "PASS", "x", "y")',
        'observed = "VER" + "IFIED"',
        'row("C9", "timeout", "...", "blackbox:add: VERIFICATION_INCOMPLETE: TIMEOUT", "x", "y")',
        'observed = "PASS"',
    ],
)
def test_literal_observation_checker_rejects_constant_raw_values(source):
    assert _literal_observations(source)


@pytest.mark.parametrize(
    "source",
    [
        'row("C9", "t", route(), result(), "x", "y")',
        'row("C8", name, "verify_bundle", check(path), wanted, "REFUSED")',
        'row("C6", "x", "judge", verdict.reason, "PASS", "y")',
    ],
)
def test_literal_observation_checker_allows_route_derived_raw_values(source):
    assert not _literal_observations(source)


def test_retired_witness_files_are_absent():
    folder = Path(__file__).parent
    names = ("_w1353_probe.py", "_w1353_extra.py", "_w1353_receipt.py", "_w1353_c10.py")
    assert all(not (folder / name).exists() for name in names)


def test_every_runtime_computed_main_row_field_uses_a_derive_rule():
    recipes: list[dict[str, Any]] = [dict(zip(FIELDS, values, strict=True)) for values in RECIPES]
    for recipe in recipes:
        recipe["reason"] = REASONS.get(recipe["seq"], "")
        recipe["route"] = ROUTES[recipe.pop("route_id")]
    expected_rules = {
        "classification": {*range(0, 25), *range(25, 35), *range(35, 38), 41, 42, 43, 47},
        "reason": {*range(0, 35), 41, *range(44, 47)},
        "expected": {38, 39},
    }
    for field, computed_seqs in expected_rules.items():
        for recipe in recipes:
            value = recipe[field]
            if recipe["seq"] in computed_seqs:
                assert value.startswith("@rule:"), (recipe["seq"], field, value)
            else:
                assert not (isinstance(value, str) and value.startswith("@rule:"))
    captured_no_sandbox = "no-sandbox: no usable sandbox backend"
    assert all(captured_no_sandbox not in recipe["reason"] for recipe in recipes)


@pytest.mark.parametrize(
    ("field", "rule", "recipe", "available", "unavailable"),
    [
        (
            "reason",
            "verify_reason",
            {"seq": 0, "class_id": "HONEST", "probe": "correct"},
            {"status": "VERIFIED", "reason": "sandbox: VERIFIED"},
            {"status": "UNCOMPARABLE", "reason": "no-sandbox: unavailable"},
        ),
        (
            "reason",
            "blackbox_reason",
            {"seq": 1, "class_id": "C1", "probe": "blackbox"},
            {"status": "VERIFIED", "reason": "[blackbox:add: PASS [matched 1/1]]"},
            {"status": "UNCOMPARABLE", "reason": "no-sandbox: unavailable"},
        ),
        (
            "classification",
            "verification_classification:HONEST_VERIFIED,HONEST_FAILED",
            {"seq": 25, "class_id": "HONEST", "probe": "correct"},
            {"status": "VERIFIED"},
            {"status": "UNCOMPARABLE"},
        ),
        (
            "classification",
            "blackbox_classification",
            {"seq": 1, "class_id": "C1", "probe": "blackbox"},
            {"status": "VERIFIED", "reason": "[blackbox:add: PASS [matched 1/1]]"},
            {
                "status": "UNCOMPARABLE",
                "reason": (
                    "[blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT]"
                ),
            },
        ),
        (
            "classification",
            "pass_classification",
            {"seq": 35, "class_id": "C6", "probe": "low-entropy"},
            "blackbox:add: PASS [matched 1/1]",
            "UNCOMPARABLE: no-sandbox",
        ),
        (
            "classification",
            "c9_classification",
            {"seq": 43, "class_id": "C9", "probe": "no-account"},
            {"status": "VERIFICATION_INCOMPLETE"},
            {"status": "UNCOMPARABLE"},
        ),
        (
            "classification",
            "c8_valid_classification",
            {"seq": 47, "class_id": "C8", "probe": "valid"},
            {"code": "OK"},
            {"code": "UNCOMPARABLE"},
        ),
        (
            "reason",
            "c10_reason",
            {"seq": 44, "class_id": "C10", "probe": "runner-sniff-1"},
            {"status": "PASS", "outside": 5},
            {"status": "UNCOMPARABLE", "outside": -1},
        ),
        (
            "expected",
            "control_expected",
            {"seq": 38, "class_id": "CONTROL", "probe": "owner-correct"},
            {"status": "PASS"},
            {"status": "UNCOMPARABLE"},
        ),
    ],
)
def test_each_derive_rule_matches_both_sandbox_outcomes(
    field, rule, recipe, available, unavailable
):
    recipe = {
        "route": "synthetic",
        "classification": "REFUSED",
        "reason": "",
        "expected": "PASS",
        **recipe,
        field: "@rule:" + rule,
    }
    available_row = derive(available, recipe, "darwin")
    unavailable_row = derive(unavailable, recipe, "darwin")
    formulas = {
        "verify_reason": ("sandbox: VERIFIED", "no-sandbox: unavailable"),
        "blackbox_reason": ("[blackbox:add: PASS [matched 1/1]]", "no-sandbox: unavailable"),
        "verification_classification:HONEST_VERIFIED,HONEST_FAILED": (
            "HONEST_VERIFIED",
            "HONEST_FAILED",
        ),
        "blackbox_classification": ("REFUSED", "NOT_RUN"),
        "pass_classification": ("VERIFIED_WRONG", "REFUSED"),
        "c9_classification": ("NOT_RUN", "NOT_RUN"),
        "c8_valid_classification": ("HONEST_VERIFIED", "HONEST_FAILED"),
        "c10_reason": (
            "outside-runner return=5; production runs python -P -m _cairnwork_blackbox_runner "
            "(src/cairnwork/blackbox_exec.py:139), so the name is absent from sys.modules",
            "outside-runner return=-1; production runs python -P -m _cairnwork_blackbox_runner "
            "(src/cairnwork/blackbox_exec.py:139), so the name is absent from sys.modules",
        ),
        "control_expected": (
            "blackbox:add: PASS [matched 1/1] low-entropy",
            "blackbox:add: PASS [matched 1/1] low-entropy",
        ),
    }
    expected_available, expected_unavailable = formulas[rule]
    assert available_row[field if field != "expected" else "expected_on_main"] == expected_available
    assert (
        unavailable_row[field if field != "expected" else "expected_on_main"]
        == expected_unavailable
    )
