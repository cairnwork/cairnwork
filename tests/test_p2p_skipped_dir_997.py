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
from cairnwork.verify_context import VerifyContext

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


def _add_index_only(
    repo, path: bytes, text: str = "def test_z():\n    pass\n", mode: bytes = b"100644"
):
    """Commit a path git tracks but this checkout never writes (APFS cannot hold Tests/ beside
    tests/, nor a non-UTF-8 name)."""
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                          input=text.encode(), capture_output=True, check=True)  # fmt: skip
    info = mode + b" " + blob.stdout.strip() + b"\t" + path + b"\0"
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


@pytest.mark.parametrize(
    ("link", "target"),
    [
        ("tests/linked", "../elsewhere"),
        ("tests/test_x.py", "../elsewhere"),
        ("tests/pkg/deep_link", "../elsewhere"),
        ("tests/test_alias.py", "test_calc.py"),  # a link to a tracked plain file: still doubt
    ],
)
def test_a_symlink_under_a_root_is_untargetable_1000(bare, link, target):  # noqa: F811 - fixture
    """#1000: pytest follows a directory link, ls-tree shows one blob: fail closed (strict only).
    Built on ``bare`` so the link is the only reason to refuse."""
    path = bare / link
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    git(bare, "add", "-A")
    git(bare, "commit", "-q", "-m", "link")
    with pytest.raises(p2p.BaseUncomparable) as info:
        p2p._base_tests(_base(bare), frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"
    assert "tests/test_calc.py" in p2p._base_tests(_base(bare), frozenset(), strict=False)


@pytest.mark.parametrize("where", ["docs/linked", "excluded"])
def test_a_symlink_outside_or_deleted_is_no_reason_to_refuse_1000(bare, where):  # noqa: F811
    exclude = frozenset()
    if where == "excluded":
        where, exclude = "tests/gone", frozenset({"tests/gone"})
    path = bare / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to("../elsewhere")
    git(bare, "add", "-A")
    git(bare, "commit", "-q", "-m", "link")
    assert p2p._base_tests(_base(bare), exclude) == ("tests/test_calc.py", "tests/test_sibling.py")


@pytest.mark.parametrize(
    ("move_tests", "link", "target", "roots", "index_only"),
    [
        (True, "tests", "real_tests", ("tests",), False),
        (True, "tests", "real_tests", ("Tests",), False),
        (True, "Tests", "real_tests", ("tests",), True),
        (False, "pkg", ".", ("pkg/tests",), False),
        (False, "a/b", "../..", ("a/b/tests",), False),
        (False, "pkg", ".", ("Pkg/Tests",), False),
        (False, "Pkg", ".", ("pkg/tests",), True),
    ],
    ids=[
        "at_root",
        "at_root_configured_case",
        "at_root_tracked_case",
        "above_nested_root",
        "above_deep_root",
        "above_nested_configured_case",
        "above_nested_tracked_case",
    ],
)
def test_a_symlink_at_or_above_a_root_is_untargetable_1289(
    bare,  # noqa: F811
    move_tests,
    link,
    target,
    roots,
    index_only,
):
    """#1289: a symlink at or above a test root makes static base listing empty while pytest
    follows it; built on ``bare`` so the link is the only reason to refuse."""
    if move_tests:
        git(bare, "mv", "tests", "real_tests")
        git(bare, "commit", "-q", "-m", "move tests")
    if index_only:
        _add_index_only(bare, link.encode(), target, mode=b"120000")
    else:
        path = bare / link
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        git(bare, "add", "-A")
        git(bare, "commit", "-q", "-m", "link")
    task = SimpleNamespace(**{**vars(_base(bare)), "test_roots": roots})
    with pytest.raises(p2p.BaseUncomparable) as info:
        p2p._base_tests(task, frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"
    assert isinstance(p2p._base_tests(task, frozenset(), strict=False), tuple)


def test_a_symlink_at_root_stops_plan_p2p_with_untargetable_1289(bare, tmp_path):  # noqa: F811
    """#1289: without rejecting the root link, plan_p2p saw 0 files and skipped P2P silently."""
    git(bare, "mv", "tests", "real_tests")
    git(bare, "commit", "-q", "-m", "move tests")
    (bare / "tests").symlink_to("real_tests")
    git(bare, "add", "-A")
    git(bare, "commit", "-q", "-m", "link root")
    task = SimpleNamespace(**vars(_base(bare)), allowed_files=("src/calc.py",), import_paths=())
    result = SimpleNamespace(attempt_id="att", logs=[])
    ctx = VerifyContext.of(task, tmp_path / "run", "att")
    with pytest.raises(p2p.BaseUncomparable) as info:
        p2p.plan_p2p(task, (), (), "whole-suite", 30, result, ctx)
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"


@pytest.mark.parametrize(
    ("where", "roots", "nested_test", "exclude", "expected"),
    [
        (
            "top_link",
            ("tests",),
            False,
            frozenset(),
            ("tests/test_calc.py", "tests/test_sibling.py"),
        ),
        ("test", ("tests",), False, frozenset(), ("tests/test_calc.py", "tests/test_sibling.py")),
        ("tests2", ("tests",), False, frozenset(), ("tests/test_calc.py", "tests/test_sibling.py")),
        ("pkg2", ("pkg/tests",), True, frozenset(), ("pkg/tests/test_calc.py",)),
        ("pkg/other", ("pkg/tests",), True, frozenset(), ("pkg/tests/test_calc.py",)),
        ("excluded_root", ("tests",), False, frozenset({"tests"}), ()),
        ("excluded_parent", ("pkg/tests",), False, frozenset({"pkg"}), ()),
    ],
    ids=[
        "unrelated_top_level",
        "shorter_prefix_no_slash",
        "longer_prefix_no_slash",
        "unrelated_near_nested",
        "sibling_under_parent",
        "excluded_root",
        "excluded_parent",
    ],
)
def test_an_unrelated_or_excluded_root_symlink_is_allowed_1289(
    bare,  # noqa: F811
    where,
    roots,
    nested_test,
    exclude,
    expected,
):
    if where == "excluded_root":
        git(bare, "mv", "tests", "real_tests")
        git(bare, "commit", "-q", "-m", "move tests")
        (bare / "tests").symlink_to("real_tests")
    elif where == "excluded_parent":
        (bare / "pkg").symlink_to(".")
    else:
        if nested_test:
            commit_change(bare, {"pkg/tests/test_calc.py": ADD})
        path = bare / where
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("../elsewhere" if "/" in where else "elsewhere")
    git(bare, "add", "-A")
    git(bare, "commit", "-q", "-m", "link")
    task = SimpleNamespace(**{**vars(_base(bare)), "test_roots": roots})
    assert p2p._base_tests(task, exclude) == expected
