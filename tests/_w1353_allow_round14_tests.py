"""Round-fourteen static and runtime row-registry tests."""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests._w1353_allow import check
from tests._w1353_allow_round14_cases import (
    FABRICATED,
    POST_ROW_TAMPERS,
    RULE_ONLY,
    STATIC_BLOCK,
    STATIC_PASS,
)
from tests._w1353_allow_row_rules import row_rule_findings
from tests._w1353_allow_rowreg import thaw, verify_rows
from tests._w1353_allow_syntax import syntax_findings

_KEYS = ("observed", "raw_return")

__all__ = (
    "test_round_fourteen_static_must_block",
    "test_round_fourteen_rule_only_layer_results",
    "test_round_fourteen_registry_issue_is_row_return_only",
    "test_round_fourteen_static_must_pass",
    "test_round_fourteen_runtime_tampering_is_rejected",
    "test_round_fourteen_fabricated_rows_are_rejected",
    "test_round_fourteen_runtime_registry_is_live",
    "registered",
    "thaw",
)


def registered(rows):
    assert verify_rows(rows) == []
    return rows


def _fixture(snippet):
    return (
        "from _w1353_probe import row, verify, blackbox_result\n"
        "import types\n_CACHE = {}\n"
        "def _c6(): return []\n" + snippet
    )


@pytest.mark.parametrize("case_id", sorted(STATIC_BLOCK))
def test_round_fourteen_static_must_block(case_id):
    source = _fixture(STATIC_BLOCK[case_id])
    assert check(source, "tests/_w1353_extra.py"), case_id


def test_round_fourteen_rule_only_layer_results():
    for case_id, expected in RULE_ONLY.items():
        source = _fixture(STATIC_BLOCK[case_id])
        tree = ast.parse(source)
        actual = set()
        if row_rule_findings(tree, "tests/_w1353_extra.py"):
            actual.add("row rules")
        if syntax_findings(tree):
            actual.add("syntax whitelist")
        assert actual == expected, case_id
        assert check(source, "tests/_w1353_extra.py"), case_id


def test_round_fourteen_registry_issue_is_row_return_only():
    source = (
        "from tests._w1353_allow_rowreg import issue\n"
        "def row(cid, probe, route, raw, expected, classification):\n"
        '    return issue({"class_id": cid})\n'
        "def other():\n    return issue({})\n"
    )
    tree = ast.parse(source)
    findings = row_rule_findings(tree, "tests/_w1353_probe.py")
    assert findings == [5]
    assert check(source, "tests/_w1353_probe.py")
    module_source = "import tests\ndef other():\n    return tests._w1353_allow_rowreg.issue({})\n"
    module_tree = ast.parse(module_source)
    assert row_rule_findings(module_tree, "tests/_w1353_probe.py")
    assert check(module_source, "tests/_w1353_probe.py")


@pytest.mark.parametrize("case_id", sorted(STATIC_PASS))
def test_round_fourteen_static_must_pass(case_id):
    assert not check(_fixture(STATIC_PASS[case_id]), "tests/_w1353_extra.py"), case_id


def _patched_probe(case_id):
    source = Path(__file__).with_name("_w1353_probe.py").read_text(encoding="utf-8")
    statement = {
        "out_store": 'out["observed"] = "VERIFIED"',
        "out_update": 'out.update(observed="VERIFIED")',
        "out_setdefault": 'out.setdefault("observed", "VERIFIED")',
        "nested_store": 'out["raw_return"]["status"] = "VERIFIED"',
        "last_store": 'rows[-1]["observed"] = "VERIFIED"',
        "last_update": 'rows[-1].update(observed="VERIFIED")',
        "first_raw_return": 'rows[0]["raw_return"] = {"status": "VERIFIED"}',
    }[case_id]
    source += (
        "\nfrom tests._w1353_allow_rowreg import verify_rows\n"
        "\ndef _round14_run():\n"
        '    result = verify("root", "state", "base", "candidate")\n'
        '    out = row("C1", "round14", "verify", result, "FAILED", "REFUSED")\n'
        "    rows = [out]\n    " + statement + "\n    return verify_rows(rows)\n"
    )
    module = ModuleType(f"_round14_{case_id}")
    module.__file__ = "_w1353_probe.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    module.__dict__["verify"] = lambda *args, **kwargs: {"status": "FAILED", "reason": "x"}
    return module


@pytest.mark.parametrize("case_id", sorted(POST_ROW_TAMPERS))
def test_round_fourteen_runtime_tampering_is_rejected(case_id):
    module = _patched_probe(case_id)
    with pytest.raises((TypeError, AttributeError)):
        module._round14_run()


def test_round_fourteen_fabricated_rows_are_rejected():
    source = Path(__file__).with_name("_w1353_probe.py").read_text(encoding="utf-8")
    module = ModuleType("_round14_fabricated")
    exec(compile(source, "_w1353_probe.py", "exec"), module.__dict__)
    vals = ("VERIFIED", {})
    fabricated = {
        "dict_row": dict(
            module.row("C1", "x", "r", {"status": "FAILED"}, "FAILED", "REFUSED"),
            observed="VERIFIED",
        ),
        "dict_keyword": dict(observed="VERIFIED"),
        "dict_zip": dict(zip(_KEYS, vals, strict=True)),
    }
    assert set(fabricated) == FABRICATED
    assert all(verify_rows([row]) for row in fabricated.values())


def test_round_fourteen_runtime_registry_is_live(monkeypatch):
    from tests import _w1353_allow_round14_cases as cases
    from tests import _w1353_allow_rowreg as registry

    assert set(cases.POST_ROW_TAMPERS) == set(POST_ROW_TAMPERS)
    monkeypatch.setattr(registry, "freeze", lambda row: row)
    caught = set()
    for case_id, operation in POST_ROW_TAMPERS.items():
        contents: dict[str, Any] = {"raw_return": {"status": "FAILED"}}
        if case_id != "out_setdefault":
            contents["observed"] = "FAILED"
        row = registry.issue(contents)
        operation(row, [row])
        if registry.verify_rows([row]):
            caught.add(case_id)
    assert caught == set(POST_ROW_TAMPERS)
    monkeypatch.setattr(registry, "_hash_matches", lambda row, expected: True)
    green = set()
    for case_id, operation in POST_ROW_TAMPERS.items():
        contents: dict[str, Any] = {"raw_return": {"status": "FAILED"}}
        if case_id != "out_setdefault":
            contents["observed"] = "FAILED"
        row = registry.issue(contents)
        operation(row, [row])
        if not registry.verify_rows([row]):
            green.add(case_id)
    assert green == set(POST_ROW_TAMPERS)
    green_fabricated = set()
    monkeypatch.setattr(registry, "verify_rows", lambda rows: [])
    row = registry.issue({"observed": "FAILED", "raw_return": {"status": "FAILED"}})
    fabricated = {
        "dict_row": dict(row, observed="VERIFIED"),
        "dict_keyword": dict(observed="VERIFIED"),
        "dict_zip": dict(zip(_KEYS, ("VERIFIED", {}), strict=True)),
    }
    for name, candidate in fabricated.items():
        if not registry.verify_rows([candidate]):
            green_fabricated.add(name)
    assert green_fabricated == FABRICATED
