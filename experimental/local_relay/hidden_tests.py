"""Hidden (deferred) tests for fixture tasks.

The tests live in a private directory outside the fixture repository. Each Core judgment
gets a fresh scratch commit that holds only those files on top of the base; it is imported
into the intake test store and passed to ``verify_candidate(test_commit=...)``. The worker
checkout never contains the files, and a test commit can be used only once, so every stage
builds its own.
"""

from __future__ import annotations

import copy
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cairnwork.issue_task import _check_path
from cairnwork.verify_candidate import verify_candidate

TEST_ROOT = "tests"  # hidden files are committed under tests/, as the verifier targets name them
MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
_GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_ID = ("-c", "user.name=relay", "-c", "user.email=relay@localhost")


@dataclass(frozen=True)
class HiddenTests:
    directory: Path
    files: tuple[str, ...]
    python: str | None = None


def load_hidden(data: dict[str, Any], root: Path) -> HiddenTests | None:
    """Validate the optional ``hidden_tests_dir`` / ``verifier_python`` fixture keys."""
    raw, python = data.get("hidden_tests_dir"), data.get("verifier_python")
    if python is not None and not (
        isinstance(python, str) and os.path.isabs(python) and os.access(python, os.X_OK)
    ):
        raise ValueError("fixture verifier_python must be an absolute executable path")
    if raw is None:
        if python is not None:
            raise ValueError("fixture verifier_python requires hidden_tests_dir")
        return None
    if not isinstance(raw, str) or not os.path.isabs(raw):
        raise ValueError("fixture hidden_tests_dir must be an absolute path")
    directory = Path(raw)
    _refuse_symlink_components(directory)
    info = directory.lstat() if directory.exists() or directory.is_symlink() else None
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise ValueError("fixture hidden_tests_dir must be a real directory")
    if info.st_mode & 0o077:
        raise ValueError("fixture hidden_tests_dir must not be accessible to group or others")
    real, root_real = directory.resolve(), root.resolve()
    if real == root_real or root_real in real.parents or real in root_real.parents:
        raise ValueError("fixture hidden_tests_dir must be outside the fixture repository")
    return HiddenTests(directory, _collect(directory), python)


def _root_owned(path: Path) -> bool:
    return path.lstat().st_uid == 0


def _refuse_symlink_components(
    directory: Path, root_owned: Callable[[Path], bool] = _root_owned
) -> None:
    """No path component may be a symlink, except root-owned ones (macOS /var, /tmp).

    A root-owned link is followed (relative targets against the link's directory) and the
    components of its target get the same check; 40 hops at most, so a cycle is refused.
    """
    message = "fixture hidden_tests_dir must not contain symlinks in its path"
    pending = list(reversed(directory.parts[1:]))  # next component last
    current, hops = Path(directory.anchor), 0
    while pending:
        part = pending.pop()
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        try:
            is_link = candidate.is_symlink()
        except OSError:
            return  # a missing path is refused with its own message
        if not is_link:
            current = candidate
            continue
        hops += 1
        if hops > 40 or not root_owned(candidate):
            raise ValueError(message)
        target = Path(os.readlink(candidate))
        if target.is_absolute():
            current = Path(target.anchor)
            pending.extend(reversed(target.parts[1:]))
        else:
            pending.extend(reversed(target.parts))


def _collect(directory: Path) -> tuple[str, ...]:
    found: list[str] = []
    for base, dirs, names in os.walk(directory):
        for name in (*dirs, *names):
            if (Path(base) / name).is_symlink():
                raise ValueError("hidden tests must not contain symlinks")
        for name in names:
            path = Path(base) / name
            rel = path.relative_to(directory).as_posix()
            _check_path(f"{TEST_ROOT}/{rel}")
            if path.stat().st_size > MAX_FILE_BYTES or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f"hidden test {rel} is not a regular file under the size cap")
            found.append(f"{TEST_ROOT}/{rel}")
    if not 0 < len(found) <= MAX_FILES:
        raise ValueError("hidden_tests_dir must contain 1 to 200 files")
    return tuple(sorted(found))


def visible_targets(targets: tuple[str, ...], hidden: HiddenTests | None) -> tuple[str, ...]:
    """Verifier targets the base tree must hold; hidden test files are checked elsewhere."""
    if hidden is None:
        return targets
    return tuple(t for t in targets if t.split("::", 1)[0] not in hidden.files)


def require_hidden_targets(targets: tuple[str, ...], hidden: HiddenTests) -> None:
    for target in targets:
        path = target.split("::", 1)[0]
        if path in hidden.files and not (hidden.directory / path.removeprefix("tests/")).is_file():
            raise ValueError(f"hidden test target {path} is missing")


def _git(cwd: Path, *args: str) -> str:
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}  # fmt: skip
    done = subprocess.run(
        [*_GIT, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=True
    )
    return done.stdout.strip()


def build_bundle(root: Path, base: str, hidden: HiddenTests, out: Path) -> str:
    """A bundle holding one commit (hidden files only, parent ``base``); returns its id."""
    work = out / "work"
    _git(out, "clone", "-q", "--no-hardlinks", "--no-checkout", str(root), str(work))
    _git(work, "checkout", "-q", "--detach", base)
    for rel in hidden.files:
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hidden.directory / rel.removeprefix(f"{TEST_ROOT}/"), target)
    _git(work, "add", "-f", "-A")
    _git(work, *_ID, "commit", "-q", "--no-verify", "-m", f"hidden tests {uuid.uuid4().hex}")
    sha = _git(work, "rev-parse", "HEAD")
    _git(work, "update-ref", "refs/heads/hidden", sha)
    _git(work, "bundle", "create", "-q", str(out / "t.bundle"), "refs/heads/hidden", f"^{base}")
    return sha


class HiddenVerify:
    """``verify`` callable for CoreJudge: every call judges against a fresh hidden commit."""

    def __init__(
        self, root: Path, hidden: HiddenTests,
        verify: Callable[..., dict[str, Any]] = verify_candidate,
    ) -> None:  # fmt: skip
        self.root, self.hidden, self.verify = Path(root), hidden, verify

    def __call__(self, intake: Any, **kwargs: Any) -> dict[str, Any]:
        intake.prepare()
        store = intake.test_store()
        with tempfile.TemporaryDirectory(prefix="relay-hidden-") as temp:
            out = Path(temp)
            sha = build_bundle(self.root, kwargs["base"], self.hidden, out)
            store.import_bundle(kwargs["repo"], out / "t.bundle", store.used)
        try:
            return self.verify(intake, **kwargs, test_commit=sha)
        finally:
            store.bundle(sha).unlink(missing_ok=True)


def with_python(intake: Any, python: str | None) -> Any:
    """Intake whose config names ``python`` as the verifier interpreter (per-task venv)."""
    if python is None:
        return intake

    class _Intake(type(intake)):  # type: ignore[misc]
        def config(self) -> dict[str, Any]:
            return {**super().config(), "verifier_python": python}

    patched = copy.copy(intake)
    object.__setattr__(patched, "__class__", _Intake)
    return patched
