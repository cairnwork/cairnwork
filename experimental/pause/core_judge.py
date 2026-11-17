"""Run Cairnwork Core against a clean-base reconstruction of R1."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from cairnwork.intake import Intake
from cairnwork.verify_candidate import verify_candidate
from experimental.pause.flow import CoreVerdict, RunResult
from experimental.pause.git_env import hardened_git_env
from experimental.pause.guard import TreeGuard

_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_GIT_OPTIONS = (
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
)


def _env() -> dict[str, str]:
    env = hardened_git_env()
    env.update(
        GIT_AUTHOR_NAME="Cairnwork PAUSE",
        GIT_AUTHOR_EMAIL="pause@cairnwork.invalid",
        GIT_COMMITTER_NAME="Cairnwork PAUSE",
        GIT_COMMITTER_EMAIL="pause@cairnwork.invalid",
    )
    return env


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *_GIT_OPTIONS, *args],
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _clone(source: Path, repo: Path) -> None:
    result = subprocess.run(
        [
            "git",
            *_GIT_OPTIONS,
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source),
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )
    if result.returncode:
        raise RuntimeError(f"git clone failed: {result.stderr.strip()}")


def _remove_working_entries(repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _entries(root: Path) -> dict[bytes, tuple[int, bytes, Path]]:
    entries: dict[bytes, tuple[int, bytes, Path]] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        dirs[:] = [name for name in dirs if not (parent == root and name == ".git")]
        for name in [*dirs, *files]:
            path = parent / name
            rel = path.relative_to(root)
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if stat.S_ISLNK(mode):
                data = os.fsencode(os.readlink(path))
                git_mode = 0o120000
            elif stat.S_ISREG(mode):
                data = path.read_bytes()
                git_mode = 0o100755 if mode & 0o111 else 0o100644
            else:
                raise ValueError(f"unsupported non-regular checkout entry: {rel}")
            entries[os.fsencode(rel.as_posix())] = (git_mode, data, path)
    return entries


def _validate_checkout_shape(
    root: Path, entries: dict[bytes, tuple[int, bytes, Path]], base_paths: set[bytes]
) -> frozenset[str]:
    """Refuse unrepresented state, except parents emptied by a tracked deletion."""
    represented = _parent_directories(entries)
    base_directories = _parent_directories(base_paths)
    excluded: set[str] = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        dirs[:] = [name for name in dirs if not (parent == root and name == ".git")]
        if stat.S_IMODE(parent.lstat().st_mode) != 0o755:
            raise ValueError(f"unsupported checkout directory mode: {parent.relative_to(root)}")
        for name in [*dirs, *files]:
            path = parent / name
            mode = path.lstat().st_mode
            rel = path.relative_to(root)
            if stat.S_ISDIR(mode):
                if stat.S_IMODE(mode) != 0o755:
                    raise ValueError(f"unsupported checkout directory mode: {rel}")
                path_bytes = os.fsencode(rel.as_posix())
                if path_bytes not in represented and path_bytes not in base_directories:
                    raise ValueError(f"checkout directory contains no committed file: {rel}")
                if path_bytes not in represented:
                    excluded.add(rel.as_posix())
            elif stat.S_ISREG(mode) and stat.S_IMODE(mode) not in (0o644, 0o755):
                raise ValueError(f"unsupported checkout file mode: {rel}")
    return frozenset(excluded)


def _parent_directories(paths: Iterable[bytes]) -> set[bytes]:
    """List directories that contain a tracked file, including nested ancestors."""
    parents: set[bytes] = set()
    for path in paths:
        parent = path.rpartition(b"/")[0]
        while parent:
            parents.add(parent)
            parent = parent.rpartition(b"/")[0]
    return parents


def _candidate_tree_digest(repo: Path, candidate: str, excluded: frozenset[str]) -> str:
    """Checkout Core's commit with a fixed umask, then hash its filesystem tree."""
    with tempfile.TemporaryDirectory(prefix="cairnwork-pause-tree-") as temp:
        checkout = Path(temp) / "checkout"
        checkout.mkdir(mode=0o755)
        os.chmod(checkout, 0o755)
        command = [
            "/bin/sh",
            "-c",
            'umask 022; exec "$@"',
            "cairnwork-pause-checkout",
            "git",
            "--git-dir",
            str(repo / ".git"),
            "--work-tree",
            str(checkout),
            *_GIT_OPTIONS,
            "checkout",
            "--force",
            "--detach",
            candidate,
        ]
        result = subprocess.run(command, capture_output=True, check=False, env=_env())
        if result.returncode:
            raise RuntimeError("cannot materialize Core candidate tree")
        return TreeGuard(checkout, exclude_git=True, exclude_paths=excluded).digest()


def _copy_working_tree(source: Path, target: Path) -> dict[bytes, tuple[int, bytes, Path]]:
    entries = _entries(source)
    for current, dirs, files in os.walk(source, followlinks=False):
        parent = Path(current)
        dirs[:] = [name for name in dirs if not (parent == source and name == ".git")]
        for name in list(dirs):
            src = parent / name
            rel = src.relative_to(source)
            dest = target / rel
            if src.is_symlink():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(os.readlink(src))
                dirs.remove(name)
            else:
                dest.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = parent / name
            rel = src.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                dest.symlink_to(os.readlink(src))
            else:
                mode = src.stat().st_mode
                dest.write_bytes(src.read_bytes())
                os.chmod(dest, stat.S_IMODE(mode))
    return entries


def _tree_entries(repo: Path, candidate: str) -> dict[bytes, tuple[int, bytes]]:
    listing = subprocess.run(
        ["git", "-C", str(repo), *_GIT_OPTIONS, "ls-tree", "-r", "-z", "--full-tree", candidate],
        capture_output=True,
        check=False,
        env=_env(),
    )
    if listing.returncode:
        raise RuntimeError("cannot enumerate Core candidate tree")
    entries: dict[bytes, tuple[int, bytes]] = {}
    for row in listing.stdout.split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode_text, kind, object_id = header.split(b" ", 2)
        if kind != b"blob":
            raise ValueError("candidate tree contains a non-blob entry")
        blob = subprocess.run(
            ["git", "-C", str(repo), *_GIT_OPTIONS, "cat-file", "blob", object_id.decode("ascii")],
            capture_output=True,
            check=False,
            env=_env(),
        )
        if blob.returncode:
            raise RuntimeError("cannot read Core candidate blob")
        entries[path] = (int(mode_text, 8), blob.stdout)
    return entries


def _require_raw_match(
    source_entries: dict[bytes, tuple[int, bytes, Path]],
    committed_entries: dict[bytes, tuple[int, bytes]],
) -> None:
    expected = {path: (mode, data) for path, (mode, data, _entry) in source_entries.items()}
    if expected != committed_entries:
        raise ValueError("Core candidate bytes or paths differ from R1 working tree")


class CoreJudge:
    """Callable Core adapter; verification runs only in a disposable clean-base repository."""

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
        base = r1.base_sha
        if type(base) is not str or not _SHA_RE.fullmatch(base):
            raise ValueError("R1 base_sha (set by the runner) must be 40 lowercase hex")
        base_entries = _tree_entries(r1.checkout, base)
        entry_source_entries = _entries(r1.checkout)
        excluded = _validate_checkout_shape(r1.checkout, entry_source_entries, set(base_entries))
        source_tree, before_copy = TreeGuard(r1.checkout).digest_pair(
            exclude_git=True, exclude_paths=excluded
        )
        with tempfile.TemporaryDirectory(prefix="cairnwork-pause-core-") as temp:
            repo = Path(temp) / "repo"
            _clone(r1.checkout, repo)
            _git(repo, "checkout", "--detach", base)
            _remove_working_entries(repo)
            source_entries = _copy_working_tree(r1.checkout, repo)
            copied_excluded = _validate_checkout_shape(
                r1.checkout, source_entries, set(base_entries)
            )
            if copied_excluded != excluded:
                raise ValueError("R1 checkout shape changed while Core copy was made")
            _git(repo, "add", "-A")
            _git(repo, "commit", "--allow-empty", "--no-verify", "-m", "PAUSE candidate")
            candidate = _git(repo, "rev-parse", "HEAD").strip()
            committed_entries = _tree_entries(repo, candidate)
            _require_raw_match(source_entries, committed_entries)
            paths = self._changed_paths(repo, base, candidate)
            source_digest = TreeGuard(
                r1.checkout, exclude_git=True, exclude_paths=excluded
            ).digest()
            if _candidate_tree_digest(repo, candidate, excluded) != before_copy:
                raise ValueError("Core candidate filesystem tree differs from R1 judge-entry tree")
            if source_digest != before_copy:
                raise ValueError("R1 checkout changed while Core copy was made")
            result = self.verify(
                self.intake,
                repo=repo,
                base=base,
                candidate=candidate,
                allowed_files=paths,
                verifiers=self.verifiers,
                import_paths=self.import_paths,
            )
            if TreeGuard(r1.checkout).digest() != source_tree:
                raise RuntimeError("Core judge changed R1 checkout")
            if not isinstance(result, dict) or type(result.get("status")) is not str:
                raise ValueError("Core result must contain a string status")
            return CoreVerdict(result["status"], source_tree)

    @staticmethod
    def _changed_paths(repo: Path, base: str, candidate: str) -> tuple[str, ...]:
        raw = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                *_GIT_OPTIONS,
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
        return tuple(
            item.decode("utf-8", "surrogateescape") for item in raw.stdout.split(b"\0") if item
        )
