from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
        f"--base-sha={base}",
        f"--head-sha={head}",
    ]
    if comments is not None:
        comment_file = repo.parent / "comments.json"
        comment_file.write_text(json.dumps(comments), encoding="utf-8")
        args += ["--comments", str(comment_file)]
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def comment(login: str, sha: str) -> dict:
    return {
        "user": {"login": login},
        "body": f"review\nPIN-APPROVE {sha}\n",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


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


@pytest.mark.parametrize("flag", ["base", "head"])
@pytest.mark.parametrize(
    "value_kind",
    ["option", "all", "HEAD", "main", "short", "empty", "newline", "space", "39", "41"],
)
def test_malformed_sha_is_incomplete(tmp_path: Path, flag: str, value_kind: str) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (repo / HELPER).write_text("edited\n")
    head = commit(repo, "guarded edit")
    bad = {
        "option": "--independent",
        "all": "--all",
        "HEAD": "HEAD",
        "main": "main",
        "short": head[:7],
        "empty": "",
        "newline": head + "\n",
        "space": head + " ",
        "39": "a" * 39,
        "41": "a" * 41,
    }[value_kind]
    code, line = invoke(
        repo, trusted, bad if flag == "base" else base, bad if flag == "head" else head
    )
    assert code == 2 and line == "PIN_GUARD PIN_GUARD_INCOMPLETE"


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_trusted_path_is_incomplete(tmp_path: Path, kind: str) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    (repo / HELPER).write_text("edited\n")
    head = commit(repo, "guarded edit")
    if kind == "file":
        trusted.write_text("not a directory")
    assert invoke(repo, trusted, base, head) == (2, "PIN_GUARD PIN_GUARD_INCOMPLETE")


@pytest.mark.parametrize("missing", ["created_at", "updated_at", "edited"])
def test_edited_or_timestamp_missing_approval_is_ignored(tmp_path: Path, missing: str) -> None:
    repo, trusted, base = setup_repo(tmp_path, approvers="alice\n")
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text("alice\n")
    (repo / HELPER).write_text("edited\n")
    head = commit(repo, "guarded edit")
    item = comment("alice", head)
    if missing == "edited":
        item["updated_at"] = "2026-01-02T00:00:00Z"
    else:
        del item[missing]
    assert invoke(repo, trusted, base, head, [item])[0] == 1


@pytest.mark.parametrize(
    "path",
    [
        HELPER,
        "tests/_w1353_allow_pins.py",
        "tests/test_1039_1353_witnesses.py",
        "tests/test_1039_1353_pin_completeness.py",
        "tests/pra1353_gate.py",
        "tests/pra1353_seal.py",
    ],
)
def test_pure_rename_is_guarded_as_old_path(tmp_path: Path, path: str) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    source = repo / path
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("content\n")
        base = commit(repo, "add fixed path")
    target = source.with_name(source.name + ".renamed")
    git(repo, "mv", str(source.relative_to(repo)), str(target.relative_to(repo)))
    head = commit(repo, "rename guarded path")
    code, line = invoke(repo, trusted, base, head)
    assert code == 1 and path in line


@pytest.mark.parametrize("base_upper,head_upper", [(False, False), (True, True)])
def test_upper_and_lower_sha_are_valid(tmp_path: Path, base_upper: bool, head_upper: bool) -> None:
    repo, trusted, base = setup_repo(tmp_path)
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (repo / "readme.txt").write_text("unrelated\n")
    head = commit(repo, "unrelated")
    assert invoke(
        repo, trusted, base.upper() if base_upper else base, head.upper() if head_upper else head
    ) == (0, "PIN_GUARD PIN_GUARD_OK")


@pytest.mark.parametrize(
    "sha_upper,line_ending", [(False, "\n"), (True, "\n"), (False, "\r\n"), (True, "\r\n")]
)
def test_valid_timestamp_approval_case_and_line_endings(
    tmp_path: Path, sha_upper: bool, line_ending: str
) -> None:
    repo, trusted, base = setup_repo(tmp_path, approvers="alice\n")
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text("alice\n")
    (repo / HELPER).write_text("edited\n")
    head = commit(repo, "guarded edit")
    item = comment("alice", head)
    approved_sha = head.upper() if sha_upper else head
    item["body"] = line_ending.join(["review", f"PIN-APPROVE {approved_sha}", ""])
    assert invoke(repo, trusted, base, head, [item])[0] == 0


@pytest.mark.parametrize("listed,login", [("alice", "Alice"), ("Alice", "alice")])
def test_approver_login_case_insensitive(tmp_path: Path, listed: str, login: str) -> None:
    repo, trusted, base = setup_repo(tmp_path, approvers=listed + "\n")
    trusted.mkdir()
    (trusted / "tests").mkdir()
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / PIN_FILE).write_bytes((repo / PIN_FILE).read_bytes())
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text(listed + "\n")
    (repo / HELPER).write_text("edited\n")
    head = commit(repo, "guarded edit")
    assert invoke(repo, trusted, base, head, [comment(login, head)])[0] == 0


def test_existing_trusted_dir_without_table_only_guards_fixed_paths(tmp_path: Path) -> None:
    repo, trusted, base = setup_repo(tmp_path, with_table=False)
    trusted.mkdir()
    (repo / "readme.txt").write_text("unrelated\n")
    unrelated = commit(repo, "unrelated")
    assert invoke(repo, trusted, base, unrelated) == (0, "PIN_GUARD PIN_GUARD_OK")
    (repo / "tests/test_1039_1353_witnesses.py").write_text("changed\n")
    head = commit(repo, "fixed path edit")
    assert invoke(repo, trusted, unrelated, head)[0] == 1


@pytest.mark.parametrize(
    "path",
    [
        "tests/pra1353_gate.py",
        "tests/pra1353_seal.py",
        ".github/workflows/ci.yml",
        ".github/workflows/pin-guard-trusted.yml",
        "scripts/ci/pin_guard.py",
        "scripts/ci/pin_guard_status.py",
    ],
)
def test_1432_gate_edit_without_pin_table_change_is_unapproved(tmp_path: Path, path: str) -> None:
    repo, trusted, base = setup_repo(tmp_path, with_table=False)
    trusted.mkdir()
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("def main() -> int:\n    return run_witness()\n")
    base = commit(repo, "gate")
    (repo / path).write_text("def main() -> int:\n    return 0\n")
    head = commit(repo, "gate returns early")
    assert invoke(repo, trusted, base, head) == (1, f"PIN_GUARD PIN_CHANGE_UNAPPROVED {path}")
