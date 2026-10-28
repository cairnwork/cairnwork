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
from cairnwork.import_path_tree import normalize_import_paths
from cairnwork.issue_task import DEFAULT_TEST_ROOTS, test_target
from cairnwork.sealed_packages import sealed_packages

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
    allow_deletions: bool = True,
    test_commit: str | None = None,
) -> tuple[str, ...]:
    """Paths a candidate must not change, because they decide what the verifier runs.

    A root module or first-level module/package under an import path is permitted
    only when the base tracks a regular file or package tree and its name cannot
    shadow a module visible to the verifier's interpreter. Without that evidence,
    such inputs remain protected. Import paths are normalized first: leading ``./`` and trailing
    ``/`` are stripped and the checkout root (empty, ``.``, ``./``) is dropped. Any empty, ``.`` or
    ``..`` segment left (``a//b``, ``a/.``, leading ``/``) or non-ASCII one raises BAD_IMPORT_PATH.
    The directories above a test root are import roots too, with two more rules (#360,
    ``_risky_above_roots``); ``allow_deletions`` is the task's (deletions are refused otherwise).
    A sealed package (``sealed_packages``, #382) is never put on sys.path by pytest, so it is no
    import root and its names count for nothing; only its ``__init__`` rule stays.
    """
    above = _dirs_above_roots(tuple(roots))
    under_above = tuple(path for path in paths if _under(path, above))
    sealed = sealed_packages(
        repo, base_commit, tuple(roots), above, under_above, tuple(import_paths), test_commit
    )
    known = {path.casefold() for path in normalize_import_paths(import_paths)}
    gate_paths = (
        *import_paths,
        *(d for d in above if d.casefold() not in known and d not in sealed),
    )
    import_roots = _import_roots(gate_paths)
    first_level_units = tuple(
        path for path in paths if _first_level_unit(path, import_roots) is not None
    )
    project = None
    if first_level_units or under_above:
        project = _project_entries(repo, base_commit, tuple(roots), import_roots, sealed)
    safe_roots = _safe_root_modules(
        first_level_units, repo, base_commit, python, import_paths=gate_paths, project=project
    )
    risky = _risky_above_roots(under_above, above, known, python, project, allow_deletions, sealed)
    return tuple(
        path
        for path in paths
        if path in roots
        or path.startswith(tuple(f"{r}/" for r in roots))
        or path.rsplit("/", 1)[-1] in _PROTECTED_NAMES
        or _is_first_level_pth(path, import_roots)
        or (path in first_level_units and path not in safe_roots)
        or path in risky
    )


def _dirs_above_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    """Every directory between the checkout root and a test root (``a/b/tests``: ``a``, ``a/b``),
    #360. In pytest's default (prepend) import mode a test or conftest.py module puts the directory
    above its top package at the front of sys.path (with ``pkg/tests/__init__.py``: ``pkg``,
    checked on pytest 9.1.1), ahead of the checkout root and the import paths. Which one depends on
    the ``__init__.py`` files above the root and on the test commit, so all of them count."""
    parts = (root.split("/")[:depth] for root in roots for depth in range(1, root.count("/") + 1))
    return tuple(dict.fromkeys(map("/".join, parts)))


def _under(path: str, tops: tuple[str, ...]) -> list[tuple[str, str]]:
    """(directory, rest of the path) for each of tops that path is inside, casefolded for APFS."""
    low = path.casefold()
    return [(top, path[len(top) + 1 :]) for top in tops if low.startswith(f"{top}/".casefold())]


def _risky_above_roots(
    paths: tuple[str, ...],
    above: tuple[str, ...],
    import_paths: set[str],
    python: str | None,
    project: tuple[dict[str, set[str]], set[str]] | None,
    allow_deletions: bool,
    sealed: frozenset[str] = frozenset(),
) -> set[str]:
    """Paths in a directory above a test root that could change what a test imports (#360).

    Such a directory may be put at the front of sys.path only after a change: the candidate adds
    or deletes an ``__init__.py`` above the root, or the test commit adds one to the root. So:
    - an ``__init__`` directly in it may be changed only when the base tracks it and the task
      refuses deletions (then it is modified, never added or removed);
    - any other path in it is refused when its top-level entry (``pkg/calc/impl.py``: ``calc``)
      is named like a module of the verifier's interpreter, or like another entry of the base
      under the checkout root, an import path, another such directory or a test root: once the
      directory is in front, that entry replaces the module the tests meant. A directory that is
      an import path (``import_paths``, casefolded) is on sys.path ahead of the interpreter's
      modules already, so only the base's other entries count there: in front, ``src/calc.py``
      wins over the root's ``calc.py``. A sealed directory (#382) is never in front: no name rule.
    Without the module inventory or the base listing every such path stays protected."""
    if not paths:
        return set()
    try:
        names = _importable_names(python or sys.executable)
    except (OSError, ValueError, subprocess.SubprocessError):
        return set(paths)  # no proof of safety: refuse before binding or verification
    if project is None:
        return set(paths)
    entries, tracked = project
    risky = set()
    for path in paths:
        for top, rest in _under(path, above):
            head = rest.split("/", 1)[0]
            if "/" not in rest and _is_init_file(rest):
                if allow_deletions or path not in tracked:
                    risky.add(path)
                continue
            name = head if "/" in rest else _import_name(head)
            if name is None or top in sealed:
                continue
            others = entries.get(name.casefold(), set()) - {f"{top}/{head}".casefold()}
            if others or (name.casefold() in names and top.casefold() not in import_paths):
                risky.add(path)
    return risky


def _project_entries(
    repo: Path | None,
    base_commit: str | None,
    roots: tuple[str, ...],
    import_roots: tuple[str, ...],
    sealed: frozenset[str] = frozenset(),
) -> tuple[dict[str, set[str]], set[str]] | None:
    """The base's importable names, each with the entries (casefolded paths) that carry it: the
    first level of the checkout root and of every other import root, and every part of every
    path under a test root whose directory is not sealed (#382). Also the regular files those
    first levels track. None when git cannot tell (no repo, no base, a failed read): the callers
    then refuse."""
    if repo is None or base_commit is None:
        return None
    dirs = tuple(f"{d}/" for d in import_roots if d)
    try:
        listing = run_git(repo, "ls-tree", "-z", base_commit)
        if dirs:
            listing += run_git(repo, "ls-tree", "-z", base_commit, "--", *dirs)
        tests = run_git(
            repo, "ls-tree", "-r", "-z", "--name-only", base_commit, "--", *(f"{r}/" for r in roots)
        )
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return None
    entries: dict[str, set[str]] = {}
    tracked = set()

    def add(name: str | None, path: str) -> None:
        if name is not None and name.casefold() != "__init__":
            entries.setdefault(name.casefold(), set()).add(path.casefold())

    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, _oid = meta.split()
        leaf = path.rsplit("/", 1)[-1]
        add(leaf if kind == "tree" else _import_name(leaf), path)
        if kind == "blob" and mode in ("100644", "100755"):
            tracked.add(path)
    for path in filter(None, tests.split("\0")):
        parts = path.split("/")
        for depth, part in enumerate(parts, 1):
            if "/".join(parts[: depth - 1]) not in sealed:
                add(part if depth < len(parts) else _import_name(part), "/".join(parts[:depth]))
    return entries, tracked


def _import_roots(import_paths: tuple[str, ...]) -> tuple[str, ...]:
    """The checkout root plus each import path, normalized by the same rule as the tree check
    (#300); an entry with an empty, ``.``, ``..`` or non-ASCII segment raises ImportPathError."""
    return ("", *normalize_import_paths(import_paths))


# Every suffix Python imports a first-level file by: source, sourceless bytecode and extension
# modules. A committed ``.pyc``/``.so`` shadows just as a ``.py`` does (verified on APFS) and is not
# covered by tamper_scan (``.py`` only) or pycache_guard (the TMPDIR prefix, not the checkout).
_SOURCE_SUFFIXES = (".py", ".pyc", ".pyo")
_EXTENSION_SUFFIXES = (".so", ".pyd")


def _import_name(filename: str) -> str | None:
    """The top-level name a first-level *file* would be imported under, or None if it is not an
    importable file. Source/bytecode keep the whole stem, so a dotted stem (``a.b.py``) is not a
    valid module name and is caught by the identifier test in ``_safe_root_modules`` (as the base
    behaviour required); an extension module's name is the part before the first dot, dropping its
    ABI tag (``x.cpython-NN-…​.so``). Matched casefolded for APFS."""
    low = filename.casefold()
    for suffix in _SOURCE_SUFFIXES:
        if low.endswith(suffix):
            return filename[: -len(suffix)]
    for suffix in _EXTENSION_SUFFIXES:
        if low.endswith(suffix):
            return filename.split(".", 1)[0]
    return None


def _is_init_file(filename: str) -> bool:
    """A package ``__init__`` in any importable suffix (.py/.pyc/.so…), case-insensitive."""
    return filename.split(".", 1)[0].casefold() == "__init__" and _import_name(filename) is not None


def _first_level_unit(
    path: str, import_roots: tuple[str, ...]
) -> tuple[str, str, str, bool, str] | None:
    # APFS is case-insensitive, so the prefix and the filename/suffix are matched casefolded:
    # Python resolves ``pkg/__init__.py`` from a committed ``pkg/__INIT__.py`` on this host. The
    # module branch fires only when the path has no further ``/``, so a package ``__init__`` (always
    # one level down) is classified as its package, never as a module named ``__init__``.
    for r in import_roots:
        if not r:
            if "/" not in path:
                name = _import_name(path)
                if name is not None:
                    return (path, r, name, False, "")
            if path.count("/") == 1 and _is_init_file(path.split("/", 1)[1]):
                name = path.split("/")[0]
                return (path, r, name, True, name)
        else:
            prefix = f"{r}/"
            if path.casefold().startswith(prefix.casefold()):
                rel = path[len(prefix) :]  # prefix is the same length in any case
                if "/" not in rel:
                    name = _import_name(rel)
                    if name is not None:
                        return (path, r, name, False, "")
                if rel.count("/") == 1 and _is_init_file(rel.split("/", 1)[1]):
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
            if path.casefold().startswith(prefix.casefold()):
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
    project: tuple[dict[str, set[str]], set[str]] | None = None,
) -> set[str]:
    """The first-level units a candidate may change. A new ``__init__`` in a tracked directory
    counts as tracked only when no other base entry carries the name (``_project_entries``): a
    namespace directory that becomes a regular package wins over a module of that name later on
    sys.path (``foo/__init__.py`` over ``src/foo.py``, #360 review)."""
    if not paths or repo is None or base_commit is None:
        return set()
    import_roots = _import_roots(import_paths)
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
        is_tracked = tracked_blobs.get(path_str, False) or (
            is_pkg and pkg_dir in tracked_trees and _only_entry(project, name, pkg_dir)
        )
        if is_tracked and name.isidentifier() and name.casefold() not in names:
            safe.add(path_str)
    return safe


def _only_entry(project: tuple[dict[str, set[str]], set[str]] | None, name: str, path: str) -> bool:
    """Whether path is the base's only entry named name (None: unknown, so no)."""
    return project is not None and not project[0].get(name.casefold(), set()) - {path.casefold()}


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
