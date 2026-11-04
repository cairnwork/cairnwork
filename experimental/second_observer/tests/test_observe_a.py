"""Variant A of the second observer (Issue #845 step 1): AC1 (observer part) to AC7.

Cheats are the plugin-hook class the public tamper_scan tests already cover (a pytest_plugins
module that flips the report, swallows the exception or skips the test body), one minimal case
each. Tests marked sandbox run the observer in the real sandbox."""

import hashlib
import json
import socket
from pathlib import Path

import pytest
from observer_support import BUGGY, FIXED, Project, git

sandbox = pytest.mark.sandbox
TARGET = "tests/test_calc.py::test_add"

PLUGINS = {
    "flip_report": (
        "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    if report.when == 'call':\n        report.outcome = 'passed'\n"
    ),
    "swallow_exception": (
        "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_call(item):\n    outcome = yield\n    outcome.force_result(None)\n"
    ),
    "skip_body": "def pytest_pyfunc_call(pyfuncitem):\n    return True\n",
    "benign": "def pytest_report_header(config):\n    return 'harmless'\n",
}


def with_plugin(name, calc=BUGGY):
    return {
        "src/calc.py": calc,
        "conftest.py": "pytest_plugins = ['shadow_plugin']\n",
        "shadow_plugin.py": PLUGINS[name],
    }


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path)


def observed(project, candidate, target=TARGET, **over):
    code, row, out = project.shadow(project.shadow_input(candidate, target, **over))
    assert code == 0, row
    return row["observer"]


# --- AC1 / AC2: catches, and no false refusals ---------------------------------------------


@sandbox
@pytest.mark.parametrize("cheat", ["flip_report", "swallow_exception", "skip_body"])
def test_plugin_hook_cheat_is_caught_by_the_observer(project, cheat):
    candidate = project.candidate(with_plugin(cheat))
    assert observed(project, candidate) == "FAIL"


@sandbox
def test_base_fails_in_the_observer_and_in_cairnwork(project):
    assert project.cw_verdict(project.base, TARGET) == "FAIL"
    assert observed(project, project.base) == "FAIL"


@sandbox
def test_a_correct_fix_passes_and_is_not_flagged(project):
    candidate = project.candidate({"src/calc.py": FIXED})
    assert project.cw_verdict(candidate, TARGET) == "PASS"
    code, row, _ = project.shadow(project.shadow_input(candidate, TARGET, cw_verdict="PASS"))
    assert row["observer"] == "PASS"
    assert row["rule_t"] is None and row["same_process"] is True and row["variant"] == "A"


@sandbox
def test_a_correct_fix_with_a_harmless_plugin_hook_is_not_refused(project):
    candidate = project.candidate(with_plugin("benign", FIXED))
    assert project.cw_verdict(candidate, TARGET) == "PASS"
    assert observed(project, candidate) == "PASS"


@sandbox
def test_before_after_table(project, capsys):
    """Same bad candidate and same correct fix: what Cairnwork says and what the observer says."""
    rows = [("bad: report flipped by a plugin", project.candidate(with_plugin("flip_report")))]
    rows.append(("correct fix", project.candidate({"src/calc.py": FIXED})))
    with capsys.disabled():
        print("\ncase | Cairnwork (run_verifiers) | observer A")
        for label, commit in rows:
            print(f"{label} | {project.cw_verdict(commit, TARGET)} | {observed(project, commit)}")


# --- AC3: shadow only: nothing else is touched, nothing is written elsewhere ----------------


def snapshot(root: Path, skip: Path) -> dict:
    seen = {}
    for path in sorted(root.rglob("*")):
        if skip in (path, *path.parents):
            continue
        seen[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return seen


@sandbox
def test_shadow_run_changes_nothing_outside_its_own_directory(project):
    receipts = project.root / "receipts"
    receipts.mkdir()
    (receipts / "receipt.json").write_text('{"verdict": "VERIFIED"}')
    candidate = project.candidate({"src/calc.py": FIXED})
    raw = project.shadow_input(candidate, TARGET)
    out = project.root / "out"
    (project.root / "out.json").write_text(json.dumps(raw))
    before = snapshot(project.root, out)
    code, row, out = project.shadow(raw, name="out", protect=[receipts])
    assert code == 0 and row["observer"] == "PASS"
    assert snapshot(project.root, out) == before
    assert all(Path(p).is_relative_to(out) for p in map(str, out.rglob("*")))


@pytest.mark.parametrize("where", ["receipts/run1", "project/sub"])
def test_output_inside_a_protected_path_is_refused_before_creating_anything(project, where):
    receipts = project.root / "receipts"
    receipts.mkdir()
    out = project.root / where
    out.parent.mkdir(parents=True, exist_ok=True)
    source = project.root / "in.json"
    source.write_text(json.dumps(project.shadow_input(project.base, TARGET)))
    before = snapshot(project.root, out)
    from experimental.second_observer.cli import main

    code = main(["--input", str(source), "--out", str(out), "--protect", str(receipts)])
    assert code == 2
    assert not out.exists()
    assert snapshot(project.root, out) == before


def test_refusal_names_the_reason(project, capsys):
    from experimental.second_observer.cli import main

    receipts = project.root / "receipts"
    receipts.mkdir()
    source = project.root / "in.json"
    source.write_text(json.dumps(project.shadow_input(project.base, TARGET)))
    main(["--input", str(source), "--out", str(receipts), "--protect", str(receipts)])
    assert "SHADOW_OUTPUT_PATH" in capsys.readouterr().err


# --- AC4: the same sandbox as the verifier ---------------------------------------------------


def probe_project(tmp_path, body, name):
    return Project(tmp_path, tests={f"tests/test_{name}.py": body})


@sandbox
@pytest.mark.parametrize("sandboxed", [True, False])
def test_network_is_denied_only_by_the_sandbox(tmp_path, sandboxed):
    from experimental.second_observer.inputs import check
    from experimental.second_observer.observe import run_observer
    from experimental.second_observer.rebuild import rebuild

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    body = (
        "import socket\n\n\ndef test_net():\n"
        f"    socket.create_connection(('127.0.0.1', {server.getsockname()[1]}), 3).close()\n"
    )
    project = probe_project(tmp_path, body, "net")
    inp = check(project.shadow_input(project.base, "tests/test_net.py::test_net"))
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    try:
        checkout = rebuild(inp, shadow, shadow / "work")
        seen = run_observer(inp, checkout, shadow, sandbox=sandboxed)
    finally:
        server.close()
    assert seen.status == ("FAIL" if sandboxed else "PASS"), seen


@sandbox
@pytest.mark.parametrize("sandboxed", [True, False])
def test_write_outside_the_run_is_refused_and_recorded_as_fail(tmp_path, sandboxed):
    from experimental.second_observer.inputs import check
    from experimental.second_observer.observe import run_observer
    from experimental.second_observer.rebuild import rebuild

    target = tmp_path / "outside.txt"
    body = f"def test_write():\n    open({str(target)!r}, 'w').write('x')\n"
    project = probe_project(tmp_path, body, "write")
    inp = check(project.shadow_input(project.base, "tests/test_write.py::test_write"))
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    checkout = rebuild(inp, shadow, shadow / "work")
    seen = run_observer(inp, checkout, shadow, sandbox=sandboxed)
    assert seen.status == ("FAIL" if sandboxed else "PASS"), seen  # FAIL, not OBS_ERROR
    assert target.exists() is (not sandboxed)


# --- AC5: the rebuilt checkout is the verifier's ---------------------------------------------


def uncomparable(project, raw):
    code, row, _ = project.shadow(raw)
    assert code == 0
    assert row["observer"] == "UNCOMPARABLE", row
    return row


def test_wrong_recorded_tree_is_uncomparable(project):
    fixed = project.candidate({"src/calc.py": FIXED})
    other = project.candidate({"src/calc.py": FIXED + "# other\n"})
    row = uncomparable(
        project, project.shadow_input(fixed, TARGET, tested_tree=project.tree(other))
    )
    assert "differs" in row["detail"]


def test_wrong_candidate_commit_is_uncomparable(project):
    fixed = project.candidate({"src/calc.py": FIXED})
    other = project.candidate({"src/calc.py": FIXED + "# other\n"})
    raw = project.shadow_input(fixed, TARGET)
    raw["candidate"] = other  # the verifier ran `fixed`; the wrong commit is named instead
    uncomparable(project, raw)


def test_test_commit_whose_parent_is_not_base_is_uncomparable(project):
    """Same test files, same tree as recorded: only the parent rule can refuse this one."""
    fixed = project.candidate({"src/calc.py": FIXED})
    raw = project.shadow_input(fixed, TARGET)
    git(project.repo, "checkout", "-q", "--detach", project.base)
    git(project.repo, "commit", "-q", "--allow-empty", "-m", "empty")
    empty = git(project.repo, "rev-parse", "HEAD")
    raw["test_commit"] = project.commit(project.tests, "odd", parent=empty)
    assert "parent" in uncomparable(project, raw)["detail"]


def test_candidate_that_is_not_on_base_is_uncomparable(project):
    fixed = project.candidate({"src/calc.py": FIXED})
    second = project.commit({"src/calc.py": FIXED + "# 2\n"}, "second", parent=fixed)
    uncomparable(project, project.shadow_input(second, TARGET, tested_tree=project.tree(second)))


# --- AC6: input completeness -------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["interpreter", "installed_plugins", "ini_text"])
def test_a_missing_input_is_uncomparable(project, missing):
    raw = project.shadow_input(project.base, TARGET)
    raw.pop(missing)
    assert missing in uncomparable(project, raw)["detail"]


def test_plugin_set_hash_that_differs_is_uncomparable(project):
    raw = project.shadow_input(project.base, TARGET, installed_plugins_sha256="0" * 64)
    uncomparable(project, raw)


def test_plugin_set_that_differs_from_the_live_interpreter_is_uncomparable(project):
    from experimental.second_observer.inputs import plugins_sha256

    raw = project.shadow_input(project.base, TARGET, installed_plugins=["no_such_plugin"])
    raw["installed_plugins_sha256"] = plugins_sha256(["no_such_plugin"])
    assert "differ" in uncomparable(project, raw)["detail"]


def test_a_relative_interpreter_is_uncomparable(project):
    uncomparable(project, project.shadow_input(project.base, TARGET, interpreter="python3"))


# --- AC7: not applicable ---------------------------------------------------------------------

NA_TESTS = (
    "import unittest\n\nimport pytest\nfrom calc import add\n\n\n"
    "def test_plain():\n    assert add(2, 3) == 5\n\n\n"
    "@pytest.fixture\ndef five():\n    return 5\n\n\n"
    "def test_fixture(five):\n    assert add(2, 3) == five\n\n\n"
    "@pytest.mark.parametrize('n', [5])\ndef test_param(n):\n    assert add(2, 3) == n\n\n\n"
    "class TestPlainClass:\n    def test_method(self):\n        assert add(2, 3) == 5\n\n\n"
    "class TestUnit(unittest.TestCase):\n    def test_method(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
)


@sandbox
@pytest.mark.parametrize(
    ("name", "base_verdict", "fixed_verdict"),
    [
        ("test_plain", "FAIL", "PASS"),  # NA is not the whole module
        ("test_fixture", "NA", "NA"),
        ("test_param", "NA", "NA"),
        ("TestPlainClass::test_method", "NA", "NA"),
        ("TestUnit::test_method", "FAIL", "PASS"),
    ],
)
def test_not_applicable_targets(tmp_path, name, base_verdict, fixed_verdict):
    project = Project(tmp_path, tests={"tests/test_na.py": NA_TESTS})
    fixed = project.candidate({"src/calc.py": FIXED})
    target = f"tests/test_na.py::{name}"
    assert observed(project, project.base, target) == base_verdict
    assert observed(project, fixed, target) == fixed_verdict


@sandbox
def test_import_error_is_an_observer_error_and_a_timeout_is_a_timeout(tmp_path):
    bad = Project(tmp_path / "a", tests={"tests/test_bad.py": "import no_such_module_xyz\n"})
    assert observed(bad, bad.base, "tests/test_bad.py::test_x") == "OBS_ERROR"
    slow = Project(
        tmp_path / "b",
        tests={"tests/test_slow.py": "import time\n\n\ndef test_x():\n    time.sleep(60)\n"},
    )
    from experimental.second_observer.inputs import check
    from experimental.second_observer.observe import run_observer
    from experimental.second_observer.rebuild import rebuild

    inp = check(slow.shadow_input(slow.base, "tests/test_slow.py::test_x"))
    out = tmp_path / "shadow"
    out.mkdir()
    assert run_observer(inp, rebuild(inp, out, out / "work"), out, timeout=3).status == "TIMEOUT"


@sandbox
def test_observer_environment_is_recorded_and_bare(project):
    code, row, _ = project.shadow(project.shadow_input(project.base, TARGET))
    assert set(row["env"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    assert any(p.endswith("/work/shadow/src") for p in row["sys_path"])
    assert row["exit_agrees"] is True and row["returncode"] == 1
