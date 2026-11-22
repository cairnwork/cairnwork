"""Static scan of the files a verifier run executes, read from git objects (#851 step 2).

The scan answers one question: which identifiers and strings do the target files and the
conftest.py files above them use? Every identifier-like token counts as a use (a superset),
so a plugin's fixture, marker or ini name is found however it is spelled. Anything the scan
cannot classify returns ``Scan.unknown`` with a reason, and the selector then loads every
installed plugin (the main behaviour). Untrusted files are parsed with ast only; nothing is
imported or executed. Logs print the reason, never file contents.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 1 << 20
GIT_TIMEOUT_SECONDS = 60
_SHA = re.compile(r"[0-9a-f]{40}")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DYNAMIC_CALLS = frozenset(
    {"getattr", "__import__", "eval", "exec", "globals", "vars", "setattr", "delattr"}
)
_DYNAMIC_ATTRS = frozenset({"import_module", "__dict__", "__getattr__", "__getattribute__"})
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)


@dataclass(frozen=True)
class Scan:
    """Identifiers used by the scanned files; ``unknown`` names why the scan gave up ("" = none)."""

    names: frozenset[str] = frozenset()
    unknown: str = ""

    @property
    def classified(self) -> bool:
        return not self.unknown


class ScanUnknown(Exception):
    """A file or path the scan cannot read; the run falls back to a full load."""


def unknown(reason: str) -> Scan:
    return Scan(frozenset(), reason)


def merge(scans: Iterable[Scan]) -> Scan:
    scans = list(scans)
    names = frozenset().union(*(s.names for s in scans)) if scans else frozenset()
    reasons = [s.unknown for s in scans if s.unknown]
    return Scan(names, reasons[0] if reasons else "")


def safe_rel(path: str) -> str:
    """A node id's file part as a repo-relative POSIX path; ScanUnknown when it leaves the repo."""
    text = path.split("::", 1)[0]
    posix = PurePosixPath(text)
    bad = not text or posix.is_absolute() or any(p in ("", "..") for p in posix.parts)
    if bad or "\0" in text or any(ord(c) < 32 for c in text):
        raise ScanUnknown("target path is not a file inside the repository")
    return posix.as_posix()


def conftests_for(target: str) -> list[str]:
    """conftest.py paths from the target's directory up to the repository root, nearest first."""
    parts = PurePosixPath(target).parts[:-1]
    return [str(PurePosixPath(*parts[:i], "conftest.py")) for i in range(len(parts), -1, -1)]


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _names_of(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name | ast.arg):
        return tokens(node.id if isinstance(node, ast.Name) else node.arg)
    if isinstance(node, ast.Attribute):
        return tokens(node.attr)
    if isinstance(node, ast.FunctionDef | ast.ClassDef):
        return tokens(node.name)
    if isinstance(node, ast.alias):
        return tokens(node.name) | tokens(node.asname or "")
    if isinstance(node, ast.keyword) and node.arg:
        return tokens(node.arg)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return tokens(node.value)
    return set()


def _import_unknown(node: ast.Import | ast.ImportFrom, local: Callable[[str], bool]) -> str:
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return "relative import"
        if any(a.name == "*" for a in node.names):
            return "star import"
        modules = [node.module or ""]
    else:
        modules = [a.name for a in node.names]
    for module in modules:
        top = module.split(".")[0]
        if top and local(top):
            return "import of a repository module"
    return ""


def _node_unknown(node: ast.AST, local: Callable[[str], bool]) -> str:
    """Why this one node defeats a static answer ("" when it does not)."""
    if isinstance(node, ast.AsyncFunctionDef):
        return "async def"
    if isinstance(node, ast.Import | ast.ImportFrom):
        return _import_unknown(node, local)
    if isinstance(node, ast.Name | ast.arg | ast.alias) and "pytest_plugins" in _names_of(node):
        return "pytest_plugins"
    if isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_CALLS:
        return "dynamic call"
    if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_ATTRS:
        return "dynamic attribute"
    return ""


def scan_bytes(path: str, data: bytes | None, local: Callable[[str], bool]) -> Scan:
    """Classify one file; None (absent at the base) and anything unparsable is unknown."""
    if data is None:
        return unknown(f"{path}: absent")
    if len(data) > MAX_FILE_BYTES:
        return unknown(f"{path}: larger than the scan limit")
    try:
        tree = ast.parse(data, filename=path)
    except _PARSE_ERRORS + (UnicodeDecodeError,):
        return unknown(f"{path}: cannot be parsed")
    names: set[str] = set()
    for node in ast.walk(tree):
        reason = _node_unknown(node, local)
        if reason:
            return unknown(f"{path}: {reason}")
        names |= _names_of(node)
    return Scan(frozenset(names))


def _git_blob(repo: Path, base: str, path: str) -> bytes | None:
    """The base's regular file at path; None when the base has no such path."""
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv, base checked as 40-hex first
            ["git", "-C", str(repo), "ls-tree", "-z", base, "--", path],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=True,
        ).stdout  # fmt: skip
        entry = listing.split(b"\0", 1)[0]
        if not entry:
            return None
        mode, kind, oid = entry.split(b"\t", 1)[0].split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ScanUnknown(f"{path}: not a regular file at the base")
        blob = subprocess.run(  # noqa: S603 - oid came from ls-tree
            ["git", "-C", str(repo), "cat-file", "blob", oid.decode()],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=True,
        )  # fmt: skip
        return blob.stdout
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ScanUnknown(f"{path}: base cannot be read") from exc


def repo_names(repo: Path, base: str) -> frozenset[str]:
    """Every file and directory name in the base tree (stems included): a top-level import
    matching one of them may be a repository module, so it defeats the scan."""
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--name-only", base],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScanUnknown("base tree cannot be listed") from exc
    found: set[str] = set()
    for item in filter(None, listing.decode("utf-8", "replace").split("\0")):
        found.update(p.split(".")[0] for p in PurePosixPath(item).parts)
    return frozenset(found)


def candidate_bytes(candidate: Path | None, path: str) -> bytes | None:
    """The candidate's copy of a conftest (candidate may edit it); None when absent there."""
    if candidate is None:
        return None
    target = candidate / path
    if not target.is_file() or target.is_symlink():
        return None
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ScanUnknown(f"{path}: larger than the scan limit")
    return target.read_bytes()


def scan_run(repo: Path, base: str, targets: Iterable[str], candidate: Path | None = None) -> Scan:
    """Scan the targets and their conftests at base, plus the candidate's own conftests.

    Both sides count (union), so a plugin the candidate's conftest needs is loaded for the base
    run too: the base and candidate runs load the same plugins."""
    if not _SHA.fullmatch(base):
        return unknown("base is not a 40-hex commit id")
    try:
        local = _local_check(repo_names(repo, base))
        scans = [_file_scan(repo, base, p, local, candidate) for p in _paths(targets)]
    except ScanUnknown as exc:
        return unknown(str(exc))
    return merge(scans)


def _paths(targets: Iterable[str]) -> list[str]:
    paths: list[str] = []
    for target in targets:
        rel = safe_rel(target)
        paths.append(rel)
        paths.extend(conftests_for(rel))
    return list(dict.fromkeys(paths))


def _local_check(names: frozenset[str]) -> Callable[[str], bool]:
    return lambda top: top in names


def _file_scan(
    repo: Path, base: str, path: str, local: Callable[[str], bool], candidate: Path | None
) -> Scan:
    """A target must exist at base. A conftest is read at base and, when present, as the
    candidate has it; a conftest absent on a side means that side loads none there."""
    data = _git_blob(repo, base, path)
    if not path.endswith("conftest.py"):
        return scan_bytes(path, data, local)
    sides = [s for s in (data, candidate_bytes(candidate, path)) if s is not None]
    return merge(scan_bytes(path, side, local) for side in sides) if sides else Scan()
