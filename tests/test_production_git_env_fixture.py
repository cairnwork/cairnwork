"""#435: production_git_env removes what conftest injected, through injected_test_git_env."""

import os

from conftest import TEST_GIT_ENV, injected_test_git_env


def test_production_git_env_delegates_to_injected_test_git_env(monkeypatch, request):
    """Operator values that differ from conftest's survive; values equal to it are removed."""
    for key, value in TEST_GIT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CW_TEST_GIT_ENV_INJECTED", " ".join(TEST_GIT_ENV))
    monkeypatch.setenv("GIT_CONFIG_KEY_2", "operator.value")  # not conftest's value
    expected = set(injected_test_git_env(os.environ))
    assert "GIT_CONFIG_KEY_2" not in expected and "GIT_CONFIG_COUNT" in expected
    request.getfixturevalue("production_git_env")
    assert os.environ["GIT_CONFIG_KEY_2"] == "operator.value"
    assert not any(k in os.environ for k in expected)
