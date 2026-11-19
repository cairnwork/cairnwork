"""#1454 step 2b: the gate step runs PR code as a separate account with a clean environment, and
the workflow has timeouts and uses no Actions cache. Offline checks on the parsed workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from workflow_yaml import parse_yaml

TRUSTED = Path(__file__).resolve().parents[1] / ".github/workflows/pra1353-trusted.yml"


def check(text: str) -> None:
    """Raise AssertionError unless the text keeps the isolation, timeout and cache rules."""
    jobs = parse_yaml(text)["jobs"]
    analyze, publish = jobs["analyze"], jobs["publish"]
    for job in (analyze, publish):
        assert re.fullmatch(r"\d+", str(job.get("timeout-minutes", "")))
    gate = [s for s in analyze["steps"] if "pra1353_gate.py" in str(s.get("run", ""))]
    assert len(gate) == 1
    step = gate[0]
    assert 0 < int(step["timeout-minutes"]) <= 20
    run = str(step["run"])
    assert re.search(r"sudo -n -u \w+ env -i ", run)
    # PR code is started only through the helper that goes through sudo -u and env -i.
    for line in run.splitlines():
        if "pra1353_gate.py" in line and "--source" in line:
            assert line.strip().startswith("run_gate ")
    assert "timeout -k" in run
    assert "pkill -KILL -u" in run
    block = re.search(r"run_gate\(\) \{.*?\n\s*\}", run, re.S)
    assert block
    launch = (
        block.group(0)
        + "\n"
        + "\n".join(line for line in run.splitlines() if line.strip().startswith("run_gate "))
    )
    assert not re.search(
        r"ACTIONS_|RUNNER_|GITHUB_(ENV|OUTPUT|PATH|TOKEN)|GH_TOKEN|github\.token", launch
    )
    others = [s for s in analyze["steps"] if s is not step]
    assert all("GH_TOKEN" not in str(s) and "github.token" not in str(s) for s in others)
    assert "actions/cache" not in text and not re.search(r"^\s*cache:", text, re.M)
    setup = [s for s in analyze["steps"] if "setup-uv" in str(s.get("uses", ""))]
    assert len(setup) == 1 and setup[0]["with"]["enable-cache"] == "false"
    assert "--no-cache" in text
    # The gate account is not allowed to use sudo.
    assert "sudo -n -u pra1353gate sudo -n true" in text


def test_real_workflow_keeps_the_rules() -> None:
    check(TRUSTED.read_text(encoding="utf-8"))


def _edit(old: str, new: str) -> str:
    text = TRUSTED.read_text(encoding="utf-8")
    assert old in text
    return text.replace(old, new, 1)


MUTATIONS: list[tuple[str, str]] = [
    ("sudo -n -u pra1353gate env -i ", "env -i "),
    ("sudo -n -u pra1353gate env -i ", "sudo -n -u pra1353gate env "),
    ("LANG=C.UTF-8 PRA1353_BASE_LOCK", "ACTIONS_RUNTIME_TOKEN=x LANG=C.UTF-8 PRA1353_BASE_LOCK"),
    ("LANG=C.UTF-8 PRA1353_BASE_LOCK", "GITHUB_TOKEN=x LANG=C.UTF-8 PRA1353_BASE_LOCK"),
    ("LANG=C.UTF-8 PRA1353_BASE_LOCK", "GH_TOKEN=x LANG=C.UTF-8 PRA1353_BASE_LOCK"),
    ("LANG=C.UTF-8 PRA1353_BASE_LOCK", "GITHUB_OUTPUT=x LANG=C.UTF-8 PRA1353_BASE_LOCK"),
    ("    timeout-minutes: 30\n", ""),
    ("    timeout-minutes: 5\n", ""),
    ("        timeout-minutes: 18\n", ""),
    ("        timeout-minutes: 18\n", "        timeout-minutes: 25\n"),
    ("enable-cache: false", "enable-cache: true"),
    (
        "      - name: Save the gate result",
        "      - uses: actions/cache@v4\n      - name: Save the gate result",
    ),
    (
        '          python-version: "3.13"\n',
        '          python-version: "3.13"\n          cache: pip\n',
    ),
    ("sudo -n pkill -KILL -u pra1353gate || true", "true"),
    ("if sudo -n -u pra1353gate sudo -n true 2>/dev/null", "if false"),
    (
        'run_gate timeout -k 10 840 "$py" -I -S -B tests/pra1353_gate.py',
        '"$py" -I -S -B tests/pra1353_gate.py',
    ),
]


@pytest.mark.parametrize("old,new", MUTATIONS)
def test_breaking_a_rule_is_caught(old: str, new: str) -> None:
    with pytest.raises((AssertionError, KeyError, ValueError)):
        check(_edit(old, new))


def test_no_cache_use_in_new_workflow_steps() -> None:
    jobs: dict[str, Any] = parse_yaml(TRUSTED.read_text(encoding="utf-8"))["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            assert "actions/cache" not in str(step.get("uses", ""))
            assert "cache" not in {k for k in (step.get("with") or {}) if k != "enable-cache"}
