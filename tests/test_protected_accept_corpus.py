from __future__ import annotations

import re
from typing import Any

from experimental.protected_accept.corpus import ENTRIES, TASKS, declaration
from experimental.protected_accept.policy import declaration_problems


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
    assert all("test_visible" in source for source in by_class["C3"] + by_class["C4"])
    assert "__init__.py" in ENTRIES[0]["files"] or any("helper" in str(e["files"]) for e in ENTRIES)
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
            "rle": [("a", 2), ("b", 1)],
        }
        if task == "counter":
            counter = namespace["Counter"]()
            assert counter.inc() == 1 and counter.inc(3) == 4 and counter.total() == 4
        else:
            assert eval(expression, namespace) == expected[task]


def test_wrong_tamper_sources_and_expected_residual() -> None:
    expected = {
        "clamp": 5,
        "slug": "hello-world",
        "median": 3,
        "duration": 5400,
        "rle": [("a", 2), ("b", 1)],
    }
    safe_import = {"C1", "C2", "C3", "C4", "C7", "C9"}
    for entry in (item for item in ENTRIES if item["kind"] == "tamper"):
        assert entry["expect_protected"] == "not PASS"
        if entry["cls"] in safe_import:
            source = entry["source"]
            assert any(
                token in source
                for token in ("runtest", "test_visible", "os.kill", "sleep", "renamed_duration")
            )
            continue
        namespace: dict[str, Any] = {"__name__": entry["task"]}
        source = re.sub(r"^import pytest.*\n", "", entry["source"], flags=re.MULTILINE)
        if entry["cls"] != "C10":
            source = re.sub(r"^import sys.*\n", "", source, flags=re.MULTILINE)
        source = re.sub(r"^import os.*\n", "", source, flags=re.MULTILINE)
        source = re.sub(
            r"^from pathlib.*\n|^Path\(.*\)\.touch\(\)\n", "", source, flags=re.MULTILINE
        )
        if entry["task"] == "counter":
            continue
        exec(compile(source, entry["task"], "exec"), namespace)
        if entry["task"] == "clamp":
            result = namespace["clamp"](8, 0, 5)
            assert result != expected["clamp"] or entry["cls"] == "C5"
        elif entry["task"] == "slug":
            result = namespace["slug"]("Hello World!")
            assert str(result) != expected["slug"] or entry["cls"] == "C5"
        elif entry["task"] == "median":
            assert namespace["median"]([1, 9, 3]) != expected["median"]
        elif entry["task"] == "duration" and "parse_duration" in namespace:
            assert namespace["parse_duration"]("1h30m") != expected["duration"]
        elif entry["task"] == "rle":
            assert namespace["rle"]("aab") != expected["rle"]


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
