"""repo_context reads blobs in a few batched git calls and gives the same text as one git show per
file (blind_warn, Ultra Review handoff 2026-11-03: about 100 s and 8,000 git calls on an
11,000-file repository)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import GitCommandError, run_git
from experimental.blind_warn import judge


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout  # fmt: skip


def _repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path / "r"
    _git(tmp_path, "init", "-q", "--template=", str(repo))
    for name, data in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return repo


def _reference(base: Path, ticket: str) -> str:
    """The text before the batched reader: one `git show` per ranked file, in order."""
    files, skipped = judge._listing(base)
    words = judge._tokens(ticket)
    ranked = sorted(
        (n for n, _, _ in files if n.endswith(judge._TEXT_SUFFIXES)),
        key=lambda n: (-len(words & judge._tokens(n.replace("/", " ").replace(".", " "))), n),
    )
    parts, used = (
        [f"--- SKIPPED: {skipped} file names with control characters"] if skipped else [],
        0,
    )
    for name in ranked:
        try:
            text = run_git(base, "show", f"HEAD:{name}").encode()[: judge.MAX_FILE_BYTES]
            text = text.decode("utf-8", "ignore")
        except (GitCommandError, UnicodeDecodeError):
            continue
        if used + len(text.encode()) > judge.CONTEXT_BYTES:
            continue
        parts.append(f"--- FILE: {name}\n{text}")
        used += len(text.encode())
    return "\n".join(parts)


@pytest.fixture
def mixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(judge, "CONTEXT_BYTES", 900)
    monkeypatch.setattr(judge, "MAX_FILE_BYTES", 300)
    monkeypatch.setattr(judge, "_BATCH", 3)
    files = {
        "src/crlf.py": b"a = 1\r\nb = 2\r\n", "src/cr.txt": b"one\rtwo\r",
        "src/latin1.md": b"caf\xe9\n", "src/empty.py": b"", "src/long.md": b"L" * 1000,
        "src/wide.md": "中文".encode() * 120, "src/tool.py": b"#!/bin/sh\n",
        "docs/ticket_words.md": b"foo bar\n", "zz/huge.txt": b"H" * 5000,
    }  # fmt: skip
    files |= {f"pkg/m{i:02}.py": b"x" * (i * 7) for i in range(30)}
    repo = _repo(tmp_path, files)
    os.chmod(repo / "src/tool.py", 0o755)
    os.symlink("src/crlf.py", repo / "link.py")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "more")
    return repo


@pytest.mark.parametrize("ticket", ["", "ticket words foo", "wide long crlf", "m07 m29 pkg"])
def test_the_text_equals_one_git_show_per_file(mixed: Path, ticket: str) -> None:
    got = judge.repo_context(mixed, ticket)
    assert got == _reference(mixed, ticket)
    assert "--- FILE: link.py" not in got and "\r" not in got


def test_a_large_repository_costs_a_few_git_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, {f"d{i // 100}/f{i:04}.py": b"y" * 2000 for i in range(1000)})
    seen: list[list[str]] = []
    real = gitcmd.subprocess.run
    monkeypatch.setattr(gitcmd.subprocess, "run", lambda a, **k: seen.append(a) or real(a, **k))
    text = judge.repo_context(repo, "")
    assert text.count("--- FILE: ") == judge.CONTEXT_BYTES // 2000
    commands = [next(x for x in ("ls-tree", "cat-file", "show") if x in a) for a in seen
                if any(x in a for x in ("ls-tree", "cat-file", "show"))]  # fmt: skip
    # 1 listing + one batch per 200 files while space is left; once full, nothing more is read
    assert commands.count("show") == 0 and commands.count("ls-tree") == 1
    assert commands.count("cat-file") <= -(-(judge.CONTEXT_BYTES // 2000) // judge._BATCH) + 1


def test_limits_count_bytes_for_non_ascii_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "CONTEXT_BYTES", 1000)
    monkeypatch.setattr(judge, "MAX_FILE_BYTES", 100)
    repo = _repo(tmp_path, {f"w{i}.md": "中".encode() * 200 for i in range(20)})
    got = judge.repo_context(repo, "")
    for part in got.split("--- FILE: ")[1:]:
        assert len(part.split("\n", 1)[1].rstrip("\n").encode()) <= 100  # 33 chars, not 100
    assert got.count("--- FILE: ") == 10  # 99 bytes each; 10 fit in 1000 bytes (11 would not)


@pytest.mark.parametrize(
    ("data", "context"),
    [("中".encode() * 200, 1089), (b"a\r\n" * 40, 1040), (b"a\r\n" * 40, 1190)],
)
def test_the_prefilter_never_skips_a_file_that_would_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes, context: int
) -> None:
    monkeypatch.setattr(judge, "CONTEXT_BYTES", context)
    monkeypatch.setattr(judge, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(judge, "_BATCH", 1)
    repo = _repo(tmp_path, {f"f{i:02}.md": data for i in range(20)})
    assert judge.repo_context(repo, "") == _reference(repo, "")
