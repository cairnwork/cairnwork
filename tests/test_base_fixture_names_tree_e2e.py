"""#1767 (#986 AC7) end to end: a name only a base test in a sibling directory requests is refused.

A base conftest registers a plugin object in ``pytest_configure`` that provides the fixture
``shared``; only ``tests/b/test_s.py`` requests it. The test commit adds ``tests/a/conftest.py``
(a sibling directory, so the code reading stops at its own subtree) that binds ``shared``, and
changes only another base file. The collect of the changed file never meets the name; the
whole-tree collect does."""

import pytest
from conftest_scenarios import BASE_CONFTEST, BASE_TEST, conftest_run
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

pytestmark = pytest.mark.sandbox

PLUGIN = (
    "import pytest\n\n\ndef pytest_configure(config):\n    class P:\n"
    "        @pytest.fixture\n        def shared(self):\n            return 99\n"
    "    config.pluginmanager.register(P())\n"
)
BINDING = "\nimport pytest\n\n\n@pytest.fixture\ndef shared():\n    return 2\n"
SIBLING = "def test_sibling(shared):\n    assert shared == 99\n"
PLAIN = "from calc import add\ndef test_regression():\n    assert add(1, 1) == 2\n"
EDITED = BASE_TEST + "\n\ndef test_also():\n    assert True\n"  # the one base file the commit edits
BOTH = (
    ("pytest", ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/test_base.py")),
)


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _run(project, tmp_path, sibling):
    base_files = {"tests/conftest.py": BASE_CONFTEST + PLUGIN}
    if sibling:
        base_files["tests/b/test_s.py"] = SIBLING
    return conftest_run(
        project,
        tmp_path,
        addition=BINDING,
        regression=PLAIN,
        path="tests/a/conftest.py",
        base_files=base_files,
        extra_files={"tests/test_base.py": EDITED},
        verifiers=BOTH,
    )


def test_a_name_a_sibling_directorys_base_test_requests_is_refused(project, tmp_path):
    result = _run(project, tmp_path, sibling=True)
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def test_without_a_base_test_that_requests_it_the_binding_is_accepted(project, tmp_path):
    result = _run(project, tmp_path, sibling=False)
    assert result.status == "COMPLETED", result.reason
