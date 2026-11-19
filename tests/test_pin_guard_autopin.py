"""Three-family auto-approval of guarded changes (pin_guard.py); every miss must stay rc 1."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci/pin_guard.py"
GUARDED = "tests/pra1353_seal.py"
AUTHOR = "app/agent-author"
REVIEWERS = {"claude": "claude-rv[bot]", "codex": "codex-rv[bot]", "gemini": "gemini-rv[bot]"}
ALLOWLIST = "# list\n" + "".join(f"{login} {fam}\n" for fam, login in REVIEWERS.items())
LINES = {
    "claude": "**Independent Claude review** (Claude Opus 5.5, separate agent): **{v} at {sha}**.",
    "codex": "**Cross-model review** (Codex gpt-6-luna high, separate agent): **{v} at {sha}**.",
    "gemini": "**Cross-model review (Gemini via agy-lane)** (Gemini, separate agent): "
    "**{v} at {sha}**.",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def setup(tmp_path: Path, changed: tuple[str, ...] = (GUARDED,)) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README").write_text("base\n")
    base = commit(repo, "base")
    for path in changed:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("changed\n")
    head = commit(repo, "change")
    trusted = tmp_path / "trusted"
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text("owner\n")
    (trusted / "scripts/ci/pin_guard_reviewers.txt").write_text(ALLOWLIST)
    return repo, trusted, base, head


def record(family: str, sha: str, login: str = "", verdict: str = "ACCEPT") -> dict:
    return {
        "user": {"login": login or REVIEWERS[family]},
        "body": f"## Review record\n{LINES[family].format(v=verdict, sha=sha)}\nfindings\n",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def run(
    tmp_path: Path, trusted: Path, repo: Path, base: str, head: str, comments: list[dict], **kw: str
) -> tuple[int, str]:
    comment_file = tmp_path / "comments.json"
    comment_file.write_text(json.dumps(comments), encoding="utf-8")
    args = [sys.executable, "-I", str(SCRIPT), "--trusted", str(trusted), "--head", str(repo)]
    args += [f"--base-sha={base}", f"--head-sha={head}", "--comments", str(comment_file)]
    author = kw.get("author", AUTHOR)
    if author:
        args.append(f"--pr-author={author}")
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def all_three(sha: str, login: str = "") -> list[dict]:
    return [record(family, sha, login) for family in LINES]


def test_three_families_at_head_auto_approve(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    assert run(tmp_path, trusted, repo, base, head, all_three(head)) == (
        4,
        f"PIN_GUARD PIN_CHANGE_AUTO_APPROVED {GUARDED}",
    )


@pytest.mark.parametrize("missing", sorted(LINES))
def test_missing_one_family_is_unapproved(tmp_path: Path, missing: str) -> None:
    repo, trusted, base, head = setup(tmp_path)
    comments = [record(family, head) for family in LINES if family != missing]
    assert run(tmp_path, trusted, repo, base, head, comments)[0] == 1


def test_records_at_another_head_do_not_count(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    comments = all_three(base)[:2] + [record("gemini", head)]
    assert run(tmp_path, trusted, repo, base, head, comments)[0] == 1
    assert run(tmp_path, trusted, repo, base, head, all_three(head.upper()))[0] == 1


@pytest.mark.parametrize("author", ["app/gemini-rv", "gemini-rv[bot]", "Codex-RV[bot]"])
def test_records_posted_by_the_pr_author_do_not_count(tmp_path: Path, author: str) -> None:
    repo, trusted, base, head = setup(tmp_path)
    assert run(tmp_path, trusted, repo, base, head, all_three(head), author=author)[0] == 1


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/pin-guard-trusted.yml",
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        "CODEOWNERS",
        "docs/CODEOWNERS",
        "scripts/ci/pin_guard.py",
        "scripts/ci/pin_guard_status.py",
        "scripts/ci/pin_guard_reviewers.txt",
        "scripts/ci/pin_guard_approvers.txt",
        "tests/_w1353_allow_pins.py",
        "tests/test_pin_guard_autopin.py",
    ],
)
def test_self_guard_paths_need_the_owner(tmp_path: Path, path: str) -> None:
    repo, trusted, base, head = setup(tmp_path, (GUARDED, path))
    code, line = run(tmp_path, trusted, repo, base, head, all_three(head))
    assert code == 1 and line.startswith("PIN_GUARD PIN_CHANGE_UNAPPROVED ")
    owner = dict(record("claude", head), user={"login": "owner"}, body=f"PIN-APPROVE {head}\n")
    assert run(tmp_path, trusted, repo, base, head, [owner])[0] == 0


def test_unlisted_poster_missing_author_or_empty_list_do_not_count(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    assert run(tmp_path, trusted, repo, base, head, all_three(head, "stranger"))[0] == 1
    assert run(tmp_path, trusted, repo, base, head, all_three(head), author="")[0] == 1
    (trusted / "scripts/ci/pin_guard_reviewers.txt").write_text("# none\n")
    assert run(tmp_path, trusted, repo, base, head, all_three(head))[0] == 1
    (trusted / "scripts/ci/pin_guard_reviewers.txt").unlink()
    assert run(tmp_path, trusted, repo, base, head, all_three(head))[0] == 1


def test_reject_edit_or_mixed_family_line_blocks(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    reject = record("codex", head, "anyone", "REJECT")
    reject["updated_at"] = "2026-01-02T00:00:00Z"  # an edited REJECT still blocks
    assert run(tmp_path, trusted, repo, base, head, [*all_three(head), reject])[0] == 1
    edited = all_three(head)
    edited[2]["updated_at"] = "2026-01-02T00:00:00Z"
    assert run(tmp_path, trusted, repo, base, head, edited)[0] == 1
    wrong = all_three(head)[:2] + [record("codex", head, REVIEWERS["gemini"])]
    assert run(tmp_path, trusted, repo, base, head, wrong)[0] == 1  # login bound to gemini
    loose = all_three(head)
    loose[0]["body"] = loose[0]["body"].replace("**.", "** after fixes.")
    assert run(tmp_path, trusted, repo, base, head, loose)[0] == 1


def test_unguarded_change_is_unaffected(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path, ("src/other.py",))
    assert run(tmp_path, trusted, repo, base, head, []) == (0, "PIN_GUARD PIN_GUARD_OK")


def test_one_comment_counts_one_family_and_fences_do_not_count(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    combined = record("claude", head)
    combined["body"] += "".join(LINES[f].format(v="ACCEPT", sha=head) + "\n" for f in LINES)
    assert run(tmp_path, trusted, repo, base, head, [combined])[0] == 1
    fenced = all_three(head)
    fenced[1]["body"] = "```\n" + fenced[1]["body"] + "```\n"
    assert run(tmp_path, trusted, repo, base, head, fenced)[0] == 1


def test_one_login_cannot_stand_for_three_families(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    for login in REVIEWERS.values():
        assert run(tmp_path, trusted, repo, base, head, all_three(head, login))[0] == 1


@pytest.mark.parametrize(
    "line",
    [
        "**Independent Claude review** (Claude Opus 5.5, separate agent): **REJECT at {h}** "
        "\u2014 main reason: test gap.",
        "Verdict: REJECT at {h}",
        "verdict: reject",
        "**Independent Claude review** (Claude, separate agent): **REJECT at {H}**.",
        "Codex REJECT at {s}: two tests missing",
        "REQUEST_CHANGES",
    ],
)
def test_any_negative_verdict_on_head_blocks(tmp_path: Path, line: str) -> None:
    repo, trusted, base, head = setup(tmp_path)
    body = line.format(h=head, H=head.upper(), s=head[:8])
    reject = dict(record("claude", head, "someone"), body=f"review\n{body}\n")
    assert run(tmp_path, trusted, repo, base, head, [*all_three(head), reject])[0] == 1


def test_reject_on_an_older_head_and_gemini_substitute_header_still_pass(tmp_path: Path) -> None:
    repo, trusted, base, head = setup(tmp_path)
    old = dict(record("codex", base), body=f"**Cross-model review** (Codex): **REJECT at {base}**.")
    gemini = dict(
        record("gemini", head),
        body="**Cross-model review (Gemini via agy-lane; Codex "
        f"substitute)** (Gemini, separate agent): **ACCEPT at {head}**.",
    )
    comments = [old, *all_three(head)[:2], gemini]
    assert run(tmp_path, trusted, repo, base, head, comments)[0] == 4


@pytest.mark.parametrize(
    "allowlist",
    ["claude-rv[bot]\n", "a claude\nb opus\n", "a claude\nA codex\n", "a claude\napp/a codex\n"],
)
def test_malformed_reviewer_allowlist_fails_closed(tmp_path: Path, allowlist: str) -> None:
    repo, trusted, base, head = setup(tmp_path)
    (trusted / "scripts/ci/pin_guard_reviewers.txt").write_text(allowlist)
    assert run(tmp_path, trusted, repo, base, head, all_three(head)) == (
        2,
        "PIN_GUARD PIN_GUARD_INCOMPLETE",
    )


@pytest.mark.parametrize(
    "old,new",
    [
        (".github/CODEOWNERS", "CODEOWNERS.old"),
        ("scripts/ci/pin_guard.py", "scripts/ci/Pin_Guard.py"),
    ],
)
def test_rename_out_of_or_case_only_in_self_guard_is_seen(
    tmp_path: Path, old: str, new: str
) -> None:
    repo, trusted, _, _ = setup(tmp_path, ())
    (repo / old).parent.mkdir(parents=True, exist_ok=True)
    (repo / old).write_text("owners\n")
    base = commit(repo, "base with self-guard file")
    git(repo, "mv", "-f", old, new)  # --no-renames lists the old path as deleted
    (repo / GUARDED).parent.mkdir(parents=True, exist_ok=True)
    (repo / GUARDED).write_text("changed\n")
    head = commit(repo, "rename plus guarded change")
    assert old in git(repo, "diff", "--name-only", "--no-renames", base, head).splitlines()
    code, line = run(tmp_path, trusted, repo, base, head, all_three(head))
    assert code == 1 and line.startswith("PIN_GUARD PIN_CHANGE_UNAPPROVED ")
