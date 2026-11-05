"""Second observer: parametrized nodes and autouse fixtures are NA, not comparable (AC7)."""

import pytest
from second_observer_support import FIXED, Project

from experimental.second_observer.autouse import autouse_files

TARGET = "tests/test_calc.py::test_add"
AUTOUSE = "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef setup():\n    yield\n"
PLAIN = "import pytest\n\n\n@pytest.fixture\ndef setup():\n    yield\n"


def row_of(project, candidate, target=TARGET):
    code, row, _ = project.shadow(project.shadow_input(candidate, target))
    assert code == 0
    return row


def test_a_parametrized_node_is_na_with_its_reason(tmp_path):
    project = Project(tmp_path)
    row = row_of(project, project.base, TARGET + "[5]")
    assert row["observer"] == "NA" and row["detail"] == "PARAMETRIZED"
    assert row["target"] == TARGET  # what follows "[" is not kept


@pytest.mark.parametrize("target", ["tests/test_calc.py::te st[", "tests/test_calc.py::[5]"])
def test_an_invalid_name_is_still_uncomparable(tmp_path, target):
    project = Project(tmp_path)
    assert row_of(project, project.base, target)["observer"] == "UNCOMPARABLE"


def test_a_conftest_autouse_fixture_is_na(tmp_path):
    project = Project(tmp_path, base_files={"conftest.py": AUTOUSE})
    row = row_of(project, project.base)
    assert row["observer"] == "NA" and row["detail"] == "AUTOUSE_FIXTURE"
    assert row["autouse_files"] == ["conftest.py"]
    assert row["autouse_in_candidate_change"] is False


def test_an_autouse_added_by_the_candidate_is_na_and_marked(tmp_path):
    project = Project(tmp_path)
    candidate = project.candidate({"src/calc.py": FIXED, "conftest.py": AUTOUSE})
    row = row_of(project, candidate)
    assert row["observer"] == "NA" and row["autouse_in_candidate_change"] is True


def test_an_autouse_in_a_pytest_plugins_module_is_na(tmp_path):
    files = {
        "conftest.py": "pytest_plugins = ['shadow_plugin']\n",
        "shadow_plugin.py": "from pytest import fixture\n\n@fixture(autouse=True)\ndef s(): pass\n",
    }
    project = Project(tmp_path, base_files=files)
    row = row_of(project, project.base)
    assert row["observer"] == "NA" and row["autouse_files"] == ["shadow_plugin.py"]


@pytest.mark.sandbox
def test_a_conftest_with_only_plain_fixtures_is_observed_as_usual(tmp_path):
    project = Project(tmp_path, base_files={"conftest.py": PLAIN})
    assert row_of(project, project.base)["observer"] == "FAIL"
    assert row_of(project, project.candidate({"src/calc.py": FIXED}))["observer"] == "PASS"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@pytest.fixture(autouse=False)\ndef f(): pass\n", []),
        ("@pytest.fixture(scope='session', autouse=True)\ndef f(): pass\n", ["conftest.py"]),
        ("@pytest.fixture\ndef f(): pass\n", []),
    ],
)
def test_the_scan_reads_the_decorator_not_the_word(tmp_path, text, expected):
    (tmp_path / "conftest.py").write_text("import pytest\n" + text)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    assert autouse_files(tmp_path, "tests/test_x.py", []) == expected


def test_marks_are_na_even_with_no_sandbox_backend(tmp_path, monkeypatch):
    """NA is static: it must not depend on the sandbox being available (Linux CI, no backend)."""
    import cairnwork_sandbox

    monkeypatch.setattr(cairnwork_sandbox, "select", lambda: None)
    marked = (
        "import pytest\nfrom calc import add\n\n\n@pytest.mark.skip\ndef test_add():\n    pass\n"
    )
    project = Project(tmp_path / "a", tests={"tests/test_calc.py": marked})
    assert row_of(project, project.base)["observer"] == "NA"
    plain = Project(tmp_path / "b")
    row = row_of(plain, plain.base)
    assert row["observer"] == "OBS_ERROR" and "no sandbox backend" in row["detail"]
