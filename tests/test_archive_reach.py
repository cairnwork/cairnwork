"""#967: a changed archive a conftest can put on sys.path by a run-time-built path is refused;
an archive that is only test data is not."""

from __future__ import annotations

import pytest
from conftest import commit_change, git
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed)

import cairnwork.deferred_tests as dt
from cairnwork.conftest_check import check_conftests

RUNTIME = "import sys, glob\nfor z in glob.glob('tests/data/*.zip'):\n    sys.path.insert(0, z)\n"


def _commit(project, files):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


@pytest.mark.parametrize(
    ("conftest", "archive", "refused"),
    [
        (RUNTIME, "tests/data/vendor.zip", True),
        (RUNTIME, "tests/data/odd.whl", True),
        ("import sys\nsys.path += ['tests/libs']\n", "tests/libs/v.egg", True),
        ("import site\nsite.addsitedir('libs')\n", "libs/v.pyz", True),
        ("import sys\nsys.path[0:0] = ['x']\nS = '*.zip'\n", "t/other.zip", True),
        # test data only: no sys.path change, no pattern or directory named, or another file
        ("import glob\nZ = glob.glob('tests/data/*.zip')\n", "tests/data/sample.zip", False),
        ("import sys\nsys.path.insert(0, 'src')\n", "tests/data/sample.zip", False),
        ("import sys\nsys.path.insert(0, 'src')\nD = 'fixtures'\n", "tests/data/s.zip", False),
        ("import sys\nsys.path.insert(0, 'tests/other.zip')\n", "tests/data/s.zip", False),
        ("import sys\nsys.path.insert(0, 'src')\n", "tests/data/s.txt", False),
        ("import sys\nsys.path.insert(0, 'x')\nD = 'data'\n", "tests/data/s.txt", False),
    ],
)
def test_changed_archive_reached_by_a_runtime_sys_path_is_refused(
    project, conftest, archive, refused
):
    commit_change(project, {"tests/conftest.py": conftest})
    base, commit = _commit(project, {archive: "PK"})
    if not refused:
        check_conftests(project, base, commit, [archive])
        return
    with pytest.raises(dt.TestCommitRejected) as err:
        check_conftests(project, base, commit, [archive])
    assert err.value.code == "TEST_COMMIT_CONFTEST_STATIC", err.value
    assert "#967" in str(err.value)
