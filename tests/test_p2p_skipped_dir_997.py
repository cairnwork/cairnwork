"""#960, #997: a base test file no verifier target can name is refused, also under a directory
pytest's default norecursedirs skips. Whether the project's own pytest skips it depends on inputs
the verifier does not read (base options and conftest code, test-module plugins, package
metadata, testpaths, links, nested config, the CI command line), so there is no exemption.
Setup: tests/.cache/test_far.py is the only base test of neg(); the wrong candidate breaks neg().
On main (a868719d) every row here except the first was CORE_VERIFIED."""

import subprocess
from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_p2p import FIX_BREAKS_NEG, SIBLING, run
from test_p2p_base_complete import ADD, bare, project  # noqa: F401 - the fixtures

from cairnwork import p2p

INI = "[pytest]\npythonpath = src\n"
FAR = {"tests/test_sibling.py": ADD, "tests/.cache/test_far.py": SIBLING}
PLUG = (
    "def pytest_ignore_collect(collection_path, config):\n"
    "    return False if collection_path.name == '.cache' else None\n"
)


@pytest.fixture
def far(bare):  # noqa: F811 - the fixture
    commit_change(bare, FAR)
    return bare


SHAPES = {
    "conftest_hook": {"tests/conftest.py": PLUG},  # refused on main by #993 too
    "test_module_plugin": {
        "tests/a/test_x.py": "pytest_plugins = ['keepplug']\ndef test_x():\n    pass\n",
        "src/keepplug.py": PLUG,
        "tests/b/.cache/test_far2.py": SIBLING,
    },
    "nested_config": {"tests/pytest.ini": "[pytest]\npythonpath = ../src\nnorecursedirs = build\n"},
    "testpaths_names_it": {"pytest.ini": INI + "testpaths = tests/.cache\n"},
}


@pytest.mark.sandbox
@pytest.mark.parametrize("name", SHAPES)
def test_the_wrong_candidate_is_refused_whatever_the_base_gives_pytest(far, tmp_path, name):
    result = run(far, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite",
                 extra=SHAPES[name])  # fmt: skip
    assert result.status == "FAILED" and not result.verified, result.reason
    assert result.reason.startswith("UNCOMPARABLE: P2P_BASE_UNTARGETABLE: "), result.reason


def _base(repo):
    return SimpleNamespace(repo=repo, base_commit=git(repo, "rev-parse", "HEAD"),
                           test_roots=("tests",), test_commit="")  # fmt: skip


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"tests/cache_link": "->.cache"},  # a tracked link names the same directory
        {"keepplug-1.dist-info/entry_points.txt": "[pytest11]\nk = keepplug\n"},
        {"tox.ini": "[testenv]\nsetenv =\n    PYTEST_ADDOPTS=-o norecursedirs=\n"},
    ],
    ids=["no_input", "link", "metadata_entry_point", "tox_environment"],
)
def test_a_skipped_directory_file_is_never_dropped(far, files):
    for name, text in files.items():
        if text.startswith("->"):
            (far / name).symlink_to(text[2:])
            git(far, "add", "-A")
            git(far, "commit", "-q", "-m", "link")
        else:
            commit_change(far, {name: text})
    with pytest.raises(p2p.BaseUncomparable) as info:
        p2p._base_tests(_base(far), frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"


def test_the_env_check_still_reads_the_nameable_tests(far):
    """#956: the non-strict read used by the base environment check drops nothing it can name."""
    assert p2p._base_tests(_base(far), frozenset(), strict=False) == (
        "tests/test_calc.py",
        "tests/test_sibling.py",
    )


def _add_index_only(repo, path: bytes, text: str = "def test_z():\n    pass\n"):
    """Commit a path git tracks but this checkout never writes (APFS cannot hold Tests/ beside
    tests/, nor a non-UTF-8 name)."""
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                          input=text.encode(), capture_output=True, check=True)  # fmt: skip
    info = b"100644 " + blob.stdout.strip() + b"\t" + path + b"\0"
    subprocess.run(["git", "-C", str(repo), "update-index", "--add", "-z", "--index-info"],
                   input=info, check=True)  # fmt: skip
    git(repo, "commit", "-q", "-m", "index only")


@pytest.mark.parametrize("path", [b"Tests/.cache/test_far.py", b"TESTS/test_upper.py"])
def test_a_root_spelled_in_another_case_is_still_read(bare, path):  # noqa: F811
    """#1001 review (Codex r1): on APFS a tracked Tests/ is the tests/ root pytest runs; its
    files no target can name (the target grammar spells the root as configured) are refused."""
    _add_index_only(bare, path)
    with pytest.raises(p2p.BaseUncomparable) as info:
        p2p._base_tests(_base(bare), frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"


def test_a_non_utf8_name_outside_the_roots_is_no_reason_to_refuse(bare):  # noqa: F811
    _add_index_only(bare, b"docs/\xff.py")
    assert p2p._base_tests(_base(bare), frozenset()) == (
        "tests/test_calc.py",
        "tests/test_sibling.py",
    )


def test_a_root_configured_in_another_case_still_reads_the_tracked_files(bare):  # noqa: F811
    task = SimpleNamespace(**{**vars(_base(bare)), "test_roots": ("Tests",)})
    with pytest.raises(p2p.BaseUncomparable) as info:  # tracked tests/..., targets spell Tests/
        p2p._base_tests(task, frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"
