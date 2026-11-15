"""Real temporary git repository integration tests for committed_sys_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from experimental.committed_sys_path import check_commit, check_source
from experimental.committed_sys_path.cli import main


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return p.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "bin").mkdir()
    (repo / "bin" / "tool.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "src" / "shadow").mkdir(parents=True)
    (repo / "src" / "shadow" / "keep.py").write_text("Y = 1\n", encoding="utf-8")
    (repo / "src" / "old.py").write_text("Z = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_git_clean_accept_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean relative ../bin edit accepts (CLI exit 0); disabled CLI exits 2."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "cand")
    cand = _git(repo, "rev-parse", "HEAD")

    assert check_commit(repo, base, cand).verdict == "ACCEPT"
    monkeypatch.delenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", raising=False)
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 2
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 0


def test_git_subpackage_shadow_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding src/shadow/calc/__init__.py rejects parent src/shadow (CX-1)."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "shadow" / "calc").mkdir(parents=True)
    (repo / "src" / "shadow" / "calc" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
    (repo / "bin" / "runner.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'shadow'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "shadow")
    cand = _git(repo, "rev-parse", "HEAD")

    res = check_commit(repo, base, cand)
    assert res.verdict == "REJECT"
    assert res.reason == "SHADOW_IN_COMMITTED_DIR"
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 1


def test_git_non_ascii_and_delete_only(tmp_path: Path) -> None:
    """Non-ASCII filename is checked (-z, CX-12); delete-only commit accepts."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "\u00e9vil.py").write_text(
        "import sys\nsys.path.insert(0, '/tmp')\n", encoding="utf-8"
    )
    _git(repo, "add", "src/\u00e9vil.py")
    _git(repo, "commit", "-m", "nonascii")
    cand = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand).verdict == "REJECT"

    repo2, base2 = _init_repo(tmp_path / "del")
    (repo2 / "src" / "old.py").unlink()
    _git(repo2, "add", "-u")
    _git(repo2, "commit", "-m", "delete")
    cand2 = _git(repo2, "rev-parse", "HEAD")
    assert check_commit(repo2, base2, cand2).verdict == "ACCEPT"


def test_git_symlink_dir_and_invalid_oid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate replacing dir with symlink rejects (CX-13); invalid OID errors."""
    repo, base = _init_repo(tmp_path)
    (repo / "bin" / "tool.py").unlink()
    (repo / "bin").rmdir()
    (repo / "bin").symlink_to("src/shadow")
    (repo / "src" / "app.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "symlink")
    cand = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand).verdict == "REJECT"

    assert check_commit(repo, "bad-ref", cand).verdict == "ERROR"
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", "bad-ref", "--candidate", cand]) == 1


def test_git_symlink_dotdot_traversal_rejected(tmp_path: Path) -> None:
    """Symlink .. traversal in candidate git commit rejects as NON_COMMITTED_SEARCH_PATH."""
    repo, _ = _init_repo(tmp_path)
    (repo / "linkdir").mkdir()
    (repo / "linkdir" / "keep.py").write_text("K = 1\n", encoding="utf-8")
    (repo / "src" / "app.py").write_text("A = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "with-linkdir")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "linkdir" / "keep.py").unlink()
    (repo / "linkdir").rmdir()
    (repo / "linkdir").symlink_to("/tmp/evilA/b")
    (repo / "src" / "app.py").write_text(
        "import os, sys\n"
        "HERE = os.path.dirname(__file__)\n"
        "sys.path.insert(0, os.path.join(HERE, '..', 'linkdir', '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "symlink-traversal")
    cand = _git(repo, "rev-parse", "HEAD")

    res = check_commit(repo, base, cand)
    assert res.verdict == "REJECT"
    assert res.reason == "NON_COMMITTED_SEARCH_PATH"


@pytest.mark.parametrize(
    ("shadow_name", "extra_base"),
    [
        ("json.py", None),
        ("mod.py", "src/mod.py"),
    ],
    ids=["stdlib_json_shadow", "base_module_mod_shadow"],
)
def test_git_unchanged_search_path_new_shadow_rejected(
    tmp_path: Path, shadow_name: str, extra_base: str | None
) -> None:
    """Unchanged ../sub sys.path.insert in base + new shadow in sub rejects."""
    repo, _ = _init_repo(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "keep.py").write_text("S = 1\n", encoding="utf-8")
    if extra_base is not None:
        (repo / extra_base).write_text("M = 1\n", encoding="utf-8")
    (repo / "src" / "app.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sub'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base-with-sub-insert")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "sub" / shadow_name).write_text("SHADOW = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add-shadow")
    cand = _git(repo, "rev-parse", "HEAD")

    res = check_commit(repo, base, cand)
    assert res.verdict == "REJECT"
    assert res.reason == "SHADOW_IN_COMMITTED_DIR"


def test_git_legitimate_sibling_helper_accepts(tmp_path: Path) -> None:
    """Legitimate sibling helper without shadow collisions accepts; cross-dir rejects."""
    repo, _ = _init_repo(tmp_path)
    (repo / "bin" / "issue_flow.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base-issue-flow")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "bin" / "issue_flow.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))\n"
        "from procgroup import run_group\n\n"
        "def main():\n    return run_group()\n",
        encoding="utf-8",
    )
    (repo / "bin" / "procgroup.py").write_text("def run_group():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_procgroup_372.py").write_text("import procgroup\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "sibling-helper")
    cand = _git(repo, "rev-parse", "HEAD")

    assert check_commit(repo, base, cand).verdict == "ACCEPT"
    assert check_commit(repo, base, cand, include_tests=True).verdict == "ACCEPT"

    (repo / "src" / "cross.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "cross-dir-bin")
    cand_cross = _git(repo, "rev-parse", "HEAD")
    res_cross = check_commit(repo, base, cand_cross)
    assert res_cross.verdict == "REJECT"
    assert res_cross.reason == "SHADOW_IN_COMMITTED_DIR"


def test_git_deep_ast_commit_returns_error(tmp_path: Path) -> None:
    """Deep AST in candidate commit returns ERROR UNREADABLE_SOURCE via check_commit."""
    repo, base = _init_repo(tmp_path)
    chain = "x" + ".a" * 5_000
    (repo / "src" / "deep.py").write_text(
        f"import sys\nx = object()\nsys.path.insert(0, {chain})\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "deep-ast")
    cand = _git(repo, "rev-parse", "HEAD")
    res = check_commit(repo, base, cand)
    assert res.verdict == "ERROR"
    assert res.reason == "UNREADABLE_SOURCE"


def test_git_mutants_m5_m12_m13_error_propagation_and_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5: invalid syntax returns ERROR; M12+M13: option-injection guards reject & isolate."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "a_valid.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    (repo / "src" / "b_bad.py").write_bytes(b"def x(:\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mixed-valid-and-bad")
    cand = _git(repo, "rev-parse", "HEAD")

    assert check_commit(repo, base, cand).verdict == "ERROR"

    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess:
        if isinstance(cmd, list):
            calls.append(cmd)
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", spy_run)
    res_flag1 = check_commit(repo, "-x", "HEAD")
    res_flag2 = check_commit(repo, "HEAD~1", "--help")
    assert res_flag1.verdict == "ERROR" and res_flag1.reason == "INVALID_COMMIT"
    assert res_flag2.verdict == "ERROR" and res_flag2.reason == "INVALID_COMMIT"
    assert not any("-x" in arg or "--help" in arg for cmd in calls for arg in cmd)

    assert check_commit(repo, base, cand).verdict == "ERROR"
    rev_cmds = [c for c in calls if "rev-parse" in c]
    assert rev_cmds and all("--end-of-options" in c for c in rev_cmds)


def test_git_mutants_m14_m15_corrupt_diff_and_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M14: diff failure returns ERROR; M15: missing candidate or base blob returns ERROR."""
    repo, _ = _init_repo(tmp_path)
    (repo / "src" / "mod.py").write_text("M = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base-with-mod")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "mod.py").write_text("M = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "modify-mod")
    cand_mod = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", base)
    (repo / "src" / "app.py").write_text("APP_UNIQUE_MARKER = 42\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "app-commit")
    cand = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "old.py").write_text("Z = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "modify-old")
    cand2 = _git(repo, "rev-parse", "HEAD")

    real_run = subprocess.run

    def fail_diff(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess:
        if isinstance(cmd, list) and "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: bad diff")
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fail_diff)
    res_diff = check_commit(repo, base, cand)
    assert res_diff.verdict == "ERROR"
    assert res_diff.reason != "INVALID_COMMIT"

    monkeypatch.undo()
    mod_oid = _git(repo, "rev-parse", f"{cand_mod}:src/mod.py")
    (repo / ".git" / "objects" / mod_oid[:2] / mod_oid[2:]).unlink()
    res_mod_blob = check_commit(repo, base, cand_mod)
    assert res_mod_blob.verdict == "ERROR"
    assert res_mod_blob.reason != "INVALID_COMMIT"

    base_oid = _git(repo, "rev-parse", f"{cand}:src/old.py")
    (repo / ".git" / "objects" / base_oid[:2] / base_oid[2:]).unlink()
    res_base_blob = check_commit(repo, cand, cand2)
    assert res_base_blob.verdict == "ERROR"
    assert res_base_blob.reason != "INVALID_COMMIT"

    blob_oid = _git(repo, "rev-parse", f"{cand}:src/app.py")
    (repo / ".git" / "objects" / blob_oid[:2] / blob_oid[2:]).unlink()
    res_blob = check_commit(repo, base, cand)
    assert res_blob.verdict == "ERROR"
    assert res_blob.reason != "INVALID_COMMIT"


def test_git_mutant_r2_5a_deleted_base_dir_rejected(tmp_path: Path) -> None:
    """R2-5a: candidate deleting base dir rejects ../bin as NON_COMMITTED_SEARCH_PATH."""
    repo, base = _init_repo(tmp_path)
    (repo / "bin" / "tool.py").unlink()
    (repo / "src" / "app.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "delete-bin-dir")
    cand = _git(repo, "rev-parse", "HEAD")

    res = check_commit(repo, base, cand)
    assert res.verdict == "REJECT"
    assert res.reason == "NON_COMMITTED_SEARCH_PATH"


def test_git_mutant_m18_unchanged_dynamic_insert(tmp_path: Path) -> None:
    """M18: unchanged sys.path.insert(0, '/tmp') accepts as NO_NEW_PATH_EDITS; edits reject."""
    old_src = b"import sys\nsys.path.insert(0, '/tmp')\nX = 1\n"
    new_src = b"import sys\nsys.path.insert(0, '/tmp')\nX = 2\n"
    res = check_source("src/x.py", old_src, new_src, frozenset({"src"}))
    assert res.verdict == "ACCEPT"
    assert res.reason == "NO_NEW_PATH_EDITS"

    mod_src = b"import sys\nsys.path.insert(0, '/tmp2')\nX = 2\n"
    assert check_source("src/x.py", old_src, mod_src, frozenset({"src"})).verdict == "REJECT"

    shadow_old = (
        b"import os, sys\n"
        b"sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shadow'))\nX = 1\n"
    )
    shadow_new = (
        b"import os, sys\n"
        b"sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shadow'))\nX = 2\n"
    )
    assert (
        check_source(
            "src/x.py",
            shadow_old,
            shadow_new,
            frozenset({"src", "src/shadow"}),
            shadow_dirs=frozenset({"src/shadow"}),
        ).verdict
        == "REJECT"
    )

    repo, _ = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_bytes(old_src)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base-tmp")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "app.py").write_bytes(new_src)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "edit-other-line")
    cand = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand).verdict == "ACCEPT"

    (repo / "src" / "app.py").write_bytes(mod_src)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "modify-tmp-insert")
    cand_mod = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand_mod).verdict == "REJECT"
