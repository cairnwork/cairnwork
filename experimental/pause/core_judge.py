"""Run Cairnwork Core against a committed copy of R1 without touching its checkout."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cairnwork.intake import Intake
from cairnwork.verify_candidate import verify_candidate
from experimental.pause.flow import CoreVerdict, RunResult
from experimental.pause.guard import TreeGuard

_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def _env() -> dict[str, str]:
    """The caller's environment without its git config: read at call time, so no global or
    system filter, hook or fsmonitor can change what is committed as the candidate."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Cairnwork PAUSE",
        "GIT_AUTHOR_EMAIL": "pause@cairnwork.invalid",
        "GIT_COMMITTER_NAME": "Cairnwork PAUSE",
        "GIT_COMMITTER_EMAIL": "pause@cairnwork.invalid",
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


class CoreJudge:
    """Callable Core adapter; verification runs only in a disposable copy of R1."""

    def __init__(
        self,
        state_dir: Path,
        verifiers: Sequence[tuple[str, Sequence[str]]],
        import_paths: Sequence[Path | str] = (),
        verify: Callable[..., dict[str, Any]] = verify_candidate,
    ) -> None:
        self.intake = Intake(Path(state_dir))
        self.verifiers = tuple((key, tuple(value)) for key, value in verifiers)
        self.import_paths = tuple(Path(path) for path in import_paths)
        self.verify = verify

    def __call__(self, r1: RunResult) -> CoreVerdict:
        if r1.checkout is None:
            raise ValueError("R1 checkout is required")
        before = TreeGuard(r1.checkout).digest()
        base = r1.base_sha  # from the runner, never from R1's own record or output
        if type(base) is not str or not _SHA_RE.fullmatch(base):
            raise ValueError("R1 base_sha (set by the runner) must be 40 lowercase hex")
        with tempfile.TemporaryDirectory(prefix="cairnwork-pause-core-") as temp:
            repo = Path(temp) / "repo"
            shutil.copytree(r1.checkout, repo, symlinks=True)
            self._refuse_git_filters(repo)
            _git(repo, "add", "-A")
            _git(
                repo,
                "-c",
                "user.name=Cairnwork PAUSE",
                "-c",
                "user.email=pause@cairnwork.invalid",
                "commit",
                "--allow-empty",
                "--no-verify",
                "-m",
                "PAUSE candidate",
            )
            candidate = _git(repo, "rev-parse", "HEAD").strip()
            raw = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "diff",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    base,
                    candidate,
                ],
                capture_output=True,
                check=False,
                env=_env(),
            )
            if raw.returncode:
                raise RuntimeError("cannot enumerate Core candidate paths")
            paths = tuple(
                item.decode("utf-8", "surrogateescape") for item in raw.stdout.split(b"\0") if item
            )
            result = self.verify(
                self.intake,
                repo=repo,
                base=base,
                candidate=candidate,
                allowed_files=paths,
                verifiers=self.verifiers,
                import_paths=self.import_paths,
            )
            after = TreeGuard(r1.checkout).digest()
            if after != before:
                raise RuntimeError("Core judge changed R1 checkout")
            if not isinstance(result, dict) or type(result.get("status")) is not str:
                raise ValueError("Core result must contain a string status")
            return CoreVerdict(result["status"], before)

    @staticmethod
    def _refuse_git_filters(repo: Path) -> None:
        config = repo / ".git" / "config"
        if not config.is_file():
            return
        rows = _git(repo, "config", "--file", str(config), "--list").splitlines()
        forbidden = ("filter.", "core.hookspath", "core.fsmonitor", "core.attributesfile")
        for row in rows:
            key = row.split("=", 1)[0].lower()
            if key.startswith(forbidden) or key == "include" or key.startswith("include."):
                raise ValueError(f"unsafe copied git config key: {key}")
