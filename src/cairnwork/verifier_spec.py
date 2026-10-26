"""Parse a task's untrusted verifier argv and name the inputs a candidate must not touch.

Verifiers come from the task, so they are untrusted text. Only one shape is
accepted: ``python3 -m pytest [-q] [-p no:cacheprovider] <test root>/<file>.py[::<name>]...``.
Anything else is rejected before it runs, and nothing goes through a shell. The
run itself lives in verifiers.py; this module is only the gate on what may run and
on which files a candidate is allowed to change, kept separate so the parsing
rules stay small and easy to audit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.issue_task import DEFAULT_TEST_ROOTS, test_target

_OPTIONS = {("-q",), ("-p", "no:cacheprovider")}
_PROTECTED_NAMES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        "setup.py",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
_MODULE_NAMES = (
    "import json, pkgutil, sys; from importlib.metadata import packages_distributions; "
    "print(json.dumps(sorted(sys.stdlib_module_names | "
    "{module.name for module in pkgutil.iter_modules()} | "
    "set(packages_distributions()))))"
)


class VerifierRejected(Exception):
    code = "VERIFIER_NOT_ALLOWED"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    targets: tuple[str, ...]


def parse_verifier(verifier_id: str, argv: list[str], roots=DEFAULT_TEST_ROOTS) -> VerifierSpec:
    """Accept only python3 -m pytest with allowed options and <test root>/*.py targets."""
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        raise VerifierRejected("argv must be a list of strings")
    if argv[:3] != ["python3", "-m", "pytest"]:
        raise VerifierRejected(f"{verifier_id}: only 'python3 -m pytest' is allowed")
    rest, targets, target = argv[3:], [], test_target(roots)
    while rest:
        if tuple(rest[:1]) in _OPTIONS:
            rest = rest[1:]
        elif tuple(rest[:2]) in _OPTIONS:
            rest = rest[2:]
        elif target.fullmatch(rest[0]):
            targets.append(rest.pop(0))
        else:
            raise VerifierRejected(f"{verifier_id}: argument {rest[0]!r} is not allowed")
    if not targets:
        raise VerifierRejected(f"{verifier_id}: no {'|'.join(roots)}/*.py target")
    return VerifierSpec(verifier_id, tuple(targets))


def protected_verifier_inputs(
    paths: tuple[str, ...],
    roots=DEFAULT_TEST_ROOTS,
    *,
    repo: Path | None = None,
    base_commit: str | None = None,
    python: str | None = None,
    import_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Paths a candidate must not change, because they decide what the verifier runs.

    A root module or first-level module/package under an import path is permitted
    only when the base tracks a regular file or package tree and its name cannot
    shadow a module visible to the verifier's interpreter. Without that evidence,
    such inputs remain protected.
    """
    import_roots = _normalize_import_paths(import_paths)
    first_level_units = tuple(
        path for path in paths if _first_level_unit(path, import_roots) is not None
    )
    safe_roots = _safe_root_modules(
        first_level_units, repo, base_commit, python, import_paths=import_paths
    )
    return tuple(
        path
        for path in paths
        if path in roots
        or path.startswith(tuple(f"{r}/" for r in roots))
        or path.rsplit("/", 1)[-1] in _PROTECTED_NAMES
        or _is_first_level_pth(path, import_roots)
        or (path in first_level_units and path not in safe_roots)
    )


def _normalize_import_paths(import_paths: tuple[str, ...]) -> tuple[str, ...]:
    roots = [""]
    for r in import_paths:
        cleaned = r.strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.strip("/")
        if cleaned and cleaned != ".":
            roots.append(cleaned)
    return tuple(dict.fromkeys(roots))


def _first_level_unit(
    path: str, import_roots: tuple[str, ...]
) -> tuple[str, str, str, bool, str] | None:
    for r in import_roots:
        if not r:
            if "/" not in path and path.endswith(".py"):
                return (path, r, path[:-3], False, "")
            if path.count("/") == 1 and path.endswith("/__init__.py"):
                name = path.split("/")[0]
                return (path, r, name, True, name)
        else:
            prefix = f"{r}/"
            if path.startswith(prefix):
                rel = path[len(prefix) :]
                if "/" not in rel and rel.endswith(".py") and rel != "__init__.py":
                    return (path, r, rel[:-3], False, "")
                if rel.count("/") == 1 and rel.endswith("/__init__.py"):
                    name = rel.split("/")[0]
                    return (path, r, name, True, f"{r}/{name}")
    return None


def _is_first_level_pth(path: str, import_roots: tuple[str, ...]) -> bool:
    for r in import_roots:
        if not r:
            if "/" not in path and path.endswith(".pth"):
                return True
        else:
            prefix = f"{r}/"
            if path.startswith(prefix):
                rel = path[len(prefix) :]
                if "/" not in rel and rel.endswith(".pth"):
                    return True
    return False


def _safe_root_modules(
    paths: tuple[str, ...],
    repo: Path | None,
    base_commit: str | None,
    python: str | None = None,
    import_paths: tuple[str, ...] = (),
) -> set[str]:
    if not paths or repo is None or base_commit is None:
        return set()
    import_roots = _normalize_import_paths(import_paths)
    units = []
    for path in paths:
        info = _first_level_unit(path, import_roots)
        if info is not None:
            units.append(info)
    if not units:
        return set()
    try:
        listing = run_git(repo, "ls-tree", "-z", base_commit, "--", *(u[0] for u in units))
        names = _importable_names(python or sys.executable)
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return set()  # no proof of safety: refuse before binding or verification
    tracked_blobs = {}
    for entry in filter(None, listing.split("\0")):
        meta, p = entry.split("\t", 1)
        mode, kind, _oid = meta.split()
        tracked_blobs[p] = kind == "blob" and mode in ("100644", "100755")

    pkg_dirs = tuple(dict.fromkeys(u[4] for u in units if u[3] and not tracked_blobs.get(u[0])))
    tracked_trees = set()
    if pkg_dirs:
        try:
            tree_listing = run_git(repo, "ls-tree", "-z", "-d", base_commit, "--", *pkg_dirs)
            for entry in filter(None, tree_listing.split("\0")):
                meta, p = entry.split("\t", 1)
                mode, kind, _oid = meta.split()
                if kind == "tree":
                    tracked_trees.add(p)
        except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
            return set()

    safe = set()
    for path_str, _r, name, is_pkg, pkg_dir in units:
        is_tracked = tracked_blobs.get(path_str, False) or (is_pkg and pkg_dir in tracked_trees)
        if is_tracked and name.isidentifier() and name.casefold() not in names:
            safe.add(path_str)
    return safe


def _importable_names(python: str) -> set[str]:
    result = subprocess.run(  # noqa: S603 - the configured verifier Python, fixed code
        [python, "-I", "-c", _MODULE_NAMES],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/",
    )
    names = json.loads(result.stdout)
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("verifier module inventory is invalid")
    return {name.casefold() for name in names} | {"_cairnwork_report_guard"}
