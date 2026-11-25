"""#1768: the file set pytest collects against the file set Cairnwork counts, on the base.

Step 1: a pytest config below the root that sets a test-naming key. The verifier's pinned ini
never reads it, so a run from the root cannot see it; it is refused on its own narrow rule.
Anything that cannot be read as a config is refused too, never passed.

Step 2: ``file_parity`` collects the directories the P2P run covers (collect-only, the
verifier's own sandbox and ini) and compares the files pytest collected with the files
Cairnwork counts (tracked, under those directories, matching ``python_files``). A counted file
pytest did not collect (``collect_ignore``, ``collect_ignore_glob``, ``pytest_ignore_collect``,
a hook that empties a module) is UNCOMPARABLE ``P2P_BASE_FILES_UNCOLLECTED``; a run that cannot
be read is ``P2P_BASE_COLLECT_UNUSABLE``, never a pass. A module that defines nothing pytest
would collect (helpers, mixins) and a skipped module are not hidden files. Items inside a
collected file are not compared: the verifier needs the file set only.
"""

from __future__ import annotations

import ast
import builtins
import shutil
from pathlib import Path

from cairnwork.collect_reader import _unclean
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.node_file import file_part
from cairnwork.pytest_config import (
    DEFAULT_PYTHON_FILES,
    _file_matches,
    base_settings,
    configured_python_files,
)
from cairnwork.pytest_config_files import _CANDIDATES, PytestConfigError, _section
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

NAMING_KEYS = ("python_files", "python_classes", "python_functions")
NESTED = "P2P_BASE_NESTED_NAMING"
UNCOLLECTED = "P2P_BASE_FILES_UNCOLLECTED"
UNUSABLE = "P2P_BASE_COLLECT_UNUSABLE"
_FORMS = {(name, section): form for name, section, form in _CANDIDATES}
_NAMES = frozenset(name.casefold() for name, _ in _FORMS)  # APFS reads PYTEST.INI


def nested_naming(task) -> None:
    """Raise UNCOMPARABLE ``P2P_BASE_NESTED_NAMING`` when a config file below the base's root sets
    ``python_files``, ``python_classes`` or ``python_functions`` (or cannot be read)."""
    try:
        listing = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    except GitCommandError as exc:
        raise _refuse("the base tree cannot be listed") from exc
    for entry in filter(None, listing.decode("utf-8", "surrogateescape").split("\0")):
        meta, _, path = entry.partition("\t")
        if "/" not in path or path.rsplit("/", 1)[1].casefold() not in _NAMES:
            continue
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise _refuse(f"{_shown(path)} is not a regular file")
        try:
            text = run_git(task.repo, "cat-file", "blob", oid)
            keys = _keys(path.rsplit("/", 1)[1], text)
        except (GitCommandError, UnicodeDecodeError, PytestConfigError) as exc:
            raise _refuse(f"{_shown(path)} cannot be read") from exc
        if keys:
            raise _refuse(f"{_shown(path)} sets {', '.join(keys)}")


def _keys(name: str, text: str) -> list[str]:
    found: set[str] = set()
    for (file, section), form in _FORMS.items():
        if file == name.casefold():
            table = _section(text, section, form, file)
            opts = str(table.get("addopts", "")) if table else ""  # ``-o python_files=...``
            found |= {k for k in NAMING_KEYS if table and (k in table or k in opts)}
    return sorted(found)


def _shown(path: str) -> str:
    return path.encode("ascii", "backslashreplace").decode()[:80]  # a path is candidate text


def _refuse(why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", NESTED, f"{why} below the root"))


def file_mismatch(counted, collected) -> list[str]:
    """The counted files pytest did not collect, sorted (an extra collected file is no mismatch)."""
    return sorted(set(counted) - set(collected))


def file_parity(task, files, ctx, budget, attempt_id) -> None:
    """Raise UNCOMPARABLE when the base's collection and its counted test files differ in files,
    for the directories of the selected ``files`` (what this P2P run covers)."""
    dirs = _dirs(files)
    counted = _counted(task, dirs) if dirs else []
    if not counted:
        return
    spec = VerifierSpec("p2p-base-files", dirs, collect_only=True, ignore=_ignored(task, dirs))
    runs = run_verifiers(ctx, task.base_commit, (spec,), f"{attempt_id}-f", timeout=budget)
    try:
        collected, why = _collected_files(runs[0])
    finally:
        for run in runs:  # evidence for this plan only, not for Core
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if why:
        raise _refuse_with(UNUSABLE, f"{why} on the base")
    missing = [p for p in file_mismatch(counted, collected) if _may_have_tests(task, p)]
    if missing:
        shown = ", ".join(_shown(p) for p in missing[:3])
        raise _refuse_with(
            UNCOLLECTED, f"{len(missing)} counted test file(s) not collected: {shown}"
        )


def _dirs(files) -> tuple[str, ...]:
    """The parent directories of ``files``: where the P2P run reads (a module-dir run takes the
    files of a directory, not of its subdirectories)."""
    return tuple(sorted({f.rsplit("/", 1)[0] for f in files if "/" in f}))


def _tracked(task) -> list[str]:
    try:
        raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", "--name-only", task.base_commit, "--")
    except GitCommandError as exc:
        raise _refuse_with(UNUSABLE, "the base test files cannot be listed") from exc
    return raw.decode("utf-8", "surrogateescape").split("\0")


def _ignored(task, dirs) -> tuple[str, ...]:
    """Tracked subdirectories of ``dirs`` the run does not read: not in ``dirs``, not above one."""
    keep = {x for d in dirs for x in (d, *_above(d))}
    below = {
        parent
        for p in _tracked(task)
        for parent in _above(p)
        if any(parent.startswith(f"{d}/") for d in dirs)
    }
    return tuple(sorted(below - keep))


def _above(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _counted(task, dirs) -> list[str]:
    """Tracked files directly in ``dirs`` that match the base's ``python_files`` as pytest reads
    them: a pattern with a ``/`` matches the checkout's absolute path, so no default is added here
    (that widening would count files pytest never collects)."""
    try:
        patterns = configured_python_files(task.repo, task.base_commit) or DEFAULT_PYTHON_FILES
    except PytestConfigError as exc:
        raise _refuse_with(UNUSABLE, "the base test files cannot be listed") from exc
    where = {d.casefold() for d in dirs}
    return sorted(
        p
        for p in _tracked(task)
        if "/" in p
        and p.rsplit("/", 1)[0].casefold() in where
        and any(_file_matches(x, p) for x in patterns)
    )


def _collected_files(run) -> tuple[set[str], str]:
    """(files pytest reached, "") or (set(), why the run says nothing about files). A skipped
    module (``importorskip``, a module-level skip) was reached, its tests are not known."""
    why = _unclean(run)
    if why:
        return set(), why
    if run.collect_errors or run.returncode not in (0, 5):
        return set(), f"collect-rc-{run.returncode}"  # a failed collector: its files are unknown
    return {file_part(n) for n in (*run.collected, *run.collect_skipped)}, ""


def _may_have_tests(task, path: str) -> bool:
    """Whether a counted file nobody collected from could have held a test. A module that binds no
    ``test*`` name and no ``Test*`` class pytest would take (a helper, a mixin, a class with
    ``__init__``) collects nothing, and that is not a hidden file. Any doubt says yes: a custom
    naming rule, dynamic binding, an unreadable or unparsable source."""
    try:
        if {"python_classes", "python_functions"} & {
            k for k, _ in base_settings(task.repo, task.base_commit).values
        }:
            return True
        tree = ast.parse(run_git(task.repo, "cat-file", "blob", f"{task.base_commit}:{path}"))
    except (GitCommandError, PytestConfigError, UnicodeDecodeError, SyntaxError, ValueError,
            RecursionError, MemoryError):  # fmt: skip
        return True
    try:
        return _dynamic(tree) or _scan(tree.body, _bound_names(tree.body))
    except RecursionError:
        return True


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DYNAMIC = frozenset(
    {"globals", "locals", "vars", "setattr", "exec", "eval", "__import__", "__dict__", "modules"}
)
_CTOR = ("__init__", "__new__")


def _named(name: str) -> bool:
    return name.startswith(("test", "Test"))


def _dynamic(tree) -> bool:
    """A star import, a ``global``, a test-named walrus, or any way to bind a name at run time
    (by name or by attribute, called or just referenced)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return True
        if isinstance(node, ast.NamedExpr) and _named(getattr(node.target, "id", "")):
            return True
        if isinstance(node, ast.Name) and node.id in _DYNAMIC:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC:
            return True
    return False


def _scope(nodes):
    """Every node of this scope: statements and expressions, not the bodies of functions and
    lambdas (their names are local), not the body of a class (it is its own scope)."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*_DEFS, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _binds(node, bound=None) -> bool:
    """Whether this node binds a ``test*``/``Test*`` name, or is a class that may collect."""
    if isinstance(node, _DEFS):
        return _named(node.name)
    if isinstance(node, ast.ClassDef):
        return _class_may_collect(node, bound if bound is not None else set())
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(_named((a.asname or a.name).split(".")[0]) for a in node.names)
    if isinstance(node, ast.Name):
        return isinstance(node.ctx, (ast.Store, ast.Del)) and _named(node.id)
    if isinstance(node, ast.ExceptHandler):
        return bool(node.name) and _named(node.name)
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return bool(node.name) and _named(node.name)
    if isinstance(node, ast.MatchMapping):
        return bool(node.rest) and _named(node.rest)
    return False


def _scan(nodes, bound) -> bool:
    return any(_binds(n, bound) for n in _scope(nodes))


def _bound_names(nodes) -> set[str]:
    """Every name the module scope binds, except the plain ``import abc`` and
    ``from abc import ABC`` (a rebound ``object``/``ABC``/``list`` is not a plain base)."""
    out: set[str] = set()
    for n in _scope(nodes):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                plain = (
                    isinstance(n, ast.Import) and a.name == "abc" and a.asname is None
                ) or (isinstance(n, ast.ImportFrom) and n.module == "abc" and a.name == "ABC"
                      and a.asname in (None, "ABC"))  # fmt: skip
                if not plain:
                    out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (*_DEFS, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            out.add(n.rest)
    return out


def _plain_base(base, bound) -> bool:
    if isinstance(base, ast.Attribute):  # ``abc.ABC``
        return (
            base.attr == "ABC"
            and isinstance(base.value, ast.Name)
            and base.value.id == "abc"
            and "abc" not in bound
        )
    if not isinstance(base, ast.Name) or base.id in bound:
        return False
    return base.id in ("object", "ABC") or isinstance(getattr(builtins, base.id, None), type)


def _has_constructor(node: ast.ClassDef) -> bool:
    """A class whose ``__init__``/``__new__`` is bound once, by a def, and never deleted."""
    direct = [m for m in node.body if isinstance(m, _DEFS) and m.name in _CTOR]
    binds = 0
    for n in _scope(node.body):
        if isinstance(n, ast.Delete):
            return False
        binds += (
            (isinstance(n, _DEFS) and n.name in _CTOR)
            or (isinstance(n, ast.Name) and n.id in _CTOR)
            or (
                isinstance(n, (ast.Import, ast.ImportFrom))
                and any((a.asname or a.name) in _CTOR for a in n.names)
            )  # fmt: skip
        )
    return bool(direct) and binds == len(direct)


def _class_may_collect(node: ast.ClassDef, bound) -> bool:
    if node.decorator_list or node.keywords or not all(_plain_base(b, bound) for b in node.bases):
        return True  # a decorator, a metaclass or an unknown base may make it collectable
    if not node.name.startswith("Test") or _has_constructor(node):
        return False  # a mixin or a helper, or a class pytest skips for its constructor
    return any(isinstance(n, ast.ClassDef) or _binds(n, bound) for n in _scope(node.body))


def _refuse_with(code: str, why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", code, why))
