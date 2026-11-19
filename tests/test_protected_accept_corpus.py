from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from cairnwork import blackbox_judge
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.blackbox_runner import MODULE, install_runner
from experimental.protected_accept.corpus import ENTRIES, TASKS, declaration
from experimental.protected_accept.policy import declaration_problems


def _run_source(tmp_path: Path, source: str, declared: Any, timeout: int = 5) -> list[str]:
    checkout, guard = tmp_path / "co", tmp_path / "guard"
    checkout.mkdir(parents=True)
    guard.mkdir()
    module = declared.checks[0].module
    (checkout / f"{module}.py").write_text(source)
    real = os.path.realpath(checkout)
    out = tmp_path / "out.json"
    install_runner(guard, declared.runner_input([real], str(out)))
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(guard), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(
            [sys.executable, "-P", "-m", MODULE],
            cwd=checkout,
            env=env,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ["TIMEOUT"]
    run = BlackboxRun(
        out.read_bytes() if out.exists() else None,
        done.returncode,
        False,
        0,
        (),
        (),
        tmp_path / "log",
        real,
        "sha256",
        True,
    )
    return [v.reason for v in blackbox_judge.judge_run(declared, "c" * 40, run)]


def test_honest_sources_pass_every_hidden_case(tmp_path: Path) -> None:
    for entry in (item for item in ENTRIES if item["kind"] == "honest"):
        total = len(TASKS[entry["task"]][3])
        expected = f"blackbox:{entry['task']}: PASS [matched {total}/{total}]"
        assert _run_source(tmp_path / entry["id"], entry["source"], entry["declaration"]) == [
            expected
        ], entry["id"]


def test_tamper_sources_fail_full_hidden_cases(tmp_path: Path) -> None:
    for entry in (item for item in ENTRIES if item["kind"] == "tamper"):
        reasons = _run_source(
            tmp_path / entry["id"],
            entry["source"],
            entry["declaration"],
            timeout=1 if entry["cls"] == "C9" else 5,
        )
        passed = any(": PASS " in reason for reason in reasons)
        assert passed is entry["known_residual"], (entry["id"], reasons)


def test_corpus_shape_and_declarations() -> None:
    honest = [entry for entry in ENTRIES if entry["kind"] == "honest"]
    assert len(honest) >= 14
    assert all(sum(item["task"] == task for item in honest) >= 2 for task in TASKS)
    for task in TASKS:
        sources = {item["source"] for item in honest if item["task"] == task}
        assert len(sources) >= 2
    for cls in (f"C{i}" for i in range(1, 10)):
        variants = [entry for entry in ENTRIES if entry["cls"] == cls]
        assert len(variants) == (3 if cls == "C8" else 2)
        unique_sources = {item["source"] for item in variants}
        assert len(unique_sources) == len(variants) - (1 if cls == "C8" else 0)
    residuals = [entry for entry in ENTRIES if entry["cls"] == "C10"]
    assert len(residuals) == 2
    assert all(
        "_cairnwork_blackbox_runner" in e["source"] or "pytest" in e["source"] for e in residuals
    )
    for task in TASKS:
        assert not declaration_problems(declaration(task))
    for entry in ENTRIES:
        assert bool(declaration_problems(entry["declaration"])) == (entry["cls"] == "C6")
    by_class = {
        cls: [e["source"] for e in ENTRIES if e["cls"] == cls]
        for cls in (f"C{i}" for i in range(1, 10))
    }
    assert all("runtest" in source for source in by_class["C2"])
    assert all("test_visible" in source for source in by_class["C3"])
    c4 = [entry for entry in ENTRIES if entry["cls"] == "C4"]
    assert all(
        any(
            "test_visible.py" in content
            for path, content in entry["files"].items()
            if path.startswith("src/helper")
        )
        for entry in c4
    )
    assert "os.kill" in by_class["C9"][1] and "sleep" in by_class["C9"][0]


def test_honest_sources_pass_visible_case_in_process() -> None:
    for entry in (item for item in ENTRIES if item["kind"] == "honest"):
        task = entry["task"]
        module, _, expression, _ = TASKS[task]
        namespace: dict[str, Any] = {"__name__": module}
        exec(compile(entry["source"], module, "exec"), namespace)
        expected = {
            "clamp": 5,
            "slug": "hello-world",
            "median": 3,
            "duration": 5400,
            "rle": [["a", 2], ["b", 1]],
        }
        if task == "counter":
            counter = namespace["Counter"]()
            assert counter.inc() == 1 and counter.inc(3) == 4 and counter.total() == 4
        else:
            assert eval(expression, namespace) == expected[task]


def test_low_entropy_intent_is_rejected() -> None:
    assert declaration_problems(declaration("clamp", low_entropy=True))
    assert declaration_problems(declaration("rle", low_entropy=True, boolean_low=True))


def test_declared_function_is_defined_by_honest_sources() -> None:
    for entry in ENTRIES:
        check = entry["declaration"].checks[0]
        function = check.name
        if entry["kind"] == "honest" and check.kind == "function":
            assert f"def {function}(" in entry["source"], entry["id"]
            assert f"import {function}" in entry["files"]["tests/test_visible.py"], entry["id"]
    renamed = next(e for e in ENTRIES if e["id"] == "duration-C7-0")
    assert "def parse_duration(" not in renamed["source"]


def test_independent_honest_variants_at_least_ten() -> None:
    independent = [
        entry
        for entry in ENTRIES
        if entry["kind"] == "honest" and entry["source"] != entry["expected_source"]
    ]
    assert len(independent) >= 10
