"""Filters, external diff, and submodule recursion are disabled by gitcmd (#759)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import run_git


def _repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True, env=env)
    return repo, env


def _commit(repo: Path, env: dict[str, str], *paths: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", *paths], check=True, env=env)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
        env=env,
    )


def _filter(repo: Path, name: str, marker: Path) -> None:
    script = marker.parent / "filter.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
    script.chmod(0o755)
    for key in ("clean", "smudge", "process"):
        subprocess.run(
            ["git", "-C", str(repo), "config", f"filter.{name}.{key}", f"{script} {key}"],
            check=True,
        )
    subprocess.run(
        ["git", "-C", str(repo), "config", f"filter.{name}.required", "true"], check=True
    )


@pytest.mark.parametrize("operation", ["checkout", "add", "status"])
def test_declared_filter_commands_never_run_and_required_does_not_fail(
    tmp_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, env = _repo(tmp_path)
    marker = tmp_path / "filter-ran"
    (repo / ".gitattributes").write_text("*.txt filter=bad\n")
    (repo / "x.txt").write_text("committed\n")
    _commit(repo, env, ".gitattributes", "x.txt")
    _filter(repo, "bad", marker)
    seen: list[list[str]] = []
    real_run = gitcmd.subprocess.run

    def capture(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if "-C" in argv and str(repo) in argv and operation in argv:
            seen.append(list(argv))
        return result

    monkeypatch.setattr(gitcmd.subprocess, "run", capture)

    if operation == "checkout":
        (repo / "x.txt").write_text("changed\n")
        run_git(repo, "checkout", "--force", "HEAD")
    elif operation == "add":
        (repo / "x.txt").write_text("added\n")
        run_git(repo, "add", "x.txt")
    else:
        (repo / "x.txt").write_text("status\n")
        assert "x.txt" in run_git(repo, "status", "--short")
    assert not marker.exists()
    assert seen and "filter.bad.required=false" in seen[0]
    assert all(f"filter.bad.{key}=" in seen[0] for key in ("clean", "smudge", "process"))


@pytest.mark.parametrize("name", ["has.dots", ""])
def test_filter_driver_names_with_dots_or_empty_are_disabled(
    tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, env = _repo(tmp_path)
    marker = tmp_path / "filter-ran"
    (repo / ".gitattributes").write_text(f'*.txt filter="{name}"\n')
    (repo / "x.txt").write_text("committed\n")
    _commit(repo, env, ".gitattributes", "x.txt")
    _filter(repo, name, marker)
    (repo / "x.txt").write_text("changed\n")
    seen: list[list[str]] = []
    real_run = gitcmd.subprocess.run

    def capture(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if "-C" in argv and str(repo) in argv and "checkout" in argv:
            seen.append(list(argv))
        return result

    monkeypatch.setattr(gitcmd.subprocess, "run", capture)
    run_git(repo, "checkout", "--force", "HEAD")
    assert not marker.exists()
    assert seen
    assert f"filter.{name}.smudge=" in seen[0]


def test_checkout_does_not_recurse_into_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, env = _repo(tmp_path)
    child = tmp_path / "child"
    subprocess.run(["git", "init", "-q", "--template=", str(child)], check=True, env=env)
    (child / "value").write_text("one\n")
    _commit(child, env, "value")
    first = subprocess.run(
        ["git", "-C", str(child), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (child / "value").write_text("two\n")
    _commit(child, env, "value")
    second = subprocess.run(
        ["git", "-C", str(child), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(parent),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(child),
            "module",
        ],
        check=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(parent), "update-index", "--cacheinfo", "160000", first, "module"],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(parent),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "submodule",
        ],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(parent), "config", "submodule.recurse", "true"], check=True)
    subprocess.run(["git", "-C", str(parent / "module"), "checkout", "-q", second], check=True)
    trace = tmp_path / "trace2.json"
    (tmp_path / ".gitconfig").write_text(f"[trace2]\n\teventTarget = {trace}\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    run_git(parent, "checkout", "--force", "HEAD")

    assert trace.exists()
    assert "submodule--helper" not in trace.read_text()
    actual = subprocess.run(
        ["git", "-C", str(parent / "module"), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual == second and actual != first


def test_diff_external_is_disabled_without_call_site_option(tmp_path: Path) -> None:
    repo, env = _repo(tmp_path)
    (repo / "x.txt").write_text("one\n")
    _commit(repo, env, "x.txt")
    (repo / "x.txt").write_text("two\n")
    marker = tmp_path / "diff-ran"
    script = tmp_path / "external-diff.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "diff.external", str(script)], check=True)

    run_git(repo, "diff", "--", "x.txt")

    assert not marker.exists()
