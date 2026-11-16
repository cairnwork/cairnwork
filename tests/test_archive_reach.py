"""#967: a changed archive a conftest can put on sys.path by a run-time-built path is refused;
an archive that is only test data is not."""

from __future__ import annotations

import pytest
from conftest import commit_change, git
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed)

import cairnwork.deferred_tests as dt
from cairnwork.conftest_check import check_conftests

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
_LITS = [("'*.zip'", "t/v.zip"), ("'*.whl'", "t/v.whl"), ("'t'", "t/v.zip")]
ALIAS_ROWS = [(h.format(lit) + "\n", a, True) for h in _HEADS for lit, a in _LITS]
ALIAS_ROWS += [(h.format("'src'") + "\nZ = '.egg'\n", "t/v.egg", False) for h in _HEADS]


def _commit(project, files):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


@pytest.mark.parametrize(
    ("conftest", "archive", "refused"),
    [
        *ALIAS_ROWS,
        (RUNTIME, "tests/data/vendor.zip", True),
        (RUNTIME, "tests/data/odd.whl", True),
        ("import sys\nsys.path += ['tests/libs']\n", "tests/libs/v.egg", True),
        ("import site\nsite.addsitedir('libs')\n", "libs/v.pyz", True),
        ("import sys, glob\nsys.path[0:0] = glob.glob('*.zip')\n", "t/other.zip", True),
        ("import sys\nsys.path[0:0] = ['x']\nS = '*.zip'\n", "t/other.zip", False),
        # a suffix test, a listing of a named directory, glob case, run-time joined names
        (WHL, "tests/data/vendor.whl", True),
        (WHL.replace("endswith('.whl')", "endswith('.WHL')"), "tests/data/vendor.whl", True),
        (LISTDIR.format("os.path.join('src', 'data')"), "src/data/v.zip", True),
        (LISTDIR.format("'tests/data/'"), "tests/data/v.zip", True),
        (LISTDIR.format("'/tests/data'"), "tests/data/v.zip", True),
        (LISTDIR.format("'./tests/data'"), "tests/data/v.zip", True),
        (LISTDIR.format("'Tests/Data'"), "tests/data/v.zip", True),
        (RUNTIME.replace("*.zip", "*.ZIP"), "tests/data/v.zip", True),
        (RUNTIME.replace("'tests/data/*.zip'", "b'*.zip'"), "t/v.zip", True),
        # the sys.path forms: alias, from-import, alias of the list, pytest, dunder, zipimporter
        (ALIAS.format("import sys as s", "s.path.insert(0, z)"), "t/v.zip", True),
        (ALIAS.format("from sys import path", "path.append(z)"), "t/v.zip", True),
        (ALIAS.format("from sys import path as q", "q.extend([z])"), "t/v.zip", True),
        (ALIAS.format("from sys import *", "path.insert(0, z)"), "t/v.zip", True),
        (ALIAS.format("import sys\np = sys.path", "p.insert(0, z)"), "t/v.zip", True),
        (ALIAS.format("import sys as s\np = s.path\nq = p", "q.insert(0, z)"), "t/v.zip", True),
        (ALIAS.format("import sys as s", "s.path += [z]"), "t/v.zip", True),
        (ALIAS.format("from sys import path", "path += [z]"), "t/v.zip", True),
        (ALIAS.format("import sys", "sys.path.__iadd__([z])"), "t/v.zip", True),
        (ALIAS.format("", "monkeypatch.syspath_prepend(z)"), "t/v.zip", True),
        (
            ALIAS.format("", "request.config.pluginmanager and None\n    zipimport.zipimporter(z)"),
            "t/v.zip",
            True,
        ),
        (ALIAS.format("from site import addsitedir", "addsitedir(z)"), "t/v.zip", True),
        # another named archive, or a dot-led path, is not a pattern
        (OTHER.format("./vendor/other.zip"), "tests/data/s.zip", False),
        (OTHER.format("../wheels/other.whl"), "tests/data/s.whl", False),
        (OTHER.format(".cache"), "tests/data/s.zip", False),
        (OTHER.format("./x.pyz"), "tests/data/s.zip", False),
        ("path = []\npath.insert(0, '*.zip')\n", "tests/data/s.zip", False),
        ("import sys as s\ns.path.insert(0, 'src')\n", "tests/data/s.zip", False),
        ("from sys import path\npath.insert(0, 'src')\n", "tests/data/s.zip", False),
        ("import sys\np = sys.path\np.insert(0, 'src')\n", "tests/data/s.zip", False),
        ("def f(mp):\n    mp.syspath_prepend('src')\n", "tests/data/s.zip", False),
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


G = "import sys, os, glob\nfrom pathlib import Path\n"
FIXTURE = "Z = glob.glob('tests/data/*.zip')\nW = [f for f in os.listdir('tests/data')]\n"
PRECISE = [
    # the module under another name, then every path form
    (G + "s = sys\ns.path.insert(0, glob.glob('*.zip')[0])", "t/v.zip", True),
    (G + "s = sys\nt = s\nt.path.insert(0, glob.glob('*.zip')[0])", "t/v.zip", True),
    (G + "import sys as s\nu = s\nu.path += glob.glob('*.whl')", "t/v.whl", True),
    (G + "s = sys\ns.path[0:0] = glob.glob('*.zip')", "t/v.zip", True),
    (G + "s = sys\ns.path.extend(glob.glob('*.zip'))", "t/v.zip", True),
    (G + "__import__('sys').path.insert(0, glob.glob('*.zip')[0])", "t/v.zip", True),
    (G + "s = __import__('sys')\ns.path.append(glob.glob('*.zip')[0])", "t/v.zip", True),
    (
        G + "import importlib\ns = importlib.import_module('sys')\ns.path.append('*.zip')",
        "t/v",
        True,
    ),
    # a literal path: the archive's directory, a suffix, a glob of any kind
    (G + "sys.path.insert(0, 'tests/data/')", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, '/tests/data')", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, './tests/data')", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, 'Tests/Data')", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, os.path.join('src', 'data'))", "src/data/v.zip", True),
    (G + "sys.path.insert(0, 'tests/data/*')", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, 'vendor' + '.whl')", "t/v.whl", True),
    (G + "sys.path.insert(0, '.WHL')", "t/v.whl", True),
    (G + "sys.path.insert(0, glob.glob('tests/data/*.ZIP')[0])", "tests/data/v.zip", True),
    (G + "sys.path.insert(0, glob.glob(b'tests/data/*')[0])", "tests/data/v.zip", True),
    # a path built at run time cannot be judged: refused
    (G + "sys.path.insert(0, ROOT)", "t/v.zip", True),
    (G + "sys.path.extend(os.listdir('src'))", "t/v.zip", True),
    (G + "sys.path.extend(Path('src').iterdir())", "t/v.zip", True),
    (G + "sys.path.insert(0, str(Path(HERE) / 'src'))", "t/v.zip", True),
    # a conftest that only reads sys.path, or names a data directory, is not a path change
    (G + "saved = sys.path[:]\nX = {'data': 1}", "tests/data/s.zip", False),
    (G + "p = sys.path\nK = 'data'", "tests/data/s.zip", False),
    (G + "root = sys.path[0]\nD = os.path.join(root, 'data')", "tests/data/s.zip", False),
    (G + "p: list = sys.path\nK = 'data'", "tests/data/s.zip", False),
    (G + "saved = sys.path[:]\nsaved.insert(0, 'data')", "tests/data/s.zip", False),
    # the aliases: assignment of the module, annotated alias, imported function under a new name
    (G + "s = sys\nfor f in glob.glob('*.zip'):\n    s.path.insert(0, f)", "t/v.zip", True),
    (G + "p: list = sys.path\nfor f in glob.glob('*.zip'):\n    p.insert(0, f)", "t/v.zip", True),
    (
        G + "from site import addsitedir as ad\nfor f in glob.glob('*.zip'):\n    ad(f)",
        "t/v.zip",
        True,
    ),
    (
        G + "from zipimport import zipimporter as Zi\nfor f in glob.glob('*.zip'):\n    Zi(f)",
        "t/v.zip",
        True,
    ),
    (G + "from zipimport import zipimporter as Zi\nZi('src')\nX = 'data'", "t/v.zip", False),
    # test data only: the path put on sys.path is a literal that cannot reach the archive
    (G + "sys.path.insert(0, 'src')\n" + FIXTURE, "tests/data/sample.zip", False),
    (G + "s = sys\ns.path.insert(0, 'src')\n" + FIXTURE, "tests/data/sample.zip", False),
    (G + "from sys import path\npath.append('src')\n" + FIXTURE, "tests/data/s.zip", False),
    (G + "sys.path = sys.path + ['src']\n" + FIXTURE, "tests/data/s.zip", False),
    (G + "sys.path.insert(0, str(Path(__file__).parent / 'src'))", "tests/data/s.zip", False),
    (G + "sys.path.insert(0, os.path.dirname(__file__))", "tests/data/s.zip", False),
    (G + "sys.path.insert(0, './vendor/other.zip')", "tests/data/s.zip", False),
    (G + "sys.path.insert(0, 'src')\nX = '.egg'", "t/v.egg", False),
]


@pytest.mark.parametrize(("source", "archive", "refused"), PRECISE)
def test_only_a_path_that_may_reach_the_archive_is_refused(source, archive, refused):
    from cairnwork.archive_reach import archive_path_reason

    assert bool(archive_path_reason(source, [archive])) is refused
