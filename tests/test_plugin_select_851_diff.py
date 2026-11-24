"""Differential test (cairnwork#851 step 2, AC10/AC11): the same run with every installed plugin
and with the selected plugins must give the same per-test outcomes.

A real git repo holds the fixture tests; pytest runs as a subprocess with plugin autoload off,
as the verifiers run it. Plugins not installed in this interpreter are simply not offered, so
their cases exercise the selector only (the rest of the matrix needs the plugins installed).
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from cairnwork.pytest_config import installed_plugins
from experimental.plugin_select.selector import needs_full_rerun, plan
from tests.test_plugin_select_851 import make_repo

FILES = {
    "tests/conftest.py": (
        "import pytest\n\n\n@pytest.fixture\ndef patched(mocker):\n    return mocker\n"
    ),
    "tests/test_direct.py": "def test_direct(mocker):\n    assert mocker is not None\n",
    "tests/test_indirect.py": "def test_indirect(patched):\n    assert patched is not None\n",
    "tests/test_string.py": (
        "def test_string(request):\n    assert request.getfixturevalue('mocker') is not None\n"
    ),
    "plain/test_plain.py": "def test_plain():\n    assert 1 + 1 == 2\n\n\ndef test_fails():\n"
    "    assert 1 == 2\n",
}
TARGETS = [
    ["plain/test_plain.py"],
    ["tests/test_direct.py"],
    ["tests/test_indirect.py"],
    ["tests/test_string.py"],
    ["plain/test_plain.py", "tests/test_indirect.py"],
]


def _outcomes(repo: Path, targets: list[str], plugins: tuple[str, ...]) -> tuple[dict, str]:
    report = repo / "junit.xml"
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    args = [sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    for plugin in plugins:
        args += ["-p", plugin]
    args += ["-c", str(repo / "pytest.ini"), "--rootdir", str(repo),
             f"--junitxml={report}", *targets]  # fmt: skip
    done = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, timeout=300)
    found: dict[str, str] = {}
    for case in ET.parse(report).iter("testcase"):
        state = "passed"
        for child in case:
            state = child.tag if child.tag in ("failure", "error", "skipped") else state
        found[f"{case.get('classname')}::{case.get('name')}"] = state
    return found, done.stdout + done.stderr


@pytest.mark.parametrize("targets", TARGETS, ids=lambda t: "+".join(Path(p).name for p in t))
def test_selected_plugins_match_the_full_load(tmp_path, targets):
    repo, base = make_repo(tmp_path, {**FILES, "pytest.ini": "[pytest]\n"})
    installed = installed_plugins(sys.executable)
    chosen = plan(repo, base, targets, installed)
    full, _ = _outcomes(repo, targets, installed)
    selected, output = _outcomes(repo, targets, chosen)
    excluded = set(installed) - set(chosen)
    if selected != full and needs_full_rerun(output, excluded):
        selected = full  # AC11: a missing-plugin failure reruns everything; the rerun decides
    assert selected == full
    assert full, "no test case ran: the fixture is broken"
