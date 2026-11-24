"""#986 step 5b end to end: the base's collected fixture names reach ``check_conftests``.

A base conftest registers a plugin object in ``pytest_configure``; the autouse fixture ``shared``
it provides reaches every base test but is invisible to static reading (#916). The test commit's
new conftest binds the same name. With the recorder's names that binding is refused; without them
(no such plugin, or no names obtained) the verdict is what it was before this step."""

import pytest
from conftest_scenarios import BASE_CONFTEST, BASE_TEST, conftest_run
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

from cairnwork import base_fixture_names

pytestmark = pytest.mark.sandbox

PLUGIN = (
    "import pytest\n\n\ndef pytest_configure(config):\n    class P:\n"
    "        @pytest.fixture(autouse=True)\n        def shared(self):\n            return 99\n"
    "    config.pluginmanager.register(P())\n"
)
BINDING = "\nimport pytest\n\n\n@pytest.fixture\ndef shared():\n    return 2\n"
USES = "from calc import add\ndef test_regression(shared):\n    assert add(1, 1) == shared\n"
EDITED = BASE_TEST + "\n\ndef test_also():\n    assert True\n"  # a base file the commit changes
BOTH = (
    ("pytest", ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/test_base.py")),
)


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _run(project, tmp_path, conftest):
    return conftest_run(
        project,
        tmp_path,
        addition=BINDING,
        regression=USES,
        base_files={"tests/conftest.py": BASE_CONFTEST + conftest},
        extra_files={"tests/test_base.py": EDITED},
        verifiers=BOTH,
    )


def test_a_name_only_a_run_time_plugin_provides_is_refused(project, tmp_path):
    result = _run(project, tmp_path, PLUGIN)
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def test_without_that_plugin_the_same_binding_is_accepted(project, tmp_path):
    result = _run(project, tmp_path, "# no plugin here\n")
    assert result.status == "COMPLETED", result.reason


def test_no_names_obtained_leaves_the_old_verdict(project, tmp_path, monkeypatch):
    monkeypatch.setattr(base_fixture_names, "collect", lambda *a, **k: None)
    result = _run(project, tmp_path, PLUGIN)
    assert result.status == "COMPLETED", result.reason
