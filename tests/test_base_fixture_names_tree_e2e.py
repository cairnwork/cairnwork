"""#1767 (#986 AC7) end to end: a name an unchanged base test under the new conftest requests.

A base conftest registers a plugin object in ``pytest_configure`` that provides the fixture
``shared``. The test commit adds ``tests/a/conftest.py`` that binds ``shared`` and changes only
another base file (optionally also a second base test). A base test
under ``tests/a`` that requests ``shared`` at run time is refused; a base test in the sibling
directory ``tests/b`` is not governed by that conftest (pytest, measured), so it is not refused."""

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
REQUESTS = "def test_requests(shared):\n    assert shared == 99\n"
PLAIN = "from calc import add\ndef test_regression():\n    assert add(1, 1) == 2\n"
EDITED = (
    BASE_TEST + "\n\n\ndef test_also():\n    assert True\n"
)  # the one base test the commit edits
OTHER = "tests/test_other.py"  # a second base test the commit may also edit
BOTH = (
    (
        "pytest",
        ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/test_base.py", OTHER),
    ),
)


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _run(project, tmp_path, requester, other_changed):
    base_files = {
        "tests/conftest.py": BASE_CONFTEST + PLUGIN,
        OTHER: "def test_o():\n    assert True\n",
    }
    if requester:
        base_files[requester] = REQUESTS
    extra = {
        "tests/test_base.py": EDITED,
        **({OTHER: "def test_o():\n    assert 1 == 1\n"} if other_changed else {}),
    }
    return conftest_run(
        project,
        tmp_path,
        addition=BINDING,
        regression=PLAIN,
        path="tests/a/conftest.py",
        base_files=base_files,
        extra_files=extra,
        verifiers=BOTH,
    )


@pytest.mark.parametrize("other_changed", [False, True])
def test_a_name_a_base_test_under_the_new_conftest_requests_is_refused(
    project, tmp_path, other_changed
):
    result = _run(project, tmp_path, "tests/a/test_g.py", other_changed)
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


@pytest.mark.parametrize("other_changed", [False, True])
def test_a_sibling_directorys_base_test_is_not_governed_so_it_passes(
    project, tmp_path, other_changed
):
    result = _run(project, tmp_path, "tests/b/test_s.py", other_changed)
    assert result.status == "COMPLETED", result.reason


def test_without_a_base_test_that_requests_it_the_binding_is_accepted(project, tmp_path):
    result = _run(project, tmp_path, None, False)
    assert result.status == "COMPLETED", result.reason
