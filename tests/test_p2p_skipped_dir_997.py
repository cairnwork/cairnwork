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

from cairnwork import p2p, p2p_links

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
                           test_roots=("tests",))  # fmt: skip


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


@pytest.mark.parametrize("link", ["tests/linked", "tests/test_x.py", "tests/pkg/deep_link"])
def test_a_symlink_under_a_root_is_untargetable_1000(bare, link):  # noqa: F811 - the fixture
    """#1000: pytest follows a directory link, ls-tree shows one blob: fail closed (strict only).
    Built on ``bare`` so the link is the only reason to refuse."""
    path = bare / link
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to("../elsewhere")
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
    ("link", "target", "refused"),
    [
        (
            "tests/test_to_file.py",
            "test_calc.py",
            False,
        ),  # a plain tracked file: pytest runs that one
        ("tests/to_dir", "../docs", True),
        ("tests/to_missing", "nowhere.py", True),
        ("tests/to_link.py", "test_to_file.py", True),  # a link to a link is doubt
        ("tests/escape.py", "../../outside.py", True),
    ],
)
def test_only_a_link_to_a_plain_tracked_file_is_no_doubt_1000(bare, link, target, refused):  # noqa: F811
    (bare / "docs").mkdir(exist_ok=True)
    (bare / "docs/readme.txt").write_text("x\n")
    (bare / "tests/test_to_file.py").symlink_to("test_calc.py")
    (bare / link).parent.mkdir(parents=True, exist_ok=True)
    if not (bare / link).is_symlink():
        (bare / link).symlink_to(target)
    git(bare, "add", "-A")
    git(bare, "commit", "-q", "-m", "links")
    if refused:
        with pytest.raises(p2p.BaseUncomparable) as info:
            p2p._base_tests(_base(bare), frozenset())
        assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"
    else:
        assert "tests/test_to_file.py" in p2p._base_tests(_base(bare), frozenset())


@pytest.mark.parametrize("target", [b"", b"bad\0target", b"/absolute.py"])
def test_invalid_symlink_target_is_doubt(bare, monkeypatch, target):  # noqa: F811
    monkeypatch.setattr(p2p_links, "run_git_bytes", lambda *_: target)
    assert not p2p_links._plain_file_link(
        _base(bare), b"tests/link.py", b"0" * 40, {b"tests/absolute.py": b"100644"}
    )


def test_unreadable_symlink_target_is_doubt(bare, monkeypatch):  # noqa: F811
    def fail(*_):
        raise p2p_links.GitCommandError("git cat-file failed")

    monkeypatch.setattr(p2p_links, "run_git_bytes", fail)
    assert not p2p_links._plain_file_link(_base(bare), b"tests/link.py", b"0" * 40, {})
