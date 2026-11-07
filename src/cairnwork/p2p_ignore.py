"""#956 item 2: a base test file the repository's own conftests leave out with ``collect_ignore``
is no untargetable file (``untargetable`` below): pytest never collects it, so it binds nothing.

pytest (``main.pytest_ignore_collect``) skips a directory or file X when the nearest conftest at or
above X's parent that has ``collect_ignore`` lists X, as a path relative to that conftest's
directory. It is read here only when that is beyond doubt, on the base and on the test commit
alike (#798: a test commit may change conftests; before the worker the test commit is not fetched,
so base_env_check reads the base alone and plan_p2p reads both after the worker); anything else
leaves the file untargetable (refused, fail closed):

* X is a directory below the test root, or the file itself, and no base ``testpaths`` entry is X,
  lies below X, or is a pattern (pytest never ignores an initial path);
* every conftest from X's parent up to the repository root is read, nearest first. One that is no
  regular UTF-8 Python file, imports ``*``, names ``pytest_plugins``, a collection hook, the plugin
  manager, ``sys.modules``, a module ``__getattr__`` or code that reaches names at run time
  (sys_path_names.DYNAMIC_NAMES, DYNAMIC_ATTRS) ends the search: not ignored;
* the nearest one that names ``collect_ignore`` binds it once, at top level, to a list or tuple of
  string literals, and names it nowhere else (as a name, attribute, import or string).

``collect_ignore_glob`` and ``--ignore`` only add paths pytest skips, so leaving them unread
refuses more, never less; ``testpaths`` is not honoured by the verifier. Not seen: another module
that changes a conftest's list at run time, and an installed plugin whose ``pytest_ignore_collect``
returns False for such a path."""

from __future__ import annotations

import ast
import posixpath
import shlex
import sys
from collections.abc import Callable

from cairnwork.gitcmd import run_git, run_git_bytes
from cairnwork.pytest_config import _render, base_settings
from cairnwork.sys_path_names import DYNAMIC_ATTRS, DYNAMIC_NAMES, IMPORT_MODULES

NAME = "collect_ignore"
OPAQUE = frozenset({"pytest_plugins", "pytest_ignore_collect", "pytest_collect_directory",
                    "pytest_collection", "pluginmanager", "modules", "__getattr__",
                    "__setattr__", "__delattr__"})  # fmt: skip
REGULAR = ("100644", "100755")
UNKNOWN = object()  # a conftest whose collect_ignore cannot be read beyond doubt


def untargetable(task, paths, target, early=False) -> list[str]:
    """#930: base test files no verifier target can name (a path part outside the target grammar,
    such as a dot or a non-ASCII letter), unless the repository's conftests leave them out with
    ``collect_ignore`` (#956 item 2), read on the base and the test commit, or ``early`` (before
    the worker, when the test commit is not fetched yet) on the base alone; after the worker
    plan_p2p reads both again. #997: no other "pytest skips it" exemption."""
    odd = [p for p in paths if not target.fullmatch(p)]
    if not odd:
        return odd
    both = (task.base_commit, task.test_commit) if task.test_commit else ()
    ignored = collect_ignored(task, task.test_roots, (task.base_commit,) if early else both)
    return [p for p in odd if not ignored(p)]


def collect_ignored(task, roots, commits) -> Callable[[str], bool]:
    """Whether pytest skips this base test file through ``collect_ignore`` on every commit given
    (none: never)."""
    readers = [_reader(task.repo, commit) for commit in commits]
    initial = _testpaths(task)

    def ignored(path: str) -> bool:
        folded = path.casefold()  # #1001: roots match case-folded, as under_roots picks the paths;
        n = max((len(r) for r in roots if folded.startswith(f"{r.casefold()}/")), default=0)
        root = path[:n]  # the tracked spelling, so its own conftests are read
        parts = path[len(root) :].strip("/").split("/")
        paths = [posixpath.join(root, *parts[: i + 1]) for i in range(len(parts))]
        free = [x for x in paths if initial is not None and not _initial(x, initial)]
        return bool(readers) and all(any(_skips(read, x) for x in free) for read in readers)

    return ignored


def _testpaths(task) -> tuple[str, ...] | None:
    """The base's testpaths, normalised; None when one is a pattern (it may name any path)."""
    value = dict(base_settings(task.repo, task.base_commit).values).get("testpaths")
    if value is None:
        return ()
    try:
        many = isinstance(value, list)
        entries = [str(v) for v in value] if many else shlex.split(_render(value) or "")
    except ValueError:  # cannot be split: it may name any path
        return None
    if any(c in e for e in entries for c in "*?["):
        return None
    return tuple(posixpath.normpath(e) for e in entries)


def _initial(x: str, initial: tuple[str, ...]) -> bool:
    return any(e == x or e.startswith(f"{x}/") for e in initial)


def _skips(read: Callable[[str], object], x: str) -> bool:
    """Whether the nearest conftest above x that has collect_ignore lists x; every conftest up to
    the root must be read beyond doubt (a farther one could change a nearer one's list)."""
    chain, directory = [], posixpath.dirname(x)
    while True:
        chain.append((directory, read(directory)))
        if not directory:
            break
        directory = posixpath.dirname(directory)
    if any(listed is UNKNOWN for _, listed in chain):
        return False
    here, listed = next(((d, v) for d, v in chain if isinstance(v, tuple)), ("", ()))
    return any(_path(here, v) == x for v in listed)  # type: ignore[union-attr]


def _path(directory: str, value: str) -> str | None:
    """value relative to directory, as pytest resolves it (an absolute value stays absolute, so it
    never equals a repository path); None outside the repository."""
    path = posixpath.normpath(posixpath.join(directory, value))
    return None if path == ".." or path.startswith("../") else path


def _reader(repo, commit: str) -> Callable[[str], object]:
    """For a directory: its conftest's collect_ignore (a tuple), None (no conftest, or one that
    does not name it) or UNKNOWN."""
    entries, modules = {}, set()
    raw = run_git(repo, "ls-tree", "-r", "-z", commit)
    for entry in filter(None, raw.split("\0")):
        meta, path = entry.split("\t", 1)
        modules |= {part.removesuffix(".py") for part in path.split("/")}
        if posixpath.basename(path) == "conftest.py":
            entries[posixpath.dirname(path)] = (meta.split()[0], meta.split()[2])
    seen: dict[str, object] = {}

    def read(directory: str) -> object:
        if directory not in entries:
            return None
        if directory not in seen:
            mode, oid = entries[directory]
            data = run_git_bytes(repo, "cat-file", "blob", oid) if mode in REGULAR else None
            seen[directory] = UNKNOWN if data is None else _listed(data, frozenset(modules))
        return seen[directory]

    return read


def _listed(data: bytes, modules: frozenset[str] = frozenset()) -> object:
    try:
        tree = ast.parse(data)  # bytes: Python's own decoding, coding cookie included
    except (SyntaxError, ValueError):
        return UNKNOWN
    mentions, opaque = 0, False
    for node in ast.walk(tree):
        names = _names(node)
        mentions += names.count(NAME)
        opaque |= any(n in OPAQUE for n in names) or _dynamic(node) or _unsafe(node, modules)
    if opaque:
        return UNKNOWN
    if not mentions:
        return None
    values = [_literal(s) for s in tree.body if _binds(s)]
    return values[0] if mentions == 1 and len(values) == 1 and values[0] is not None else UNKNOWN


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.alias):
        return [node.name.rsplit(".", 1)[-1], node.asname or ""]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.Global | ast.Nonlocal):
        return list(node.names)
    if isinstance(node, ast.arg | ast.keyword):
        return [node.arg or ""]
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return [node.name or ""]
    if isinstance(node, ast.MatchMapping):
        return [node.rest or ""]
    return []


def _dynamic(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
        return True
    if isinstance(node, ast.Name):
        return node.id in DYNAMIC_NAMES or node.id == "__getattr__"
    return isinstance(node, ast.Attribute) and node.attr in DYNAMIC_ATTRS


def _unsafe(node: ast.AST, modules: frozenset[str]) -> bool:
    """An import that could reach the conftest's globals from elsewhere: a relative one, one whose
    top-level name a repository path could provide (``modules``), a module outside the standard
    library and pytest, an IMPORT_MODULES one (``builtins``, ``gc``, ``importlib``), or a name
    in DYNAMIC_NAMES or DYNAMIC_ATTRS."""
    if isinstance(node, ast.alias):
        return {node.name.rsplit(".", 1)[-1], node.asname} & (
            DYNAMIC_NAMES | DYNAMIC_ATTRS
        ) != set()
    if isinstance(node, ast.ImportFrom):
        found = [node.module or ""] if node.level == 0 else [""]
    else:
        found = [a.name for a in node.names] if isinstance(node, ast.Import) else []
    for module in found:
        top = module.split(".")[0]
        known = top == "pytest" or top in sys.stdlib_module_names
        risky = top in IMPORT_MODULES or any(module.startswith(f"{m}.") for m in IMPORT_MODULES)
        if top in modules or not known or risky or module in IMPORT_MODULES:
            return True
    return False


def _binds(node: ast.stmt) -> bool:
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == NAME)  # fmt: skip


def _literal(node: ast.stmt) -> tuple[str, ...] | None:
    value = node.value if isinstance(node, ast.Assign) else None
    if not isinstance(value, ast.List | ast.Tuple):
        return None
    items = [e.value if isinstance(e, ast.Constant) else None for e in value.elts]
    return tuple(items) if all(isinstance(i, str) for i in items) else None  # type: ignore[arg-type]
