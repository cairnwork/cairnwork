"""Measured production-route witnesses for Issue #1353."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


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
_ACCOUNT_GAP = (
    "[blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 1/1]]"
)


def test_c1_c4_evasions_are_runnable_and_live():
    """C1-C4; each source compiles, imports, calls add, and its removal changes C2's verdict."""
    rows = probe.core_rows()
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
    if sys.platform == "linux" or _sandbox_available():
        # Linux runs the normal verifier without blackbox; the C2 pytest mutation passes.
        assert baseline.startswith("VERIFIED")
        assert all(r["observed"] == "VERIFIED" for r in got if r["class_id"] == "C2")
    else:
        assert baseline == "UNCOMPARABLE"
    mutant = next(r["observed"] for r in rows if r["class_id"] == "C2_MUTANT")
    assert mutant == (
        "FAILED" if sys.platform == "linux" or _sandbox_available() else "UNCOMPARABLE"
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
    if sys.platform == "linux" or _sandbox_available():
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
        assert c1 == ["REFUSED"] * 3 and c2_unavailable(rows) == ["UNCOMPARABLE"] * 2
        assert c3 == ["UNCOMPARABLE"] * 4 and c3_mutants == ["UNCOMPARABLE"] * 2
        assert c4 == ["REFUSED"] * 2
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
    assert all(not _literal_observations(path.read_text()) for path in sources)
    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"insert", "append"}
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "path"
        for n in ast.walk(tree)
    )


def _literal_observations(source):
    tree = ast.parse(source)

    def literal_tree(node):
        return isinstance(node, ast.Constant) or (
            isinstance(node, (ast.List, ast.Tuple))
            and all(literal_tree(item) for item in node.elts)
        )

    def allowed(node):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and node.args
                and literal_tree(node.args[0])
            ):
                return False
            return True
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

    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bindings.setdefault(name.id, []).append(node.value)

    row_names = {"row"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            row_names.update(
                alias.asname or alias.name
                for alias in node.names
                if node.module == "_w1353_probe" and alias.name == "row"
            )

    flagged = []
    for node in ast.walk(tree):
        is_row_call = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in row_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "row")
        )
        if is_row_call and isinstance(node, ast.Call):
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
            ):
                if not allowed(node.value):
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


def test_literal_observation_checker_accepts_current_probe_call_sites():
    sources = Path(__file__).parent.glob("_w1353_*.py")
    assert all(not _literal_observations(path.read_text()) for path in sources)


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
    rows = probe.core_rows()
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        selected = [r for r in rows if r["class_id"] == cid]
        assert len(selected) >= 2
        assert all(r["observed"] for r in selected)
    c2 = [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]
    c2_blackbox = [r for r in rows if r["class_id"] == "C2" and r["probe"].startswith("blackbox-")]
    assert len(c2_blackbox) == 2
    assert all(r["classification"] == "NOT_RUN" for r in c2_blackbox)
    # Linux: C2 is observed on CI as VERIFIED.
    expected = ["VERIFIED", "VERIFIED"]
    if not (sys.platform == "linux" or _sandbox_available()):
        expected = ["UNCOMPARABLE", "UNCOMPARABLE"]
    assert c2 == expected
    # Linux: derived from src/cairnwork/verify_candidate.py:130-140, not yet observed on CI
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
    assert len([r for r in rows if r["class_id"] == "C7"]) == 3
    c7_b = next(r for r in rows if r["probe"] == "b-one-tampered-pytest")
    assert c7_b["classification"] == "NOT_RUN"
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


def _sandbox_available():
    from cairnwork_sandbox import select

    return sys.platform == "darwin" and select() is not None


def c2_unavailable(rows):
    return [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]
