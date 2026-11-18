"""Isolated archive, environment and real verifier execution for acceptance replay."""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from experimental.acceptance_casebook.store import CasebookError


class RunnerFailure(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class RealRunner:
    """One archive and venv per replay invocation; per-case state and repository copies."""

    def __init__(
        self, clone: Path, repo: Path, version: str, timeout: int, state_config: Path | None = None
    ):
        self.root = Path(tempfile.mkdtemp(prefix="cairnwork-replay-"))
        self.repo, self.version, self.timeout = repo.resolve(), version, timeout
        self.state_config = state_config
        self.copy, self.venv = self.root / "copy", self.root / "venv"
        try:
            self.copy.mkdir()
            with (self.root / "archive.tar").open("wb") as archive:
                subprocess.run(
                    ["git", "-C", str(clone), "archive", version],
                    check=True,
                    stdout=archive,
                    timeout=timeout,
                )
            subprocess.run(
                ["tar", "-xf", str(self.root / "archive.tar"), "-C", str(self.copy)],
                check=True,
                timeout=timeout,
            )
            subprocess.run(
                ["uv", "venv", "--offline", str(self.venv)],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
            pip = self.venv / "bin" / "python"
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--offline",
                    "--python",
                    str(pip),
                    "-e",
                    str(self.copy),
                ],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
            check = subprocess.run(
                [str(pip), "-I", "-c", "import cairnwork; print(cairnwork.__file__)"],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            assert_copy_import(check.stdout.strip(), self.copy)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __call__(self, case: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        return self.run(case, args)

    def run(self, case: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        case_tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.root))
        working = case_tmp / "repo"
        state = case_tmp / "state"
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--no-local",
                    "--no-hardlinks",
                    "--no-checkout",
                    "--",
                    str(self.repo),
                    str(working),
                ],
                check=True,
                capture_output=True,
                timeout=self.timeout,
            )
            for sha in (case["base"], case["candidate"]):
                try:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(working),
                            "cat-file",
                            "-e",
                            "--end-of-options",
                            f"{sha}^{{commit}}",
                        ],
                        check=True,
                        capture_output=True,
                        timeout=self.timeout,
                    )
                except subprocess.CalledProcessError as exc:
                    raise RunnerFailure("COMMIT_MISSING") from exc
            state.mkdir(mode=0o700)  # cairnwork-verify refuses a state directory that is not 0700
            os.chmod(state, 0o700)
            state_config = getattr(self, "state_config", None)
            if state_config is not None:
                try:
                    self._copy_state_config(state_config, state)
                except CasebookError as exc:
                    raise RunnerFailure(exc.code) from exc
            (state / "ACCEPTING").touch(mode=0o600)
            argv = build_argv(self.venv, state, working, case, args)
            result = run_verifier(argv, self.timeout)
            try:
                payload = json.loads(result.stdout)
            except (ValueError, TypeError) as exc:
                raise RunnerFailure("UNREADABLE_OUTPUT") from exc
            if not isinstance(payload, dict):
                raise RunnerFailure("UNREADABLE_OUTPUT")
            status, core = payload.get("status"), payload.get("core_status")
            if status == "REFUSED":
                raise RunnerFailure("PRE_CORE_REFUSED")
            if status == "UNCOMPARABLE":
                raise RunnerFailure("PRE_CORE_UNCOMPARABLE")
            if (
                result.returncode == 0
                and payload.get("verified") is True
                and status == "VERIFIED"
                and core == "VERIFIED"
            ):
                verdict = "accepted"
            elif result.returncode == 2 and payload.get("verified") is False and core == "FAILED":
                verdict = "refused"
            else:
                raise RunnerFailure("NO_CORE_VERDICT")
            return {"exit_code": result.returncode, "verdict": verdict, "output": result.stdout}
        finally:
            shutil.rmtree(case_tmp, ignore_errors=True)

    @staticmethod
    def _copy_state_config(template: Path, state: Path) -> None:
        """Copy a config template through no-follow descriptors into private case state."""
        source_fd = destination_fd = None
        try:
            source_fd = os.open(template, os.O_RDONLY | os.O_NOFOLLOW)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise CasebookError("REPLAY_INPUT_INVALID")
            destination_fd = os.open(
                state / "config.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(source_fd, "rb") as source, os.fdopen(destination_fd, "wb") as dest:
                source_fd = destination_fd = None
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fchmod(dest.fileno(), 0o600)
        except CasebookError:
            raise
        except OSError as exc:
            raise CasebookError("REPLAY_INPUT_INVALID") from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if destination_fd is not None:
                os.close(destination_fd)


def build_argv(
    venv: Path, state: Path, repo: Path, case: dict[str, Any], args: dict[str, Any]
) -> list[str]:
    argv = [
        str(venv / "bin" / "cairnwork-verify"),
        f"--state={state}",
        "run",
        f"--repo-path={repo}",
        f"--base={case['base']}",
        f"--candidate={case['candidate']}",
    ]
    argv.extend(f"--allowed-file={value}" for value in args["allowed_file"])
    argv.extend(f"--verifier={value}" for value in args["verifier"])
    argv.append(f"--test-commit={args.get('test_commit', '')}")
    argv.extend(f"--import-path={value}" for value in args.get("import_path", []))
    if args.get("blackbox") is not None:
        argv.append(f"--blackbox={args['blackbox']}")
    if args.get("require_protection", False):
        argv.append("--require-protection")
    return argv


def run_verifier(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the verifier in its own process group and reap the group on timeout."""
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:  # the group exited in the instant after the timeout
            pass
        process.wait()
        raise RunnerFailure("TIMEOUT") from exc
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def assert_copy_import(imported: str, copy: Path) -> None:
    """Reject an editable environment that resolves Cairnwork outside its archived version."""
    Path(imported).resolve().relative_to(copy.resolve())
