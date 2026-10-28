"""Candidate paths below the first level of an import root that take over a dotted name (#384).

pytest puts the import paths, the checkout root and test directories in front of
site-packages. A directory without ``__init__`` is a namespace portion: PathFinder keeps looking
and, when no module or regular package of the name turns up anywhere on sys.path, merges every
portion in sys.path order. So a candidate ``src/nsco/somelib/impl.py`` is found ahead of an
installed namespace's ``nsco.somelib.impl`` (#384), or of the base's own namespace module under
another root (``acme/core/calc.py`` at the checkout root against ``src/acme/core/calc.py``). And
PathFinder returns that namespace before an appended ``sys.meta_path`` finder (editable installs)
is asked, so a package found only by such a finder is replaced by any directory of its name
(#390). The first-level rule in verifier_spec never looks this deep.

For each path and each import root R holding it, the dotted chain is its directories plus the
module a code file defines (``__init__`` is its directory). It stops at the first part that is
not an identifier: nothing below that is importable by name. Each name is checked against the
verifier's interpreter (``_PROBE``), the base's entries under the other roots (the import roots,
each test root and every directory in one, as pytest's prepend mode may put any of them on
sys.path; which one is in front is not modelled) and the base's own entry under R. At a
directory level the candidate only adds a namespace portion, which loses to any module or
regular package found on sys.path: own module or package (that the task cannot delete),
interpreter ``path``, or nothing anywhere end the walk, allowed. Interpreter ``meta`` (resolved
by find_spec, not by PathFinder) is refused: the portion would win. At the leaf the candidate
defines the module or package itself, refused when any other source has the name, unless the
base already tracks the very path and the task refuses deletions (snapshot writes only regular
files at the listed paths, so it stays the same module). A path is also walked with each
spelling of its directories that the base or another path uses (equal up to case: one directory
on APFS, whose on-disk case Python matches). A probe or listing failure protects every such
path; a base that git says is no commit is left to the later refusals, as in
``pytest_config.gate_import_paths``. Without a repository or base commit (pycache_guard's
per-file check) the rule is not applied; check_task passes both.

Not covered: base code that edits ``sys.path`` itself (#385's limit), data files read through
``importlib.resources`` from a namespace, and new names in a pkgutil-style package probed with
``try: import``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git

# Every suffix Python imports a first-level file by: source, sourceless bytecode and extension
# modules. A committed ``.pyc``/``.so`` shadows just as a ``.py`` does (verified on APFS) and is not
# covered by tamper_scan (``.py`` only) or pycache_guard (the TMPDIR prefix, not the checkout).
_SOURCE_SUFFIXES = (".py", ".pyc", ".pyo")
_EXTENSION_SUFFIXES = (".so", ".pyd")
_KINDS = frozenset({"none", "ns", "path", "meta"})
# Run in the verifier's Python with -I from /, names as JSON on stdin. Only a namespace level
# (no loader, or NamespaceLoader) is ever imported, so no package code runs; finders installed
# by site .pth files do, as for verifier_spec's module inventory.
_PROBE = r"""
import importlib, importlib.util, json, sys
from importlib.machinery import NamespaceLoader, PathFinder

def namespace(spec):
    return spec.origin is None and spec.submodule_search_locations is not None and (
        spec.loader is None or isinstance(spec.loader, NamespaceLoader))

def kind(name):
    parent = name.rpartition(".")[0]
    try:
        spec = importlib.util.find_spec(name)
        found = PathFinder.find_spec(name, sys.modules[parent].__path__ if parent else None)
    except Exception:
        return "meta"
    if spec is None:
        return "none"
    if found is None:
        return "meta"
    if not namespace(found):
        return "path"
    return "ns" if namespace(spec) else "meta"

answer = []
for chain in json.load(sys.stdin):
    kinds = []
    for depth in range(1, len(chain) + 1):
        kinds.append(kind(".".join(chain[:depth])))
        if kinds[-1] != "ns":
            break
        importlib.import_module(".".join(chain[:depth]))
    answer.append(kinds)
print(json.dumps(answer))
"""


def import_name(filename: str) -> str | None:
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


def is_init_file(filename: str) -> bool:
    """A package ``__init__`` in any importable suffix (.py/.pyc/.so…), case-insensitive."""
    return filename.split(".", 1)[0].casefold() == "__init__" and import_name(filename) is not None


def namespace_shadows(
    paths: tuple[str, ...],
    import_roots: tuple[str, ...],
    test_roots: tuple[str, ...],
    sealed: frozenset[str],
    *,
    repo: Path | None,
    base_commit: str | None,
    python: str | None,
    allow_deletions: bool,
) -> set[str]:
    """The paths below the first level of an import root that take over a dotted name."""
    candidates = [p for p in paths if not _under_any(p, test_roots) and _chains(p, import_roots)]
    if not candidates or repo is None or base_commit is None:
        return set()
    try:
        tree = run_git(repo, "ls-tree", "-rz", "--name-only", "--end-of-options", base_commit)
    except GitCommandError:
        if _absent(repo, base_commit):  # nothing runs against it (pytest_config.gate_import_paths)
            return set()
        return set(candidates)  # no proof of safety: refuse before binding or verification
    listing = [path for path in tree.split("\0") if path]
    spellings = _spellings(listing, tuple(candidates))
    walks = {
        path: [found for variant in spellings[path] for found in _chains(variant, import_roots)]
        for path in candidates
    }
    names = sorted({tuple(chain) for found in walks.values() for _, chain, _, _ in found})
    try:
        answer = _kinds(_run_probe(python or sys.executable, names), names)
    except (OSError, ValueError, subprocess.SubprocessError):
        return set(candidates)
    kinds = dict(zip(names, answer, strict=True))
    others = (*import_roots, *_test_dirs(listing, test_roots, sealed))
    firsts = {chain[0].casefold() for chain in names}
    deletable = {path.casefold() for path in paths} if allow_deletions else set()
    base = _Base(listing, others, firsts, deletable)
    return {
        path
        for path, found in walks.items()
        if any(
            _takes_over(variant, root, chain, leaf, kinds[tuple(chain)], base)
            for root, chain, leaf, variant in found
        )
    }


def _takes_over(
    path: str, root: str, chain: list[str], leaf: bool, kinds: list[str], base: _Base
) -> bool:
    """The walk of one chain under one root (see the module docstring)."""
    for depth in range(1, len(chain) + 1):
        parts = tuple(chain[:depth])
        found = kinds[depth - 1] if depth <= len(kinds) else "none"
        taken = found != "none" or base.elsewhere(root, parts)
        if leaf and depth == len(chain):
            return taken and not (path in base.tracked and path.casefold() not in base.deletable)
        if base.concrete(root, parts) or found == "path" or not taken:
            return False
        if found == "meta":
            return True
    return False


class _Base:
    """The base's entries by import root and dotted parts, for names starting like a chain:
    casefolded for the other roots (more is refused), exact for the candidate's own root (only
    an exact entry wins on APFS), there with the files that make it a module or package."""

    def __init__(
        self, listing: list[str], roots: tuple[str, ...], firsts: set[str], deletable: set[str]
    ) -> None:
        self.roots, self.tracked, self.deletable = roots, set(listing), deletable
        self.folded: set[tuple[str, tuple[str, ...]]] = set()
        self.exact: dict[tuple[str, tuple[str, ...]], set[str]] = {}
        for path in listing:
            for root in roots:
                rel = _relative(path, root)
                if rel is None or rel.split("/", 1)[0].split(".", 1)[0].casefold() not in firsts:
                    continue
                parts = rel.split("/")
                self.folded.update((root, _fold(parts[:depth])) for depth in range(1, len(parts)))
                name = import_name(parts[-1])
                if name is None or len(parts) < 2 and is_init_file(parts[-1]):
                    continue
                key = tuple(parts[:-1]) if is_init_file(parts[-1]) else (*parts[:-1], name)
                self.folded.add((root, _fold(key)))
                self.exact.setdefault((root, key), set()).add(path)

    def elsewhere(self, root: str, parts: tuple[str, ...]) -> bool:
        """Any base entry of this name under another root, in any case."""
        return any(other != root and (other, _fold(parts)) in self.folded for other in self.roots)

    def concrete(self, root: str, parts: tuple[str, ...]) -> bool:
        """A module or regular package of exactly this name under root, which the task keeps."""
        files = self.exact.get((root, parts), set())
        return bool(files) and not {path.casefold() for path in files} & self.deletable


def _chains(path: str, import_roots: tuple[str, ...]) -> list[tuple[str, list[str], bool, str]]:
    """(root, dotted parts, leaf is code, path) per import root holding path below its first
    level."""
    found = []
    for root in import_roots:
        rel = _relative(path, root)
        if rel is None or "/" not in rel:
            continue
        parts = rel.split("/")
        name = import_name(parts[-1])
        if is_init_file(parts[-1]):
            chain, leaf = parts[:-1], True
        elif name is not None and name.isidentifier():
            chain, leaf = [*parts[:-1], name], True
        else:
            chain, leaf = parts[:-1], False
        if len(parts) == 2 and is_init_file(parts[-1]):
            continue  # a first-level package: verifier_spec's rule
        cut = next((i for i, part in enumerate(chain) if not part.isidentifier()), None)
        if cut is not None:
            chain, leaf = chain[:cut], False
        if chain:
            found.append((root, chain, leaf, path))
    return found


def _kinds(stdout: str, names: list[tuple[str, ...]]) -> list[list[str]]:
    """The probe's answer, one list of kinds per chain, or ValueError: only the last kind may be
    other than ``ns``, and a list may end early only on such a kind."""
    answer = json.loads(stdout)
    if not isinstance(answer, list) or len(answer) != len(names):
        raise ValueError("namespace probe answer is invalid")
    for kinds, chain in zip(answer, names, strict=True):
        if not (
            isinstance(kinds, list)
            and 1 <= len(kinds) <= len(chain)
            and all(isinstance(kind, str) and kind in _KINDS for kind in kinds)
            and all(kind == "ns" for kind in kinds[:-1])
            and (len(kinds) == len(chain) or kinds[-1] != "ns")
        ):
            raise ValueError("namespace probe answer is invalid")
    return answer


def _run_probe(python: str, names: list[tuple[str, ...]]) -> str:
    return subprocess.run(  # noqa: S603 - the configured verifier Python, fixed code
        [python, "-I", "-c", _PROBE],
        input=json.dumps(names),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/",
    ).stdout


def _spellings(listing: list[str], paths: tuple[str, ...]) -> dict[str, list[str]]:
    """Each path, and the path with a directory spelled as the base or another path spells it
    (equal up to case): on APFS both land in the one directory, whose on-disk case Python
    matches."""
    wanted = {prefix.casefold() for path in paths for prefix in _dir_prefixes(path)}
    spelled: dict[str, set[str]] = {}
    for entry in (*listing, *paths):
        for prefix in _dir_prefixes(entry):
            if prefix.casefold() in wanted:
                spelled.setdefault(prefix.casefold(), set()).add(prefix)
    return {
        path: list(
            dict.fromkeys(
                [path]
                + [
                    other + path[len(prefix) :]
                    for prefix in _dir_prefixes(path)
                    for other in sorted(spelled[prefix.casefold()] - {prefix})
                ]
            )
        )
        for path in paths
    }


def _absent(repo: Path, base_commit: str) -> bool:
    """Whether git says base_commit is no commit of repo (``rev-parse --verify`` exit 1)."""
    try:
        run_git(
            repo,
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{base_commit}^{{commit}}",
        )
    except GitCommandError as exc:
        failed = exc.__cause__
        return isinstance(failed, subprocess.CalledProcessError) and failed.returncode == 1
    return False


def _test_dirs(
    listing: list[str], test_roots: tuple[str, ...], sealed: frozenset[str]
) -> tuple[str, ...]:
    """Each test root and every directory in one, unless sealed (#382)."""
    dirs = dict.fromkeys(test_roots)
    for path in listing:
        dirs.update(dict.fromkeys(d for d in _dir_prefixes(path) if _under_any(d, test_roots)))
    return tuple(d for d in dirs if d not in sealed)


def _dir_prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


def _relative(path: str, root: str) -> str | None:
    if not root:
        return path
    return path[len(root) + 1 :] if path.casefold().startswith(f"{root}/".casefold()) else None


def _under_any(path: str, tops: tuple[str, ...]) -> bool:
    low = path.casefold()
    return any(low == top.casefold() or low.startswith(f"{top}/".casefold()) for top in tops)


def _fold(parts) -> tuple[str, ...]:
    return tuple(part.casefold() for part in parts)
