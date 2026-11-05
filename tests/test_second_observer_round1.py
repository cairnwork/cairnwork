"""Second observer, review round 1: import path, module marks, ini integrity, worktrees, types."""

import hashlib
import json

import pytest
from second_observer_support import FIXED, Project, git

TARGET = "tests/test_calc.py::test_add"
FLAT_TEST = {
    "tests/test_flat.py": "from flat import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
}
FLAT = "tests/test_flat.py::test_add"
BUG = "def add(a, b):\n    return a - b\n"


def row_of(project, candidate, target=TARGET, **over):
    code, row, _ = project.shadow(project.shadow_input(candidate, target, **over))
    assert code == 0
    return row


def uncomparable(project, raw):
    code, row, _ = project.shadow(raw)
    assert code == 0 and row["observer"] == "UNCOMPARABLE", row
    return row["detail"]


# --- the checkout root is on the import path, first -------------------------------------------


@pytest.mark.sandbox
def test_a_flat_layout_fix_passes_in_both(tmp_path):
    project = Project(tmp_path, tests=FLAT_TEST, base_files={"flat.py": BUG})
    fixed = project.candidate({"flat.py": FIXED})
    assert project.cw_verdict(fixed, FLAT) == "PASS"
    assert row_of(project, fixed, FLAT)["observer"] == "PASS"
    assert row_of(project, project.base, FLAT)["observer"] == "FAIL"


@pytest.mark.sandbox
def test_the_same_module_name_resolves_like_pytest(tmp_path):
    """calc.py at the root and under src: pytest's order puts the root first."""
    project = Project(tmp_path, base_files={"calc.py": BUG})
    fixed = project.candidate({"calc.py": FIXED})  # src/calc.py stays buggy
    assert project.cw_verdict(fixed, TARGET) == "PASS"
    assert row_of(project, fixed)["observer"] == "PASS"


# --- marks above the function -----------------------------------------------------------------

MODULE_MARKS = {
    "usefixtures": "pytestmark = pytest.mark.usefixtures('prime')",
    "skip": "pytestmark = pytest.mark.skip",
    "xfail": "pytestmark = pytest.mark.xfail",
}


def marked_tests(head: str) -> dict:
    body = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    return {"tests/test_calc.py": f"import pytest\n\n{head}\n\n\n{body}"}


@pytest.mark.parametrize("mark", sorted(MODULE_MARKS))
def test_a_module_level_mark_is_na(tmp_path, mark):
    project = Project(tmp_path, tests=marked_tests(MODULE_MARKS[mark]))
    row = row_of(project, project.candidate({"src/calc.py": FIXED}))
    assert row["observer"] == "NA", row


@pytest.mark.parametrize("mark", ["usefixtures('prime')", "skip", "xfail"])
def test_a_class_level_mark_on_a_unittest_class_is_na(tmp_path, mark):
    text = (
        "import unittest\n\nimport pytest\nfrom calc import add\n\n\n"
        f"@pytest.mark.{mark}\nclass TestU(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    )
    project = Project(tmp_path, tests={"tests/test_calc.py": text})
    row = row_of(
        project, project.candidate({"src/calc.py": FIXED}), "tests/test_calc.py::TestU::test_add"
    )
    assert row["observer"] == "NA", row


@pytest.mark.parametrize("mark", ["usefixtures('prime')", "skip", "xfail"])
def test_a_function_level_mark_is_na(tmp_path, mark):
    text = (
        f"import pytest\nfrom calc import add\n\n\n@pytest.mark.{mark}\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
    )
    project = Project(tmp_path, tests={"tests/test_calc.py": text})
    assert row_of(project, project.candidate({"src/calc.py": FIXED}))["observer"] == "NA"


@pytest.mark.sandbox
def test_an_unmarked_module_is_still_observed(tmp_path):
    project = Project(tmp_path, tests=marked_tests("pytestmark = pytest.mark.slow"))
    assert row_of(project, project.candidate({"src/calc.py": FIXED}))["observer"] == "PASS"


# --- the ini is re-rendered, not trusted ---------------------------------------------------------


def with_ini(project, text):
    return project.shadow_input(
        project.base, TARGET, ini_text=text, ini_sha256=hashlib.sha256(text.encode()).hexdigest()
    )


def test_a_foreign_ini_with_its_own_hash_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    assert "pythonpath" in uncomparable(project, with_ini(project, "x\n"))


def test_an_edited_ini_with_a_matching_hash_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    good = project.shadow_input(project.base, TARGET)["ini_text"]
    assert uncomparable(project, with_ini(project, good + "addopts = -x\n"))


def test_an_ini_hash_that_differs_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET, ini_sha256="0" * 64)
    assert "ini_sha256" in uncomparable(project, raw)


def test_an_ini_with_a_relative_first_entry_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    good = project.shadow_input(project.base, TARGET)["ini_text"]
    text = good.replace("/verifier/checkout", "checkout")  # re-renders to the same text
    assert uncomparable(project, with_ini(project, text))


def test_another_interpreter_identity_is_uncomparable(tmp_path):
    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET, interpreter_identity="0" * 64)
    assert "interpreter" in uncomparable(project, raw)


# --- a worktree-form project -------------------------------------------------------------------


@pytest.mark.sandbox
def test_a_project_that_is_a_linked_worktree_is_rebuilt(tmp_path):
    project = Project(tmp_path)
    fixed = project.candidate({"src/calc.py": FIXED})
    linked = tmp_path / "linked"
    git(project.repo, "worktree", "add", "--detach", str(linked), project.base)
    assert (linked / ".git").is_file()
    row = row_of(project, fixed, repo=str(linked))
    assert row["observer"] == "PASS", row


# --- types: never an exception ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ini_text", 5),
        ("installed_plugins", [1]),
        ("installed_plugins", "pytest_mock"),
        ("test_roots", "tests"),
        ("import_paths", "src"),
        ("interpreter", ["x"]),
        ("target", 7),
        ("cw_verdict", 1),
        ("base", None),
    ],
)
def test_a_wrongly_typed_field_is_uncomparable(tmp_path, key, value):
    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET)
    raw[key] = value
    uncomparable(project, raw)
    json.dumps(raw)


# --- round 2: marks only the runner sees, the `mark.` spelling, the output check order ---------

T = "def test_add():\n    assert add(2, 3) == 5\n"
USES = "uses = pytest.mark.usefixtures('prime')\n"
RUNNER_ONLY = {
    "alias": USES + "\n\n@uses\n" + T,
    "annotated": "pytestmark: list = [pytest.mark.usefixtures('prime')]\n\n\n" + T,
    "conditional": "if True:\n    pytestmark = pytest.mark.usefixtures('prime')\n\n\n" + T,
    "base_class": (
        "import unittest\n\n\n@pytest.mark.usefixtures('prime')\n"
        "class Base(unittest.TestCase):\n    pass\n\n\n"
        "class TestU(Base):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    ),
    "method_alias": (
        "import unittest\n\nuses = pytest.mark.usefixtures('prime')\n\n\n"
        "class TestU(unittest.TestCase):\n    @uses\n    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
    ),
}


@pytest.mark.sandbox
@pytest.mark.parametrize("form", sorted(RUNNER_ONLY))
def test_marks_the_static_scan_cannot_see_are_na_through_the_runner(tmp_path, form):
    text = "import pytest\nfrom calc import add\n\n" + RUNNER_ONLY[form]
    project = Project(tmp_path, tests={"tests/test_calc.py": text})
    name = "TestU::test_add" if "TestU" in text else "test_add"
    row = row_of(project, project.candidate({"src/calc.py": FIXED}), f"tests/test_calc.py::{name}")
    assert row["observer"] == "NA", row


@pytest.mark.parametrize("where", ["function", "unittest_method"])
def test_the_from_import_mark_spelling_is_found_statically(tmp_path, monkeypatch, where):
    import cairnwork_sandbox

    monkeypatch.setattr(
        cairnwork_sandbox, "select", lambda: None
    )  # only the static path can say NA
    if where == "function":
        body = "@mark.usefixtures('prime')\ndef test_add():\n    assert add(2, 3) == 5\n"
        name = "test_add"
    else:
        body = (
            "import unittest\n\n\nclass TestU(unittest.TestCase):\n    @mark.usefixtures('prime')\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        )
        name = "TestU::test_add"
    text = f"from calc import add\nfrom pytest import mark\n\n\n{body}"
    project = Project(tmp_path, tests={"tests/test_calc.py": text})
    row = row_of(project, project.base, f"tests/test_calc.py::{name}")
    assert row["observer"] == "NA" and row["detail"] == "MARKS"


def test_an_unusable_input_still_cannot_write_into_the_repo(tmp_path):
    from experimental.second_observer.cli import main

    project = Project(tmp_path)
    raw = project.shadow_input(project.base, TARGET, interpreter_identity="0" * 64)
    source = tmp_path / "in.json"
    source.write_text(json.dumps(raw))
    out = project.repo / "shadow-out"
    assert main(["--input", str(source), "--out", str(out)]) == 2
    assert not out.exists()
