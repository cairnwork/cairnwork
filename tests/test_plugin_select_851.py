"""Verifier pytest plugin selection (cairnwork#851 step 2): scanner, selector, AC map.

AC1 indirect conftest use, AC2 marker spellings, AC3 config fixtures, AC4 pytest_plugins,
AC5 unknown shapes, AC6 positive exclusion, AC7 installed-but-failed cause, AC8 single source,
AC9 mutation kills (each required name), AC10 base and candidate load the same plugins,
AC11 full rerun on a missing-plugin failure. The differential test is in
test_plugin_select_851_diff.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork import plugin_names
from cairnwork.plugin_names import attribution_names, fixtures_source, selection_names
from cairnwork.pytest_config import plugin_kinds
from experimental.plugin_select import selector
from experimental.plugin_select.scanner import (
    Scan,
    ScanUnknown,
    conftests_for,
    merge,
    safe_rel,
    scan_bytes,
    scan_run,
)
from experimental.plugin_select.selector import (
    ASYNCIO_MODULE,
    needs_full_rerun,
    plan,
    plugins_to_load,
)

DJANGO = "pytest_django.plugin"
ANYIO = "anyio.pytest_plugin"
MOCK = "pytest_mock"
INSTALLED = (ASYNCIO_MODULE, DJANGO, ANYIO, MOCK, "pytest_timeout")
NO_LOCAL = frozenset()
FULL = INSTALLED


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


def make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """A git repo whose one commit holds files; returns (repo, base commit id)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "b")
    return repo, _git(repo, "rev-parse", "HEAD")


def run_plan(tmp_path: Path, files: dict[str, str], targets: list[str]) -> tuple[str, ...]:
    repo, base = make_repo(tmp_path, files)
    return plan(repo, base, targets, INSTALLED)


def local_never(_top: str) -> bool:
    return False


# --- scanner -----------------------------------------------------------------------------


def test_scan_collects_identifiers_of_a_target():
    scan = scan_bytes("t.py", b"def test_x(mocker):\n    mocker.patch('a.b')\n", local_never)
    assert scan.classified
    assert {"mocker", "patch", "test_x"} <= scan.names


@pytest.mark.parametrize(
    "data",
    [b"def (:\n", b"x = 1\0\n", b"x = (" * 20000, "é=".encode("latin-1")],
    ids=["syntax", "nul", "deep-nesting", "bad-encoding"],
)
def test_unparsable_source_is_unknown(data):
    scan = scan_bytes("t.py", data, local_never)
    assert not scan.classified


def test_oversize_source_is_unknown():
    scan = scan_bytes("t.py", b"x = 1\n" * 300000, local_never)
    assert "limit" in scan.unknown


def test_absent_target_is_unknown(tmp_path):
    repo, base = make_repo(tmp_path, {"README.md": "x\n"})
    assert not scan_run(repo, base, ["tests/test_missing.py"]).classified


@pytest.mark.parametrize("path", ["../x.py", "/abs/x.py", "a/../b.py", "a\nb.py", ""])
def test_path_outside_repo_is_refused(path):
    with pytest.raises(ScanUnknown):
        safe_rel(path)


def test_node_id_keeps_only_the_file_part():
    assert safe_rel("tests/test_a.py::test_x[1]") == "tests/test_a.py"


def test_bad_base_is_unknown(tmp_path):
    repo, _ = make_repo(tmp_path, {"t.py": "x\n"})
    assert not scan_run(repo, "HEAD", ["t.py"]).classified


def test_directory_target_is_unknown(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "x = 1\n"})
    assert not scan_run(repo, base, ["tests"]).classified


def test_conftests_run_nearest_first():
    assert conftests_for("a/b/test_x.py") == ["a/b/conftest.py", "a/conftest.py", "conftest.py"]


def test_no_conftest_anywhere_is_still_classified(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    assert scan_run(repo, base, ["tests/test_a.py"]).classified


def test_merge_keeps_first_reason():
    merged = merge([Scan(frozenset({"a"})), Scan(frozenset({"b"}), "why")])
    assert merged == Scan(frozenset({"a", "b"}), "why")


# --- AC4 / AC5: shapes that must load everything ------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "async def test_x():\n    pass\n",  # async def (pytest_asyncio / anyio)
        "from os import *\n",  # star import
        "from . import helpers\n",  # relative import
        "import pytest\nx = getattr(pytest.mark, 'db')\n",  # dynamic call
        "import importlib\nm = importlib.import_module('x')\n",  # dynamic module load
        "import pytest\nm = pytest.mark.__dict__\n",  # dynamic attribute
    ],
)
def test_shapes_the_scan_cannot_classify(tmp_path, source):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": source})
    assert not scan_run(repo, base, ["tests/test_a.py"]).classified


def test_control_import_is_classified(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "import getattr_helpers_mod\n"})
    assert scan_run(repo, base, ["tests/test_a.py"]).classified


def test_repo_module_import_is_unknown(tmp_path):
    repo, base = make_repo(
        tmp_path, {"helpers.py": "X = 1\n", "tests/test_a.py": "import helpers\n"}
    )
    assert not scan_run(repo, base, ["tests/test_a.py"]).classified


@pytest.mark.parametrize("where", ["target", "conftest"])
def test_pytest_plugins_in_any_file_is_unknown(tmp_path, where):
    files = {"tests/test_a.py": "def test_a():\n    pass\n"}
    files["tests/conftest.py" if where == "conftest" else "tests/test_a.py"] = (
        "pytest_plugins = ['pytest_mock']\n"
    )
    repo, base = make_repo(tmp_path, files)
    assert not scan_run(repo, base, ["tests/test_a.py"]).classified


def test_pytest_plugins_in_config_loads_everything():
    scan = Scan(frozenset())
    assert plugins_to_load(INSTALLED, scan, frozenset({"pytest_plugins"})) == FULL


# --- AC1 / AC6 / AC10: the selector's verdicts ------------------------------------------


def test_ac6_positive_exclusion_when_nothing_is_used(tmp_path):
    chosen = run_plan(tmp_path, {"tests/test_a.py": "def test_a():\n    assert 1\n"},
                      ["tests/test_a.py"])  # fmt: skip
    assert MOCK not in chosen
    assert "pytest_timeout" in chosen  # outside the table: always kept


def test_ac1_indirect_conftest_fixture_loads_the_plugin(tmp_path):
    files = {
        "tests/conftest.py": (
            "import pytest\n\n@pytest.fixture\ndef patched(mocker):\n    return 1\n"
        ),
        "tests/test_a.py": "def test_a(patched):\n    assert patched\n",
    }
    assert MOCK in run_plan(tmp_path, files, ["tests/test_a.py"])


def test_ac1_root_conftest_counts_too(tmp_path):
    files = {"conftest.py": "def helper(mocker):\n    return mocker\n",
             "tests/test_a.py": "def test_a():\n    pass\n"}  # fmt: skip
    assert MOCK in run_plan(tmp_path, files, ["tests/test_a.py"])


def test_ac10_candidate_conftest_loads_plugin_for_base_run_too(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    candidate = tmp_path / "candidate"
    (candidate / "tests").mkdir(parents=True)
    (candidate / "tests" / "conftest.py").write_text("def f(mocker):\n    return mocker\n")
    assert MOCK in plan(repo, base, ["tests/test_a.py"], INSTALLED, candidate)
    assert MOCK not in plan(repo, base, ["tests/test_a.py"], INSTALLED)


def test_ac10_candidate_cannot_drop_a_plugin_the_base_uses(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {
            "tests/conftest.py": "def f(mocker):\n    return mocker\n",
            "tests/test_a.py": "def test_a(f):\n    pass\n",
        },  # fmt: skip
    )
    candidate = tmp_path / "candidate"
    (candidate / "tests").mkdir(parents=True)
    (candidate / "tests" / "conftest.py").write_text("x = 1\n")
    assert MOCK in plan(repo, base, ["tests/test_a.py"], INSTALLED, candidate)


@pytest.mark.parametrize("spelling", [
    "from pytest import mark\n\n@mark.{m}\ndef test_a():\n    pass\n",
    "import pytest as pt\n\n@pt.mark.{m}\ndef test_a():\n    pass\n",
    "import pytest\nmark = pytest.mark\n\n@mark.{m}\ndef test_a():\n    pass\n",
    "import pytest\n\n@pytest.mark.{m}\ndef test_a():\n    pass\n",
])  # fmt: skip
@pytest.mark.parametrize("marker, module", [("asyncio", ASYNCIO_MODULE), ("django_db", DJANGO)])
def test_ac2_marker_spellings_load_the_plugin(tmp_path, spelling, marker, module):
    chosen = run_plan(tmp_path, {"tests/test_a.py": spelling.format(m=marker)},
                      ["tests/test_a.py"])  # fmt: skip
    assert module in chosen


def test_ac2_marker_of_another_plugin_does_not_load_asyncio(tmp_path):
    chosen = run_plan(tmp_path, {"tests/test_a.py": "import pytest\n\n@pytest.mark.db\n"
                                 "def test_a():\n    pass\n"}, ["tests/test_a.py"])  # fmt: skip
    assert ASYNCIO_MODULE not in chosen


# --- AC3 / AC9: every required name loads its module; mutants flip the verdict -----------

REQUIRED = [
    ("mocker", MOCK), ("settings", DJANGO), ("db", DJANGO), ("client", DJANGO),
    ("rf", DJANGO), ("django_db", DJANGO), ("asyncio", ASYNCIO_MODULE),
    ("event_loop", ASYNCIO_MODULE), ("anyio_backend", ANYIO), ("anyio", ANYIO),
]  # fmt: skip


@pytest.mark.parametrize("name, module", REQUIRED)
def test_ac3_ac9_required_name_loads_its_module(name, module):
    assert module in plugins_to_load(INSTALLED, Scan(frozenset({name})), NO_LOCAL)


@pytest.mark.parametrize("name, module", REQUIRED)
def test_ac9_mutant_dropping_the_name_changes_the_verdict(monkeypatch, name, module):
    real = selection_names()
    monkeypatch.setattr(
        selector, "selection_names", lambda: {k: v for k, v in real.items() if k != name}
    )
    monkeypatch.setitem(
        selector._EXTRA_NAMES,
        module,
        tuple(n for n in selector._EXTRA_NAMES.get(module, ()) if n != name),
    )
    assert module not in plugins_to_load(INSTALLED, Scan(frozenset({name})), NO_LOCAL)


@pytest.mark.parametrize("module, key", [(DJANGO, "DJANGO_SETTINGS_MODULE"), (ASYNCIO_MODULE,
                                         "asyncio_mode")])  # fmt: skip
def test_ac3_config_key_loads_its_plugin(module, key):
    assert module in plugins_to_load(INSTALLED, Scan(), frozenset({key}))


def test_ac9_inverted_guard_is_caught():
    # the guard "not in table or used": an inverted guard would drop mocker when it is used
    assert MOCK not in plugins_to_load(INSTALLED, Scan(), NO_LOCAL)
    assert MOCK in plugins_to_load(INSTALLED, Scan(frozenset({"mocker"})), NO_LOCAL)


# --- AC7 / AC8 / AC11 ----------------------------------------------------------------------


def test_ac7_installed_but_setup_failed_is_not_not_installed():
    fail = {"t::a": "plugin:pytest_mock", "t::b": "plugin:pytest_mock"}
    assert plugin_kinds(fail, (MOCK,)) == {"t::a": "setup", "t::b": "setup"}
    assert plugin_kinds(fail, ()) == {"t::a": "plugin", "t::b": "plugin"}


def test_ac8_single_source_for_selection_and_attribution():
    assert selector.selection_names is plugin_names.selection_names
    assert set(attribution_names()) <= set(selector.name_modules())
    for name, module in attribution_names().items():
        assert selector.name_modules()[name] == module
    namespace: dict = {}
    exec(fixtures_source(), namespace)  # trusted, generated by plugin_names
    assert namespace["_PLUGIN_FIXTURES"] == attribution_names()


@pytest.mark.parametrize("output", [
    "E   fixture 'mocker' not found",
    "'asyncio' not found in `markers` configuration option",
    "PytestUnknownMarkWarning: Unknown pytest.mark.django_db",
])  # fmt: skip
def test_ac11_missing_plugin_failure_reruns_everything(output):
    assert needs_full_rerun(output, {MOCK, DJANGO, ASYNCIO_MODULE})


def test_ac11_no_rerun_without_the_plugin_or_its_names():
    assert not needs_full_rerun("E   fixture 'mocker' not found", {DJANGO})
    assert not needs_full_rerun("E   assert 1 == 2", {MOCK})
    assert needs_full_rerun("async def functions are not natively supported", {ASYNCIO_MODULE})
    assert not needs_full_rerun("async def functions are not natively supported", {MOCK})


def test_ac5_failing_plan_falls_back_to_everything(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    assert plan(repo, "not-a-sha", ["tests/test_a.py"], INSTALLED) == INSTALLED
    assert plan(tmp_path / "missing", base, ["tests/test_a.py"], INSTALLED) == INSTALLED
    assert plan(repo, base, ["../escape.py"], INSTALLED) == INSTALLED
