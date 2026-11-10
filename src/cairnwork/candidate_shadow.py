"""Find nested module files that can shadow imports used by the candidate's tests (#1065)."""

from __future__ import annotations

import ast
import re
import sys
import warnings
from collections import Counter
from importlib.machinery import all_suffixes
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git_bytes

_OID = re.compile(r"^[0-9a-f]{40}$")
_IMPORT_CALLS = frozenset({"import_module", "__import__", "importorskip"})
_ALWAYS_SHADOW = frozenset({"pytest", "_pytest", "pluggy", "conftest", "py"})


def find_shadow_file(
    repo: Path,
    candidate: str,
    test_commit: str | None = None,
    changed: dict[str, str] | None = None,
) -> str | None:
    """Return the first nested file the candidate ``changed`` (path -> ADD/MODIFY) that can shadow
    an imported module; base content is trusted, so unchanged files never count.

    A changed nested file named like a standard or pytest module counts. A name some tree or test
    file imports (every dotted part, relative imports, import_module/__import__/importorskip
    strings, so a ``__path__`` redirect of ``pkg.helper`` is seen) counts when the file was added
    or another file has the same module name, so modifying a project's own src-layout module
    (``src/calc.py`` imported as ``calc``) is not one. Names compare case-insensitively; an
    unparseable ``.py`` the candidate changed fails closed, any other one imports nothing."""
    commits = tuple(
        dict.fromkeys((candidate,) if test_commit is None else (candidate, test_commit))
    )
    always = {name.casefold() for name in (*sys.stdlib_module_names, *_ALWAYS_SHADOW)}
    changed = changed or {}
    imported: set[str] = set()
    trees: dict[str, tuple[tuple[int, str, str, str], ...]] = {}
    for commit in commits:
        _validate_oid(commit)
        trees[commit] = _tree_entries(repo, commit)
    sources = [
        (commit, path, oid)
        for commit in commits
        for mode, typ, oid, path in trees[commit]
        if _is_source(path)
    ]
    blobs = _read_source_blobs(repo, sources)
    parsed: dict[str, set[str] | None] = {}
    for commit, path, oid in sources:
        if oid not in parsed:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed[oid] = _parse_imports(blobs[oid], path)
            except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError):
                parsed[oid] = None
        names = parsed[oid]
        if names is None and path in changed:
            raise ValueError(f"{commit}:{path} could not be parsed")
        imported.update(name.casefold() for name in names or ())
    paths = tuple(path for _, _, _, path in trees[candidate])
    counts = Counter(name for path in paths if (name := _module_name(path)))
    for path in sorted(changed):
        depth = path.count("/") - (
            path.casefold().endswith("/__init__.py") or path.casefold() == "__init__.py"
        )
        if depth < 1 or not (module := _module_name(path)):  # root modules import as themselves
            continue
        added = changed[path] == "ADD"
        if module in always or (module in imported and (added or counts[module] > 1)):
            return path
    return None


def _validate_oid(commit: str) -> None:
    if not _OID.fullmatch(commit):
        raise ValueError("commit must be a full 40-character object id")


def _tree_entries(repo: Path, commit: str) -> tuple[tuple[int, str, str, str], ...]:
    raw = run_git_bytes(repo, "ls-tree", "-r", "-z", commit)
    entries = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, typ, oid = header.split(b" ")
        entries.append(
            (
                int(mode, 8),
                typ.decode("ascii"),
                oid.decode("ascii"),
                path.decode("utf-8", "surrogateescape"),
            )
        )
    return tuple(entries)


def _read_source_blobs(repo: Path, sources: list[tuple[str, str, str]]) -> dict[str, bytes]:
    """Read each distinct source object in one batch; malformed or missing records fail closed."""
    oids = list(dict.fromkeys(oid for _, _, oid in sources))
    if not oids:
        return {}
    raw = run_git_bytes(
        repo, "cat-file", "--batch", stdin="".join(f"{oid}\n" for oid in oids).encode()
    )
    blobs: dict[str, bytes] = {}
    pos = 0
    for oid in oids:
        end = raw.find(b"\n", pos)
        header = raw[pos:end].split() if end >= 0 else []
        if (
            len(header) != 3
            or header[0] != oid.encode()
            or header[1] != b"blob"
            or not header[2].isdigit()
        ):
            raise GitCommandError("git cat-file did not return a blob")
        start, stop = end + 1, end + 1 + int(header[2])
        if stop >= len(raw) or raw[stop : stop + 1] != b"\n":
            raise GitCommandError("git cat-file returned a truncated blob")
        blobs[oid] = raw[start:stop]
        pos = stop + 1
    if pos != len(raw):
        raise GitCommandError("git cat-file returned unexpected data")
    return blobs


def _is_source(path: str) -> bool:
    return path.casefold().endswith(".py")


def _parse_imports(raw: bytes, path: str) -> set[str]:
    """Names one file imports; raises when it cannot be parsed (the caller treats that as None)."""
    tree = ast.parse(raw, filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(part for alias in node.names for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):  # relative too: from . import helper
            names.update((node.module or "").split("."))  # a submodule can be redirected
            names.update(alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call) and _call_name(node.func) in _IMPORT_CALLS:
            named = (k.value for k in node.keywords if k.arg in {"name", "modname"})
            for arg in (*node.args[:1], *named):  # import_module("x.y"), __import__, importorskip
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.update(arg.value.lstrip(".").split("."))
    names.discard("")
    return names


def _call_name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _module_name(path: str) -> str | None:
    """The importable name a tree file provides: a module stem or a package directory."""
    name = path.rsplit("/", 1)[-1].casefold()  # compared case-insensitively: fail closed
    if name == "__init__.py":
        parts = path.casefold().split("/")
        return parts[-2] if len(parts) > 1 else None
    for suffix in sorted(all_suffixes(), key=len, reverse=True):  # .py, .pyc, extension modules
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None
