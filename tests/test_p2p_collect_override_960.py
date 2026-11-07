"""#960: a default-skipped directory is exempt from the P2P targets only while the base gives
pytest no options, conftest code or plugin that could make it collect that directory. Setup:
tests/.cache/test_far.py is the only base test of neg(); the wrong candidate breaks neg(). The
base project here has no conftest (its src path comes from pytest.ini), so each trigger is the
only one. On main every trigger row was VERIFIED."""

import subprocess
from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_p2p import FIX_ADD, FIX_BREAKS_NEG, SIBLING, run
from test_p2p_base_complete import ADD, bare, project  # noqa: F401 - the fixtures

from cairnwork import p2p
from cairnwork.p2p_collect import skipped_dir

INI = "[pytest]\npythonpath = src\n"
FAR = {"tests/test_sibling.py": ADD, "tests/.cache/test_far.py": SIBLING}
BODY = (
    "(collection_path, config):\n    return False if collection_path.name == '.cache' else None\n"
)
HOOK = "def _k" + BODY


@pytest.fixture
def far(bare):  # noqa: F811 - the fixture
    commit_change(bare, FAR)
    return bare


REFUSED = {  # reviewer counterexamples of 11/07: each VERIFIED the wrong candidate on 88ea54ad
    "addopts_shlex_quote": {"pytest.ini": INI + "addopts = -o 'norecur''sedirs='\n"},
    "conftest_assign": {"tests/conftest.py": HOOK + "pytest_ignore_collect = _k\n"},
    "conftest_for_loop": {
        "conftest.py": "for _ in [1]:\n"
        + ("def pytest_ignore_collect" + BODY)
        .replace("\n    ", "\n        ")
        .replace("def", "    def", 1)
    },
}


@pytest.mark.sandbox
@pytest.mark.parametrize("name", REFUSED)
def test_a_base_that_gives_pytest_input_is_not_exempt(far, tmp_path, name):
    result = run(far, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite",
                 extra=REFUSED[name])  # fmt: skip
    assert result.status == "FAILED" and not result.verified, result.reason
    assert result.reason.startswith("UNCOMPARABLE: P2P_BASE_UNTARGETABLE: "), result.reason


@pytest.mark.sandbox
def test_a_base_with_no_input_keeps_the_exemption(far, tmp_path):
    result = run(far, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite")
    assert result.status == "COMPLETED", result.reason


TRIGGERS = {
    "addopts_any": {"pytest.ini": INI + "addopts = -ra\n"},
    "addopts_toml_list": {
        "pytest.ini": None,
        "pyproject.toml": (
            "[tool.pytest.ini_options]\npythonpath = ['src']\naddopts = ['-o', 'norecursedirs=']\n"
        ),
    },
    "conftest_annotated": {"conftest.py": "pytest_plugins: list = []\n"},
    "conftest_import": {"tests/sub/conftest.py": "from plug import pytest_ignore_collect\n"},
    "conftest_upper_case": {"CONFTEST.PY": "x = 1\n"},
    "conftest_symlink": {"conftest.py": "->elsewhere.py"},
    "pytest11_entry_point": {
        "pyproject.toml": (
            "[project]\nname = 'p'\n[project.entry-points.pytest11]\np = 'p.plugin'\n"
        )
    },
}


def _commit(repo, files):
    for name, text in files.items():
        if text is None:
            git(repo, "rm", "-q", name)
        elif text.startswith("->"):
            (repo / name).symlink_to(text[2:])
        else:
            commit_change(repo, {name: text})
            continue
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "x")
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("name", TRIGGERS)
def test_each_input_ends_the_exemption(far, name):
    assert skipped_dir(far, git(far, "rev-parse", "HEAD"))(".cache")  # the bare base: exempt
    assert not skipped_dir(far, _commit(far, TRIGGERS[name]))(".cache")


def test_a_non_utf8_name_does_not_stop_a_base_with_nothing_to_exempt(repo):
    """Review F1 on 88ea54ad: the conftest search ran on every selection and decoded strictly."""
    commit_change(repo, {"tests/test_a.py": "def test_a():\n    pass\n"})
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    blob = git(repo, "rev-parse", "HEAD:tests/test_a.py")
    rows = subprocess.run(["git", "-C", str(repo), "ls-tree", tree], capture_output=True,
                          check=True).stdout  # fmt: skip
    rows += b"100644 blob " + blob.encode() + b"\t\xff.txt\n"  # not UTF-8, outside tests/
    new = subprocess.run(["git", "-C", str(repo), "mktree"], input=rows, capture_output=True,
                         check=True).stdout.decode().strip()  # fmt: skip
    base = git(repo, "commit-tree", new, "-p", "HEAD", "-m", "non-utf8 name")
    task = SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))
    assert p2p._base_tests(task, frozenset()) == ("tests/test_a.py",)


def test_a_project_file_that_is_no_blob_ends_the_exemption(far):
    """A submodule named pyproject.toml cannot be read for a pytest11 entry point."""
    head = git(far, "rev-parse", "HEAD")
    git(far, "update-index", "--add", "--cacheinfo", f"160000,{head},pyproject.toml")
    git(far, "commit", "-q", "-m", "gitlink")
    assert not skipped_dir(far, git(far, "rev-parse", "HEAD"))(".cache")
