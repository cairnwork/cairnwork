"""#804: the verifier's explicit plugin table (pytest_config.PLUGINS). A base whose tests use a
table plugin's fixture is compared when the verifier's Python has the plugin, and refused with
PLUGIN_NOT_INSTALLED when it does not; nothing outside the table is ever loaded."""

import configparser
import importlib.util

import pytest
from conftest import commit_change
from test_uncomparable_paths import FIX_DOUBLE, run_cli
from verify_helpers import FIX

from cairnwork.pytest_config import PLUGINS, PytestConfigError, base_settings

DJANGO = "pytest_django.plugin"
MOCKER_TEST = "def test_double(mocker):\n    from calc import double\n    assert double(2) == 4\n"


def read_ini(text: bytes) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment]
    parser.read_string(text.decode())
    return dict(parser.items("pytest"))


def test_the_table_and_the_keys_each_plugin_needs_are_exact():
    assert [(name, module) for name, module, _ in PLUGINS] == [
        ("pytest_asyncio", "pytest_asyncio.plugin"),
        ("pytest_timeout", "pytest_timeout"),
        ("anyio", "anyio.pytest_plugin"),
        ("pytest_mock", "pytest_mock"),
        ("pytest_django", DJANGO),
    ]
    needs = {name: keys for name, _, keys in PLUGINS}
    assert needs["pytest_django"] == {
        "DJANGO_SETTINGS_MODULE",
        "DJANGO_CONFIGURATION",
        "FAIL_INVALID_TEMPLATE_VARS",
    }
    assert needs["pytest_mock"] == needs["anyio"] == frozenset()


def test_django_keys_are_copied_in_their_spelling_and_need_the_plugin(repo, tmp_path):
    base = commit_change(
        repo,
        {
            "pytest.ini": (
                "[pytest]\nDJANGO_SETTINGS_MODULE = proj.settings\nDJANGO_CONFIGURATION = Dev\n"
                "FAIL_INVALID_TEMPLATE_VARS = true\ndjango_find_project = true\n"
                "django_debug_mode = true\nmock_use_standalone_module = true\n"
            )
        },
    )
    settings = base_settings(repo, base)
    ini = read_ini(settings.ini_text(tmp_path, (DJANGO,)))
    assert ini["DJANGO_SETTINGS_MODULE"] == "proj.settings"
    assert ini["DJANGO_CONFIGURATION"] == "Dev" and ini["FAIL_INVALID_TEMPLATE_VARS"] == "true"
    assert ini["django_find_project"] == "false"  # pinned, never the base's true
    assert "django_debug_mode" not in ini and "mock_use_standalone_module" not in ini
    assert {"django_find_project", "django_debug_mode", "mock_use_standalone_module"} <= set(
        settings.dropped
    )
    assert settings.args((DJANGO,)) == ["-p", DJANGO]
    with pytest.raises(PytestConfigError, match="sets DJANGO_SETTINGS_MODULE, but pytest_django"):
        settings.ini_text(tmp_path, ("pytest_mock",))


def test_ini_keys_are_case_sensitive_as_in_pytest(repo, tmp_path):
    """pytest-django reads only DJANGO_SETTINGS_MODULE; a lower-case key does nothing in the
    target's own CI, so it is dropped here too (not turned into the real key)."""
    base = commit_change(repo, {"pytest.ini": "[pytest]\ndjango_settings_module = p.s\n"})
    settings = base_settings(repo, base)
    assert settings.values == () and "django_settings_module" in settings.dropped
    toml = commit_change(
        repo,
        {"pyproject.toml": '[tool.pytest.ini_options]\nDJANGO_SETTINGS_MODULE = "p.s"\n'},
        delete=("pytest.ini",),
    )
    assert dict(base_settings(repo, toml).values) == {"DJANGO_SETTINGS_MODULE": "p.s"}


def test_a_non_django_base_differs_only_by_the_load_and_the_pin(repo, tmp_path):
    base = commit_change(repo, {"pytest.ini": "[pytest]\npython_functions = check_*\n"})
    settings = base_settings(repo, base)
    without = settings.ini_text(tmp_path).decode().splitlines()
    loaded = settings.ini_text(tmp_path, (DJANGO,)).decode().splitlines()
    assert [line for line in loaded if line not in without] == ["django_find_project = false"]
    assert settings.args((DJANGO,)) == [*settings.args(), "-p", DJANGO]


def test_a_candidate_that_deletes_or_edits_the_config_changes_nothing(repo):
    base = commit_change(repo, {"pytest.ini": "[pytest]\nDJANGO_SETTINGS_MODULE = a.settings\n"})
    commit_change(repo, {"pytest.ini": "[pytest]\nDJANGO_SETTINGS_MODULE = evil.settings\n"})
    commit_change(repo, delete=("pytest.ini",))
    assert dict(base_settings(repo, base).values) == {"DJANGO_SETTINGS_MODULE": "a.settings"}


def test_plugins_named_by_the_base_are_never_loaded(repo):
    base = commit_change(
        repo,
        {"pytest.ini": "[pytest]\naddopts = -p pytest_mock -p evil\nrequired_plugins = evil\n"},
    )
    assert base_settings(repo, base).args() == []


# Real runs (Seatbelt) through cairnwork-verify. CI installs pytest-mock (dev group, #810), so
# the first two do not skip there.


@pytest.mark.sandbox
def test_a_base_using_mocker_has_teeth_and_the_fix_verifies(project, state):
    pytest.importorskip("pytest_mock")
    code, out = run_cli(project, state, MOCKER_TEST, {"src/calc.py": FIX_DOUBLE})
    assert code == 0 and out["status"] == "VERIFIED", out  # main: UNCOMPARABLE BASE_SETUP_ERROR


@pytest.mark.sandbox
def test_a_wrong_candidate_for_a_mocker_test_fails(project, state):
    pytest.importorskip("pytest_mock")
    code, out = run_cli(project, state, MOCKER_TEST, {"src/calc.py": FIX})
    assert code == 2 and out["status"] == "FAILED" and out["verified"] is False, out


@pytest.mark.sandbox
def test_a_missing_table_plugin_is_plugin_not_installed(project, state):
    if importlib.util.find_spec("pytest_django") is not None:
        pytest.skip("pytest-django is installed here")
    test = "def test_double(rf):\n    from calc import double\n    assert double(2) == 4\n"
    code, out = run_cli(project, state, test, {"src/calc.py": FIX_DOUBLE})
    assert code == 2 and out["status"] == "UNCOMPARABLE", out
    assert out["uncomparable"]["code"] == "PLUGIN_NOT_INSTALLED"
    assert "test_double" in out["uncomparable"]["detail"]


@pytest.mark.sandbox
def test_the_parent_environment_does_not_reach_the_run(project, state, monkeypatch):
    """DJANGO_SETTINGS_MODULE would beat the base's ini value in pytest-django, and PYTEST_ADDOPTS
    would add options; the verifier's environment keeps neither."""
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "evil.settings")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p evil --co")
    test = (
        "import os\n"
        "def test_double():\n"
        "    assert 'DJANGO_SETTINGS_MODULE' not in os.environ\n"
        "    assert 'PYTEST_ADDOPTS' not in os.environ\n"
        "    from calc import double\n    assert double(2) == 4\n"
    )
    code, out = run_cli(project, state, test, {"src/calc.py": FIX_DOUBLE})
    assert code == 0 and out["status"] == "VERIFIED", out


def test_plugin_not_installed_only_when_no_other_setup_error():
    from test_base_fail_kind import T1, T2, judge

    got = judge({T1: "failed", T2: "failed"}, {T1: "plugin", T2: "plugin"})
    assert (got.kind, got.code) == ("UNCOMPARABLE", "PLUGIN_NOT_INSTALLED")
    assert judge({T1: "failed", T2: "failed"}, {T1: "plugin", T2: "setup"}).code == (
        "BASE_SETUP_ERROR"
    )
    assert judge({T1: "failed", T2: "failed"}, {T1: "plugin", T2: "env"}).code == (
        "PLUGIN_NOT_INSTALLED"
    )
    assert judge({T1: "failed", T2: "failed"}, {T1: "plugin"}).kind == "TEETH"


def test_a_missing_fixture_is_plugin_not_installed_only_when_the_plugin_cannot_be_found():
    from cairnwork.pytest_config import plugin_kinds

    kinds = {"a": "plugin:pytest_mock", "b": "plugin:pytest_django.plugin", "c": "env"}
    assert plugin_kinds(kinds, ("pytest_mock",)) == {"a": "setup", "b": "plugin", "c": "env"}
    assert plugin_kinds(kinds, ()) == {"a": "plugin", "b": "plugin", "c": "env"}


def test_the_guard_record_accepts_only_table_plugin_kinds():
    import json

    from cairnwork.report_guard import parse_verdict

    def kind_ok(kind):
        record = {
            "nonce": "n", "collected": ["t::a"], "outcomes": {"t::a": "failed"},
            "fail_kind": {"t::a": kind}, "files": ["t"], "file_of": [0], "renamed": [],
            "collect_errors": [], "collect_skipped": [], "teardown_errors": [],
            "exitstatus": 1, "testsfailed": 1,
        }  # fmt: skip
        return parse_verdict(json.dumps(record).encode(), "n").recorded

    assert kind_ok("plugin:pytest_mock") and kind_ok("setup")
    assert not kind_ok("plugin:evil") and not kind_ok("plugin")
