from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/ci/pin_guard.py"
HELPER = "tests/_w1353_probe.py"
PIN_FILE = "tests/_w1353_allow_pins.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def setup_repo(
    tmp_path: Path, *, with_table: bool = True, approvers: str | None = None
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "tests").mkdir()
    (repo / "scripts/ci").mkdir(parents=True)
    (repo / HELPER).write_bytes(b"original helper\n")
    if with_table:
        digest = hashlib.sha256((repo / HELPER).read_bytes()).hexdigest()
        (repo / PIN_FILE).write_text(
            'TRUSTED_HELPER_PINS: dict[str, str] = dict(zip(("tests/_w" + '
            '"1353_probe.py",), ("' + digest + '",), strict=True))\n',
            encoding="utf-8",
        )
    if approvers is not None:
        (repo / "scripts/ci/pin_guard_approvers.txt").write_text(approvers, encoding="utf-8")
    base = commit(repo, "base")
    return repo, tmp_path / "trusted", base


def invoke(
    repo: Path, trusted: Path, base: str, head: str, comments: list[dict] | None = None
) -> tuple[int, str]:
    args = [
        sys.executable,
        "-I",
        str(SCRIPT),
        "--trusted",
        str(trusted),
        "--head",
        str(repo),
        "--base-sha",
        base,
        "--head-sha",
        head,
    ]
    if comments is not None:
        comment_file = repo.parent / "comments.json"
        comment_file.write_text(json.dumps(comments), encoding="utf-8")
        args += ["--comments", str(comment_file)]
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def comment(login: str, sha: str) -> dict:
    return {"user": {"login": login}, "body": f"review\nPIN-APPROVE {sha}\n"}


def test_ac1_pinned_helper_and_entry_change_requires_approval(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (repo / HELPER).write_bytes(b"changed helper\n")
    digest = hashlib.sha256((repo / HELPER).read_bytes()).hexdigest()
    (repo / PIN_FILE).write_text(f'TRUSTED_HELPER_PINS = {{"{HELPER}": "{digest}"}}\n')
    head = commit(repo, "change pinned helper and hash")
    code, line = invoke(repo, trusted, base, head)
    assert code == 1
    assert line == f"PIN_GUARD PIN_CHANGE_UNAPPROVED {PIN_FILE},{HELPER}"

    empty_repo, empty_trusted, empty_base = setup_repo(tmp_path / "negative", with_table=False)
    empty_trusted.mkdir()
    (empty_repo / HELPER).write_bytes(b"changed, but not registered\n")
    empty_head = commit(empty_repo, "unregistered helper")
    assert invoke(empty_repo, empty_trusted, empty_base, empty_head) == (
        0,
        "PIN_GUARD PIN_GUARD_OK",
    )


def test_ac2_pin_table_change_requires_approval(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (repo / PIN_FILE).write_text('TRUSTED_HELPER_PINS = {"tests/new.py": "' + "a" * 64 + '"}\n')
    head = commit(repo, "change table")
    assert invoke(repo, trusted, base, head) == (1, f"PIN_GUARD PIN_CHANGE_UNAPPROVED {PIN_FILE}")


def test_ac3_missing_table_still_guards_table_and_witnesses(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path, with_table=False)
    trusted.mkdir()
    (repo / PIN_FILE).write_text("TRUSTED_HELPER_PINS = {}\n")
    table_head = commit(repo, "add pin table")
    assert invoke(repo, trusted, base, table_head) == (
        1,
        f"PIN_GUARD PIN_CHANGE_UNAPPROVED {PIN_FILE}",
    )
    (repo / "tests/test_1039_1353_witnesses.py").write_text("# changed\n")
    witness_head = commit(repo, "change witness")
    assert invoke(repo, trusted, table_head, witness_head) == (
        1,
        "PIN_GUARD PIN_CHANGE_UNAPPROVED tests/test_1039_1353_witnesses.py",
    )


def test_ac4_approvals_are_trusted_and_bound_to_head(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path, approvers="")
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text("alice\n", encoding="utf-8")
    (repo / HELPER).write_bytes(b"changed\n")
    head = commit(repo, "guarded change")
    assert invoke(repo, trusted, base, head, [comment("alice", head)]) == (
        0,
        f"PIN_GUARD PIN_CHANGE_APPROVED {HELPER}",
    )
    assert invoke(repo, trusted, base, head, [comment("alice", "0" * 40)]) == (
        1,
        f"PIN_GUARD PIN_APPROVAL_STALE {HELPER}",
    )
    assert invoke(repo, trusted, base, head, [comment("bob", head)]) == (
        1,
        f"PIN_GUARD PIN_APPROVER_NOT_ALLOWED {HELPER}",
    )
    (repo / "scripts/ci/pin_guard_approvers.txt").write_text("alice\n")
    head_with_list = commit(repo, "add untrusted approver")
    empty_trusted = tmp_path / "empty-trusted"
    empty_trusted.mkdir()
    (empty_trusted / "tests").mkdir()
    (empty_trusted / "tests" / Path(PIN_FILE).name).write_bytes((repo / PIN_FILE).read_bytes())
    assert (
        invoke(repo, empty_trusted, base, head_with_list, [comment("alice", head_with_list)])[0]
        == 1
    )


def test_ac5_unrelated_and_head_only_keys_do_not_expand_guard(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (repo / "readme.txt").write_text("unrelated\n")
    unrelated = commit(repo, "unrelated")
    assert invoke(repo, trusted, base, unrelated) == (0, "PIN_GUARD PIN_GUARD_OK")
    (repo / "tests/new_head_only.py").write_text("new\n")
    (repo / PIN_FILE).write_text(
        'TRUSTED_HELPER_PINS = {"tests/new_head_only.py": "' + "b" * 64 + '"}\n'
    )
    head = commit(repo, "head changes table and new file")
    assert invoke(repo, trusted, base, head) == (1, f"PIN_GUARD PIN_CHANGE_UNAPPROVED {PIN_FILE}")


def test_ac6_head_table_change_is_guarded_even_when_helper_matches_main_pin(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (trusted / HELPER).write_bytes((repo / HELPER).read_bytes())
    (repo / HELPER).chmod(0o755)
    (repo / PIN_FILE).write_text('TRUSTED_HELPER_PINS = {"tests/other.py": "' + "c" * 64 + '"}\n')
    head = commit(repo, "change head pin table")
    assert (
        hashlib.sha256((repo / HELPER).read_bytes()).hexdigest()
        == hashlib.sha256((trusted / HELPER).read_bytes()).hexdigest()
    )
    assert invoke(repo, trusted, base, head) == (
        1,
        f"PIN_GUARD PIN_CHANGE_UNAPPROVED {PIN_FILE},{HELPER}",
    )


def test_failures_fail_closed_and_empty_approvers(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_text("TRUSTED_HELPER_PINS = dict(\n")
    assert invoke(repo, trusted, base, base) == (2, "PIN_GUARD PIN_GUARD_INCOMPLETE")
    assert invoke(repo, trusted, "bad-base", base) == (2, "PIN_GUARD PIN_GUARD_INCOMPLETE")
    repo2, trusted2, base2 = setup_repo(tmp_path / "empty", approvers="")
    trusted2.mkdir()
    (trusted2 / "tests").mkdir()
    (trusted2 / PIN_FILE).write_bytes((repo2 / PIN_FILE).read_bytes())
    (repo2 / HELPER).write_bytes(b"edited\n")
    head2 = commit(repo2, "guarded")
    assert invoke(repo2, trusted2, base2, head2, [comment("alice", head2)])[0] == 1
