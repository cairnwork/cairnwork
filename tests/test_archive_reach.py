"""#967: when the test commit changes an archive, a conftest that changes sys.path in any detected
way is refused (the path text is not judged); one that does not change it is not."""

from __future__ import annotations

import pytest
from conftest import commit_change, git
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed)

import cairnwork.deferred_tests as dt
from cairnwork.archive_reach import archive_path_reason
from cairnwork.conftest_check import check_conftests

REASON = "changes sys.path"
RUNTIME = "import sys, glob\nfor z in glob.glob('tests/data/*.zip'):\n    sys.path.insert(0, z)\n"
WHL = (
    "import sys, os\nfor f in os.listdir(D):\n    if f.endswith('.whl'):\n"
    "        sys.path.insert(0, f)\n"
)
LISTDIR = "import sys, os\nfor f in os.listdir({}):\n    sys.path.insert(0, f)\n"
ALIAS = "{}\n\ndef f(z):\n    {}\n\nS = '*.zip'\n"
OTHER = "import sys\nsys.path.insert(0, {!r})\n"
_HEADS = [
    "import sys as s\ns.path.insert(0, {})",
    "from sys import path\npath.append({})",
    "import sys\np = sys.path\np.insert(0, {})",
]
ALIAS_ROWS = [
    (h.format(lit) + "\n", a)
    for h in _HEADS
    for lit, a in [
        ("'*.zip'", "t/v.zip"),
        ("'*.whl'", "t/v.whl"),
        ("'t'", "t/v.zip"),
        ("'src'", "t/v.egg"),
    ]
]

# a conftest that changes sys.path: each is refused whatever the path expression is
E2E_REFUSED = [
    *ALIAS_ROWS,
    (RUNTIME, "tests/data/vendor.zip"),
    ("import sys\nsys.path += ['tests/libs']\n", "tests/libs/v.egg"),
    ("import site\nsite.addsitedir('libs')\n", "libs/v.pyz"),
    ("import sys, glob\nsys.path[0:0] = glob.glob('*.zip')\n", "t/other.zip"),
    ("import sys\nsys.path[0:0] = ['x']\nS = '*.zip'\n", "t/other.zip"),
    (WHL, "tests/data/vendor.whl"),
    (LISTDIR.format("os.path.join('src', 'data')"), "src/data/v.zip"),
    (LISTDIR.format("'Tests/Data'"), "tests/data/v.zip"),
    (OTHER.format("./vendor/other.zip"), "tests/data/s.zip"),
    (OTHER.format(".cache"), "tests/data/s.zip"),
    ("path = []\nimport sys\nsys.path.insert(0, '*.zip')\n", "tests/data/s.zip"),
    ("def f(mp):\n    mp.syspath_prepend('src')\n", "tests/data/s.zip"),
    (ALIAS.format("import sys as s", "s.path.insert(0, z)"), "t/v.zip"),
    (ALIAS.format("from sys import *", "path.insert(0, z)"), "t/v.zip"),
    (ALIAS.format("import sys as s\np = s.path\nq = p", "q.insert(0, z)"), "t/v.zip"),
    (ALIAS.format("import sys", "sys.path.__iadd__([z])"), "t/v.zip"),
    (
        ALIAS.format("", "request.config.pluginmanager and None\n    zipimport.zipimporter(z)"),
        "t/v.zip",
    ),
    (ALIAS.format("from site import addsitedir", "addsitedir(z)"), "t/v.zip"),
]
# no sys.path change: not refused, even with a changed archive
E2E_PASSED = [
    # the changed file is not an archive: nothing to reach, whatever the conftest does
    ("import sys\nsys.path.insert(0, 'src')\n", "tests/data/s.txt"),
    ("import glob\nZ = glob.glob('tests/data/*.zip')\n", "tests/data/sample.zip"),
    ("import sys\nprint(sys.path)\nD = 'fixtures'\n", "tests/data/s.zip"),
    ("import sys\nsaved = sys.path[:]\nX = '.egg'\n", "t/v.egg"),
]


def _commit(project, files):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


@pytest.mark.parametrize(("conftest", "archive"), E2E_REFUSED)
def test_a_conftest_that_changes_sys_path_is_refused_when_an_archive_changes(
    project, conftest, archive
):
    commit_change(project, {"tests/conftest.py": conftest})
    base, commit = _commit(project, {archive: "PK"})
    with pytest.raises(dt.TestCommitRejected) as err:
        check_conftests(project, base, commit, [archive])
    assert err.value.code == "TEST_COMMIT_CONFTEST_STATIC", err.value
    assert "#967" in str(err.value)
    assert REASON in str(err.value)


@pytest.mark.parametrize(("conftest", "archive"), E2E_PASSED)
def test_a_conftest_that_does_not_change_sys_path_is_not_refused(project, conftest, archive):
    commit_change(project, {"tests/conftest.py": conftest})
    base, commit = _commit(project, {archive: "PK"})
    check_conftests(project, base, commit, [archive])


G = "import sys, os, glob\nfrom pathlib import Path\n"
ARC = ["vendor/changed.zip"]
CHANGES = [
    # the three bypasses a judgment by text let through (Codex review)
    G + 'sys.path.insert(0, "alias")',
    G + 'sys.path.insert(0, sys.path[0] + "/vendor")',
    G + 'sys.path.insert(0, str(Path.cwd() / "vendor"))',
    # a literal path, a suffix, a glob, the archive's directory, any case, bytes
    G + "sys.path.insert(0, 'src')",
    G + "sys.path.insert(0, 'tests/data/')",
    G + "sys.path.insert(0, '/tests/data')",
    G + "sys.path.insert(0, 'Tests/Data')",
    G + "sys.path.insert(0, os.path.join('src', 'data'))",
    G + "sys.path.insert(0, 'tests/data/*')",
    G + "sys.path.insert(0, 'vendor' + '.whl')",
    G + "sys.path.insert(0, '.WHL')",
    G + "sys.path.insert(0, b'tests/data')",
    G + "sys.path.insert(0, 'tests/data/[ab]')",
    G + "sys.path.insert(0, './vendor/other.zip')",
    G + "sys.path.insert(0, str(Path(__file__).parent / 'src'))",
    G + "sys.path.insert(0, os.path.dirname(__file__))",
    G + "sys.path.insert(0, '')",
    G + "sys.path.insert(0, str(Path.cwd()))",
    G + "sys.path.insert(0, str(Path('src').joinpath('lib')))",
    G + "sys.path.extend([*('src', 'lib')])",
    G + "sys.path.extend({'src'})",
    G + "sys.path.extend(sys.path.copy())",
    G + "sys.path = sys.path + ['src']",
    G + "sys.path = sys.path[1:] + ['src']",
    # run-time built paths
    G + "sys.path.insert(0, ROOT)",
    G + "sys.path.extend(os.listdir('src'))",
    G + "sys.path.extend(Path('src').iterdir())",
    G + "sys.path.insert(0, str(Path(HERE) / 'src'))",
    G + "sys.path.insert(0, glob.glob('*.zip')[0])",
    G + "sys.path.insert(0, str(object=ROOT))",
    G + "sys.path.extend(os.walk.__call__('src'))",
    # the module and the list under other names, any form of change
    G + "s = sys\ns.path.insert(0, 'src')",
    G + "s = sys\nt = s\nt.path.insert(0, 'src')",
    G + "v = u\nu = t\nt = s\ns = sys\nv.path.insert(0, 'src')",
    G + "import sys as s\nu = s\nu.path += ['src']",
    G + "s = sys\ns.path[0:0] = ['src']",
    G + "s = sys\ns.path.extend(['src'])",
    G + "sys.path.__setitem__(0, 'src')",
    G + "sys.path.extend()",
    G + "from sys import path\npath.append('src')",
    G + "from sys import path\npath += ['src']",
    G + "p: list = sys.path\np.insert(0, 'src')",
    G + "p = sys.path\np.insert(0, 'src')",
    G + "sys.path: list = ['src']",
    G + "__import__('sys').path.insert(0, 'src')",
    G + "s = __import__('sys')\ns.path.append('src')",
    G + "import importlib\ns = importlib.import_module('sys')\ns.path.append('src')",
    # site, pytest and zipimport, also under another name or by keyword
    G + "import site\nsite.addsitedir('src')",
    G + "import site\nsite.addsitedir(sitedir='src')",
    G + "from site import addsitedir as ad\nad('src')",
    G + "from zipimport import zipimporter as Zi\nZi('src')",
    G + "from zipimport import zipimporter\nzipimporter(path='src')",
    "def f(monkeypatch):\n    monkeypatch.syspath_prepend(path=ROOT)",
    "def f(mp):\n    mp.syspath_prepend('src')",
]
UNCHANGED = [
    "",
    "import sys\nsaved = sys.path[:]\nX = {'data': 1}",
    "import sys\np = sys.path[:]\nK = 'data'",
    "import sys\nx = sys.path[0]\nD = x + '/data'",
    "import sys\nroot = sys.path[0]\nD = root",
    "import sys\np: list = sys.path\nK = 'data'",
    "import sys\np = sys.path\nK = 'data'",
    "import sys\nprint(sys.path)",
    "import sys\nsaved = sys.path[:]\nsaved.insert(0, 'data')",
    "import sys\np = sys.path\np = ['data']",
    "import sys\np: list = sys.path\np: list = ['data']",
    "import sys\nsys.path: list",
    "import glob\nZ = glob.glob('tests/data/*.zip')\nS = '.egg'",
    "path = []\npath.insert(0, '*.zip')\n",
    "x = dict()\nX = 'data'",
]


@pytest.mark.parametrize("source", CHANGES)
def test_every_sys_path_change_is_refused_whatever_the_path(source):
    assert archive_path_reason(source, ARC) == REASON


@pytest.mark.parametrize("source", UNCHANGED)
def test_no_sys_path_change_is_not_refused(source):
    assert archive_path_reason(source, ARC) == ""


@pytest.mark.parametrize("source", CHANGES[:8])
def test_no_changed_archive_means_no_refusal(source):
    assert archive_path_reason(source, []) == ""
    assert archive_path_reason(source, ["", ""]) == ""


@pytest.mark.parametrize("source", ["def (:\n", "sys.path.insert(0, \x00)", "(" * 200000])
def test_an_unparsable_source_is_not_judged_here(source):
    assert archive_path_reason(source, ARC) == ""
