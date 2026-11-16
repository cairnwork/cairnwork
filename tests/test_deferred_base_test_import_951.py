"""#951 (residual of cw#948 / #924): the base-test-file variant of
``test_a_base_conftest_that_imports_a_changed_module_is_refused``. The base conftest imports
nothing; a base *test file* imports the module the test commit changes. cw#948 looks only at
conftests, so this ends COMPLETED. The test pins that present behaviour (the open residual); the
wanted outcome is the xfail(strict) twin below, which flips to a failure the day a rule lands."""

import pytest
from conftest import commit_change
from conftest_scenarios import (
    BASE_CONFTEST,
    BREAKS_BASE,
    NAMES_HELPER,
    PLAIN,
    RUNNER_PATCH,
)
from conftest_scenarios import (
    project as _project,  # noqa: F401  (the fixture, re-exposed below)
)
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

pytestmark = pytest.mark.sandbox

BASE_IMPORTER = "import colorsys\n\n\ndef test_uses_helper():\n    assert colorsys is not None\n"


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _go(project, tmp_path, imported):
    importer = {"tests/test_importer.py": BASE_IMPORTER} if imported else {}
    commit_change(project, {"tests/colorsys.py": "def test_helper():\n    pass\n", **importer})
    base, tests = make_test_commit(
        project, {"tests/colorsys.py": RUNNER_PATCH, "tests/test_regression.py": PLAIN}
    )
    fix = BREAKS_BASE if imported else FIX
    return run(project, tmp_path, base, tests, SeeingWorker(fix), key="k1",
               p2p_mode="whole-suite", p2p_budget=300, verifiers=NAMES_HELPER)  # fmt: skip


def test_the_base_conftest_here_imports_nothing():
    assert "import" in BASE_CONFTEST and "colorsys" not in BASE_CONFTEST


@pytest.mark.parametrize("imported", [True, False])
def test_a_base_test_file_import_of_a_changed_module_is_not_refused_today(
    project, tmp_path, imported
):
    """Residual pinned: a base test file imports the changed module; no conftest does."""
    result = _go(project, tmp_path, imported)
    assert "TEST_COMMIT_CONFTEST_STATIC" not in result.reason
    assert result.status == "COMPLETED", result.reason


@pytest.mark.xfail(strict=True, reason="#951: only base test files import the changed module")
def test_a_base_test_file_import_of_a_changed_module_should_be_refused(project, tmp_path):
    result = _go(project, tmp_path, True)
    assert result.status == "FAILED", result.reason


# --- scripts/measure_base_test_imports.py (#951 measurement fixes) -------------------------

import importlib.util  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_base_test_imports.py"
_spec = importlib.util.spec_from_file_location("measure_base_test_imports", _SCRIPT)
measure_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure_mod)


def _git(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


def _commit(repo, files, msg):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _measure(repo, sha):
    return measure_mod.measure(str(repo), sha, 300, time.monotonic() + 60)


def _same_basename_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, {
        "package_a/__init__.py": "",
        "package_a/colors.py": "A = 1\n",
        "package_b/__init__.py": "",
        "package_b/colors.py": "B = 1\n",
        "tests/test_base.py": "import package_a.colors\n",
    }, "base")  # fmt: skip
    changed_b = _commit(repo, {
        "package_b/colors.py": "B = 2\n",
        "tests/test_new.py": "def test_new():\n    pass\n",
    }, "change package_b")  # fmt: skip
    changed_a = _commit(repo, {
        "package_a/colors.py": "A = 2\n",
        "tests/test_more.py": "def test_more():\n    pass\n",
    }, "change package_a")  # fmt: skip
    return repo, changed_b, changed_a


def test_same_basename_in_another_package_is_not_counted(tmp_path):
    repo, changed_b, changed_a = _same_basename_repo(tmp_path)
    # package_b/colors.py is changed, the base test imports package_a.colors: not a hit
    assert _measure(repo, changed_b)["with_imported_changed_module"] == 0
    # positive control: the same import of the module that is really changed is a hit
    assert _measure(repo, changed_a)["with_imported_changed_module"] == 1


def test_fixed_ref_result_does_not_change_when_new_commits_land(tmp_path):
    repo, _changed_b, changed_a = _same_basename_repo(tmp_path)
    before = dict(_measure(repo, changed_a))
    _commit(repo, {
        "package_a/colors.py": "A = 3\n",
        "tests/test_later.py": "def test_later():\n    pass\n",
    }, "later commit")  # fmt: skip
    assert dict(_measure(repo, changed_a)) == before
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--ref", changed_a, str(repo)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert f"ref: {changed_a} -> {changed_a}" in done.stdout


def test_nonexistent_repo_path_exits_nonzero(tmp_path):
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), str(tmp_path / "no-such-repo")],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode != 0
    assert "git" in done.stderr and "no-such-repo" in done.stderr


def test_bad_ref_exits_nonzero(tmp_path):
    repo, _changed_b, _changed_a = _same_basename_repo(tmp_path)
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--ref", "no-such-ref-951", str(repo)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode != 0
    assert "no-such-ref-951" in done.stderr
