"""#1829: a new standard-library-named file where it can be an import root is refused without
any path signal in the changed files. Every case builds a real git tree and runs the real gate."""

import subprocess
from pathlib import Path

import pytest

from cairnwork import git_read_cap
from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.workspace import CandidateRejected

TEST = "import colorsys\n\n\ndef test_x():\n    assert colorsys\n"
BODY = "X = 1\n"
ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env=ENV,
    )
    return done.stdout.strip()


def _write(repo: Path, files: dict) -> None:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            target.unlink()
        elif isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)


def _gate(tmp_path: Path, base_files: dict, cand_files: dict, allowed=None):
    """Commit base, then the candidate change; return the gate's reason or ''."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, base_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base", "--allow-empty")
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, cand_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    cand = _git(repo, "rev-parse", "HEAD")
    try:
        refuse_new_sys_path_mutation(repo, base, cand, allowed or list(cand_files))
    except CandidateRejected as exc:
        return str(exc)
    return ""


def _layout(**extra):
    return {"tests/test_a.py": TEST, "pkgdir/m.py": BODY, **extra}


# must-block: a stdlib-named file in each way the base makes the directory a root
@pytest.mark.parametrize(
    ("base_files", "added"),
    [
        pytest.param(
            _layout(**{"pytest.ini": "[pytest]\npythonpath = pkgdir\n"}),
            "pkgdir/colorsys.py",
            id="ini-pythonpath",
        ),
        pytest.param(
            _layout(**{"pyproject.toml": "[tool.pytest.ini_options]\npythonpath = ['pkgdir']\n"}),
            "pkgdir/colorsys.py",
            id="pyproject-pythonpath",
        ),
        pytest.param(
            {"d1/conftest.py": "", "d1/test_a.py": TEST},
            "d1/colorsys.py",
            id="conftest-dir-no-init",
        ),
        pytest.param({"tests/test_a.py": TEST}, "tests/colorsys.py", id="tests-dir-no-init"),
        pytest.param({"tests/test_a.py": TEST}, "colorsys.py", id="repo-root-depth-0"),
        pytest.param(
            _layout(**{"conftest.py": "import sys\nsys.path.insert(0, 'pkgdir')\n"}),
            "pkgdir/colorsys.py",
            id="base-conftest-path-edit",
        ),
        pytest.param(_layout(), "pkgdir/colorsys.py", id="env-or-script-root-no-init"),
        pytest.param(_layout(), "pkgdir/colorsys.cpython-314-darwin.so", id="compiled-module"),
        pytest.param(_layout(), "pkgdir/COLORSYS.py", id="case-insensitive"),
        pytest.param(_layout(), "pkgdir/colorsys/__init__.py", id="package-form"),
        pytest.param(_layout(), "pkgdir/pytest.py", id="pytest-family"),
    ],
)
def test_added_stdlib_named_file_is_refused_without_any_signal(tmp_path, base_files, added):
    reason = _gate(tmp_path, base_files, {added: BODY})
    assert "CANDIDATE_SYS_PATH_MUTATION" in reason
    assert "standard-library" in reason


# must-pass: the directory is a package whose parent, not the directory itself, is the root
@pytest.mark.parametrize(
    ("base_files", "added"),
    [
        pytest.param(
            {
                "pytest.ini": "[pytest]\npythonpath = src\n",
                "tests/test_a.py": TEST,
                "src/pkg/__init__.py": "",
            },
            "src/pkg/colorsys.py",
            id="src-package",
        ),
        pytest.param(
            {"tests/test_a.py": TEST, "tests/pkg/__init__.py": ""},
            "tests/pkg/colorsys.py",
            id="tests-package",
        ),
        pytest.param(
            {"tests/test_a.py": TEST, "pkgdir/m.py": BODY},
            "pkgdir/helper_new.py",
            id="non-stdlib-name",
        ),
        pytest.param(
            {"tests/test_a.py": TEST, "pkgdir/m.py": BODY},
            "pkgdir/colorsys.txt",
            id="not-a-module-suffix",
        ),
    ],
)
def test_package_dir_and_ordinary_names_still_pass(tmp_path, base_files, added):
    assert _gate(tmp_path, base_files, {added: BODY}) == ""


# control: today's refusal when a changed file carries a path signal stays
def test_control_changed_file_with_path_signal_is_still_refused(tmp_path):
    base = {"pytest.ini": "[pytest]\npythonpath = pkgdir\n", **_layout()}
    cand = {
        "pkgdir/colorsys.py": BODY,
        "pkgdir/m.py": "import sys\nsys.path.insert(0, 'x')\n" + BODY,
    }
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, cand)


# fail closed: anything the check cannot read counts as a possible root
@pytest.mark.parametrize(
    "config",
    [
        pytest.param(("pytest.ini", "[pytest\nbroken"), id="unparsable-ini"),
        pytest.param(("pyproject.toml", "[tool\nbroken ="), id="unparsable-toml"),
        pytest.param(("tox.ini", b"\xff\xfe\x00bad"), id="not-utf8"),
        pytest.param(("pytest.ini", "[pytest]\n" + "# pad\n" * 800), id="over-the-read-cap"),
    ],
)
def test_unreadable_configuration_counts_as_possible_root(tmp_path, monkeypatch, config):
    name, content = config
    if len(content) > 4000:  # the 4800-byte configuration is over a 2500-byte read cap
        monkeypatch.setattr(git_read_cap, "MAX_GIT_READ", 2500)
    base = {"tests/test_a.py": TEST, "pkg/__init__.py": "", name: content}
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, {"pkg/colorsys.py": BODY})


def test_pythonpath_naming_the_package_dir_is_refused_even_with_init(tmp_path):
    base = {"pytest.ini": "[pytest]\npythonpath = pkg\n", "pkg/__init__.py": ""}
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, {"pkg/colorsys.py": BODY})


def test_pth_or_startup_file_in_the_tree_removes_the_package_exemption(tmp_path):
    for extra in ({"site/x.pth": "pkg\n"}, {"sitecustomize.py": ""}):
        sub = tmp_path / ("a" if "site/x.pth" in extra else "b")
        sub.mkdir()
        base = {"pkg/__init__.py": "", **extra}
        assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(sub, base, {"pkg/colorsys.py": BODY})


def test_path_edit_anywhere_in_python_source_removes_the_package_exemption(tmp_path):
    base = {"pkg/__init__.py": "", "tools/run.py": "import sys\nsys.path.append('.')\n"}
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, {"pkg/colorsys.py": BODY})


# scope: rename, copy and type change count as new; files outside allowed are not judged
def test_rename_into_a_stdlib_name_is_refused(tmp_path):
    base = {"pkgdir/other.py": BODY, "tests/test_a.py": TEST}
    cand = {"pkgdir/other.py": None, "pkgdir/colorsys.py": BODY}
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, cand)


def test_copy_into_a_stdlib_name_is_refused(tmp_path):
    base = {"pkgdir/other.py": BODY, "tests/test_a.py": TEST}
    cand = {"pkgdir/colorsys.py": BODY}
    assert "CANDIDATE_SYS_PATH_MUTATION" in _gate(tmp_path, base, cand)


def test_unchanged_existing_stdlib_named_file_is_not_judged(tmp_path):
    base = {"pkgdir/colorsys.py": BODY, "tests/test_a.py": TEST}
    assert _gate(tmp_path, base, {"pkgdir/m.py": BODY}) == ""


def test_added_file_outside_allowed_is_not_judged_here(tmp_path):
    base = {"tests/test_a.py": TEST}
    cand = {"tests/colorsys.py": BODY, "tests/note.txt": "n\n"}
    assert _gate(tmp_path, base, cand, allowed=["tests/note.txt"]) == ""


def test_type_change_to_a_regular_file_counts_as_new(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pkgdir").mkdir()
    (repo / "pkgdir" / "colorsys.py").symlink_to("other.py")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text(TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "pkgdir" / "colorsys.py").unlink()
    (repo / "pkgdir" / "colorsys.py").write_text(BODY)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    cand = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(CandidateRejected):
        refuse_new_sys_path_mutation(repo, base, cand, ["pkgdir/colorsys.py"])


# the name list is fixed, not read from the interpreter that runs the gate
def test_name_list_is_fixed_and_covers_removed_modules():
    from cairnwork import candidate_stdlib_root as mod

    for name in ("json", "colorsys", "random", "distutils", "imp", "asyncore", "tomllib"):
        assert name in mod._STDLIB
    assert mod._import_dir("a/b/distutils.py") == "a/b"
    assert mod._import_dir("a/b/notstd.py") is None
    assert mod._import_dir("json/__init__.py") == ""


@pytest.mark.parametrize("how", ["env-root", "script-dir"])
def test_known_residual_root_made_outside_repo_with_init_passes_1829(tmp_path, how):
    """#1829 residual: a directory with ``__init__.py`` that only the environment or the verifier
    command turns into a search root is not seen by a static gate. Pinned so a change to it is a
    decision; the planned run-time module-origin check is the real answer."""
    cand = {"pkgdir/__init__.py": "", "pkgdir/colorsys.py": BODY}
    assert _gate(tmp_path, {"README.md": "x\n"}, cand) == "", how


# review conditions at dd9514fb (#1829): newer pytest config forms, addopts overrides, other
# package-marker suffixes, a repo-root package marker, and wrong-shaped config
_PKG = {"tests/test_a.py": TEST, "pkg/__init__.py": ""}


@pytest.mark.parametrize(
    ("cfg_name", "cfg"),
    [
        ("pytest.toml", '[pytest]\npythonpath = ["pkg"]\n'),
        (".pytest.toml", '[pytest]\npythonpath = ["pkg"]\n'),
        (".pytest.ini", "[pytest]\npythonpath = pkg\n"),
        ("pyproject.toml", '[tool.pytest]\npythonpath = ["pkg"]\n'),
        ("pytest.ini", "[pytest]\naddopts = -o pythonpath=pkg\n"),
        ("pytest.ini", "[pytest]\naddopts = --override-ini=pythonpath=pkg\n"),
        ("pytest.ini", "[pytest]\naddopts = -opythonpath=pkg\n"),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-o pythonpath=pkg"\n'),
        ("pyproject.toml", "[tool.pytest.ini_options]\npythonpath = 5\n"),
        ("pyproject.toml", "tool = 1\n"),
    ],
)
def test_newer_pytest_config_forms_make_package_dir_a_root_1829(tmp_path, cfg_name, cfg):
    base = {**_PKG, cfg_name: cfg}
    assert _gate(tmp_path, base, {"pkg/colorsys.py": BODY}) != ""


# Claude review at 89f276ea: pytest reads ini-style ``pythonpath`` and ``addopts`` with shlex, so
# quotes and backslashes change the value the gate would see
@pytest.mark.parametrize(
    ("cfg_name", "cfg"),
    [
        ("pytest.ini", '[pytest]\npythonpath = "pkg"\n'),
        ("pytest.ini", "[pytest]\npythonpath = 'pkg'\n"),
        ("pytest.ini", "[pytest]\npythonpath = p\\kg\n"),
        ("pytest.ini", '[pytest]\npythonpath = "p"kg\n'),
        ("pytest.ini", "[pytest]\npythonpath = './pkg'\n"),
        ("tox.ini", '[pytest]\npythonpath = "pkg"\n'),
        ("setup.cfg", "[tool:pytest]\npythonpath = 'pkg'\n"),
        ("pyproject.toml", "[tool.pytest.ini_options]\npythonpath = \"'pkg'\"\n"),
        ("pytest.ini", "[pytest]\naddopts = '-'o pyth''onpath=pkg\n"),
        ("pytest.ini", "[pytest]\naddopts = -qo pyth''onpath=pkg\n"),
        ("pytest.ini", '[pytest]\naddopts = "--override-ini" pyth""onpath=pkg\n'),
        ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = \"-q'o' pyth''onpath=pkg\"\n"),
    ],
)
def test_shell_quoted_config_values_are_untrusted_1829(tmp_path, cfg_name, cfg):
    base = {**_PKG, cfg_name: cfg}
    assert _gate(tmp_path, base, {"pkg/colorsys.py": BODY}) != ""


# Claude review at e67db87a: pytest splits with shlex (only space, tab, CR, LF are blanks), and a
# list-form addopts is not shlex-split at all
_ODD = "[pytest]\npythonpath = a{c}pkg\n"


@pytest.mark.parametrize(
    ("cfg_name", "cfg", "char"),
    [
        ("pytest.ini", _ODD, "\u00a0"),
        ("pytest.ini", _ODD, "\u3000"),
        ("pytest.ini", _ODD, "\u2003"),
        ("pytest.ini", _ODD, "\u205f"),
        ("pytest.ini", _ODD, "\x1f"),
        ("pytest.ini", _ODD, "\x0b"),
        ("setup.cfg", "[tool:pytest]\npythonpath = a{c}pkg\n", "\u00a0"),
        ("tox.ini", _ODD, "\u3000"),
        ("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = "a{c}pkg"\n', "\u00a0"),
    ],
)
def test_non_shlex_whitespace_in_config_values_is_untrusted_1829(tmp_path, cfg_name, cfg, char):
    base = {"tests/test_a.py": TEST, f"a{char}pkg/__init__.py": "", cfg_name: cfg.format(c=char)}
    assert _gate(tmp_path, base, {f"a{char}pkg/colorsys.py": BODY}) != ""


@pytest.mark.parametrize(
    ("cfg_name", "cfg"),
    [
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = ["-ra"]\n'),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = ["-ra", "--strict-markers"]\n'),
        ("pytest.toml", '[pytest]\naddopts = ["-q"]\n'),
        ("pytest.ini", "[pytest]\npythonpath = src\ttests\n"),
        ("pytest.ini", "[pytest]\naddopts = -q\n"),
    ],
)
def test_list_addopts_without_override_keeps_exemption_1829(tmp_path, cfg_name, cfg):
    base = {**_PKG, cfg_name: cfg}
    assert _gate(tmp_path, base, {"pkg/colorsys.py": BODY}) == ""


@pytest.mark.parametrize(
    "marker",
    ["__init__.pyc", "__init__.so", "__init__.abi3.so", "__init__.cpython-314-darwin.so"],
)
def test_package_marker_other_suffixes_are_judged_1829(tmp_path, marker):
    base = {"tests/test_a.py": TEST, "pkgdir/m.py": BODY}
    assert _gate(tmp_path, base, {f"pkgdir/colorsys/{marker}": b"\0"}) != ""


def test_repo_root_package_marker_does_not_exempt_depth_zero_1829(tmp_path):
    assert (
        _gate(tmp_path, {"__init__.py": "", "tests/test_a.py": TEST}, {"colorsys.py": BODY}) != ""
    )


@pytest.mark.parametrize(
    ("cfg_name", "cfg"),
    [
        ("pytest.toml", '[pytest]\npythonpath = ["src"]\n'),
        ("pyproject.toml", '[tool.pytest]\npythonpath = ["src"]\n'),
    ],
)
def test_newer_config_naming_other_dir_keeps_package_exempt_1829(tmp_path, cfg_name, cfg):
    base = {"tests/test_a.py": TEST, "src/pkg/__init__.py": "", cfg_name: cfg}
    assert _gate(tmp_path, base, {"src/pkg/colorsys.py": BODY}) == ""


# second review round (#1829): a case-variant config name or value, and a non-string list item
@pytest.mark.parametrize(
    ("cfg_name", "cfg"),
    [
        ("PYTEST.INI", "[pytest]\npythonpath = pkg\n"),
        ("Pyproject.TOML", '[tool.pytest.ini_options]\npythonpath = ["pkg"]\n'),
        ("pytest.ini", "[pytest]\npythonpath = PKG\n"),
        ("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["Pkg"]\n'),
        ("pyproject.toml", "[tool.pytest.ini_options]\npythonpath = [1]\n"),
    ],
)
def test_case_variants_and_non_string_items_are_not_skipped_1829(tmp_path, cfg_name, cfg):
    base = {**_PKG, cfg_name: cfg}
    assert _gate(tmp_path, base, {"pkg/colorsys.py": BODY}) != ""


def test_unicode_equivalent_directory_names_match_1829(tmp_path):
    """APFS treats a precomposed and a decomposed name as one directory."""
    cfg = '[tool.pytest.ini_options]\npythonpath = ["cafe\\u0301"]\n'
    base = {"tests/test_a.py": TEST, "café/__init__.py": "", "pyproject.toml": cfg}
    assert _gate(tmp_path, base, {"café/colorsys.py": BODY}) != ""
