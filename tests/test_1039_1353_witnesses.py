"""Measured production-route witnesses for Issue #1353."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _load(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("_w1353_probe")
extra = _load("_w1353_extra")


def test_c1_c4_evasions_are_runnable_and_live():
    """C1-C4; each source compiles, imports, calls add, and its removal changes C2's verdict."""
    rows = probe.core_rows()
    got = [
        r
        for r in rows
        if r["class_id"] in {"C1", "C2", "C3", "C4"} and r["probe"].startswith("verify-cli")
    ]
    assert len(got) == 8
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
    # Main verifies both C2 evasions (VERIFIED-wrong); replacing the evasion by `pass` fails.
    if _sandbox_available():
        assert baseline.startswith("VERIFIED")
        assert all(r["observed"] == "VERIFIED" for r in got if r["class_id"] == "C2")
    else:
        assert baseline == "UNCOMPARABLE"
    mutant = next(r["observed"] for r in rows if r["class_id"] == "C2_MUTANT")
    assert mutant == ("FAILED" if _sandbox_available() else "UNCOMPARABLE")
    c3 = [r["observed"] for r in rows if r["class_id"] == "C3"]
    c3_mutants = [r["observed"] for r in rows if r["class_id"] == "C3_MUTANT"]
    c1 = [r["observed"] for r in rows if r["class_id"] == "C1"]
    c4 = [r["observed"] for r in rows if r["class_id"] == "C4"]
    other_mutants = [r["observed"] for r in rows if r["class_id"] in {"C1_MUTANT", "C4_MUTANT"}]
    if _sandbox_available():
        assert mutant != baseline
        # Main catches C3 on every route (checkout changed): stronger than the spec's claim.
        assert c3 == ["FAILED"] * 4 and c3_mutants == ["FAILED", "FAILED"]
        assert c1 == ["REFUSED"] * 4 and c4 == ["REFUSED"] * 4
        assert other_mutants == ["FAILED", "FAILED"]
    else:
        assert c3_mutants == ["UNCOMPARABLE", "UNCOMPARABLE"]
        assert c1 == ["REFUSED"] * 4 and c4 == ["REFUSED"] * 4
        assert other_mutants == ["UNCOMPARABLE", "UNCOMPARABLE"]


def test_observed_values_are_recomputed_from_raw_route_returns():
    """C1-C10; every record stores the raw production return and derives observed from it."""
    rows = probe.core_rows() + extra.extra_rows()
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
    sources = list(Path(__file__).parent.glob("_w1353_*.py"))
    literal = re.compile(r"\bobserved\s*(?::=|=)\s*(?:[\"'][^\"']*[\"']\s*)+")
    assert all(not literal.search(path.read_text()) for path in sources)
    assert literal.search('observed := "blackbox:add: PASS [matched 1/1]"')
    assert literal.search('observed = "VER" "IFIED"')
    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"insert", "append"}
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "path"
        for n in ast.walk(tree)
    )


def test_candidate_routes_match_pinned_main_outputs():
    """C1-C5; real verify_cli run outputs are pinned, including both known C2/C5 bypasses."""
    rows = probe.core_rows()
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        selected = [r for r in rows if r["class_id"] == cid]
        assert len(selected) >= 2
        assert all(r["observed"] for r in selected)
    c2 = [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]
    expected = (
        ["VERIFIED", "VERIFIED"] if _sandbox_available() else ["UNCOMPARABLE", "UNCOMPARABLE"]
    )
    assert c2 == expected
    c5 = [r["observed"] for r in rows if r["class_id"] == "C5"]
    assert c5 == expected
    honest = [r for r in rows if r["class_id"] == "HONEST"]
    assert len(honest) == 10 and all(r["observed"] == expected[0] for r in honest)


def test_class_results_are_pinned():
    """C6-C10; raw current judge, policy, runner and receipt outcomes are pinned."""
    rows = extra.extra_rows()
    assert len([r for r in rows if r["class_id"] == "C6"]) >= 3
    controls = {r["probe"]: r["observed"] for r in rows if r["class_id"] == "CONTROL"}
    assert controls["owner-correct"] == "blackbox:add: PASS [matched 1/1] low-entropy"
    assert controls["owner-wrong"] == "blackbox:add: MISMATCH [matched 0/1] low-entropy"
    assert {r["probe"]: r["observed"] for r in rows if r["class_id"] == "C6"} == {
        "low-entropy-true": "blackbox:add: PASS [matched 1/1] low-entropy",
        "low-entropy-zero": "blackbox:add: PASS [matched 1/1]",
        "low-entropy-empty": "blackbox:add: PASS [matched 1/1]",
    }
    assert len([r for r in rows if r["class_id"] == "C7"]) == 4
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


def _sandbox_available():
    from cairnwork_sandbox import select

    return sys.platform == "darwin" and select() is not None
