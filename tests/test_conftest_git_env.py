"""conftest's test-only git variables (#417): only what conftest itself set is ever removed.
The tests' own commits start no background git maintenance (#578)."""

import pytest
from conftest import INJECTED_VAR, TEST_GIT_ENV, inject_test_git_env, injected_test_git_env

CONFIG = {k: v for k, v in TEST_GIT_ENV.items() if k.startswith("GIT_CONFIG_")}


def test_a_clean_environment_gets_all_of_them_once():
    environ = {"PATH": "/bin"}
    inject_test_git_env(environ)
    assert {k: environ[k] for k in TEST_GIT_ENV} == TEST_GIT_ENV
    assert sorted(injected_test_git_env(environ)) == sorted(TEST_GIT_ENV)
    del environ["GIT_TEMPLATE_DIR"]
    inject_test_git_env(environ)  # a second import (or an xdist worker) changes nothing
    assert "GIT_TEMPLATE_DIR" not in environ


def test_preset_variables_with_the_same_values_are_never_claimed():
    environ = {"GIT_TEMPLATE_DIR": "", **CONFIG}  # set by the operator, equal to ours
    inject_test_git_env(environ)
    assert environ[INJECTED_VAR] == "" and injected_test_git_env(environ) == []
    assert {k: environ[k] for k in TEST_GIT_ENV} == TEST_GIT_ENV


def test_a_partial_operator_config_set_is_left_alone():
    environ = {"GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "x"}
    inject_test_git_env(environ)
    assert injected_test_git_env(environ) == ["GIT_TEMPLATE_DIR"]
    assert environ["GIT_CONFIG_KEY_0"] == "user.name" and "GIT_CONFIG_COUNT" not in environ


def test_a_value_changed_after_injection_is_not_removed():
    environ = {}
    inject_test_git_env(environ)
    environ["GIT_CONFIG_VALUE_0"] = "true"
    assert "GIT_CONFIG_VALUE_0" not in injected_test_git_env(environ)


@pytest.mark.parametrize("helper", ["repo-fixture", "test_workspace.commit"])
def test_the_tests_commits_start_no_background_maintenance(helper, tmp_path, monkeypatch, request):
    """GIT_TRACE names every git command started: a commit with auto maintenance on starts
    ``git maintenance run --auto --detach`` (#565), which the workspace object walk can race."""
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    repo = request.getfixturevalue("repo")  # built now, under GIT_TRACE
    if helper == "test_workspace.commit":
        from test_workspace import commit

        (repo / "src" / "a.py").write_text("x = 2\n")
        commit(repo)
    text = trace.read_text()
    assert " commit " in text and "maintenance" not in text and " gc " not in text
