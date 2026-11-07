"""#916: the names a test-commit conftest may not take.

The differential base probe (p2p_conftest.py) sees base test outcomes only. A new binding that
reuses a name a base test reads can make a check weaker, and a base test that passed keeps passing,
so the probe sees nothing. So:

* a new top-level name (assignment, function, import, or a fixture's ``name=``) may not be one the
  base version of the same conftest already binds (Python and pytest use the last binding);
* a name that can register a fixture (a function, a ``from M import n`` name, a fixture's
  ``name=``) may not be one a base conftest above binds (a fixture here overrides that one) or
  requests (a fixture above that depends on it would get this one), or one a base Python file in
  the conftest's directory requests: any function parameter (test or fixture) and any literal
  name passed to ``usefixtures`` or ``getfixturevalue``. A literal assignment or a plain
  ``import M`` binds no fixture, and another module's globals are its own.

The base files are read in one ``git cat-file --batch``; more than MAX_FILES of them refuses. A
base file that does not parse counts every identifier in it (``_parse``). The ini ``usefixtures``
option counts for every directory (#982). Not seen: a name a third-party plugin's fixture
requests internally, and fixtures of a ``pytest_plugins`` module outside the directory.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from posixpath import dirname, join

from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.pytest_config import PytestConfigError, base_config

CODE = "TEST_COMMIT_CONFTEST_STATIC"
MAX_FILES = 5000  # base Python files read under one conftest's directory
_REQUESTS = frozenset({"usefixtures", "getfixturevalue"})


def taken_names(
    repo: Path, base: str, path: str, old: ast.Module
) -> tuple[frozenset[str], frozenset[str]]:
    """(names the conftest at ``path``, whose base version is ``old``, binds; fixture names the
    base requests or a conftest above binds): a new binding may take neither."""
    directory = dirname(path)
    sources = _base_sources(repo, base, directory)
    every_function, names = _ini_requests(repo, base)  # #982
    trees: list[ast.Module] = []
    for parent in _parents(directory):
        conftest = join(parent, "conftest.py") if parent else "conftest.py"
        if conftest in sources:  # r2: what its own fixtures request may not be overridden either
            trees.append(tree := _parse(conftest, sources[conftest]))
            names |= bound(tree)
    trees += [_parse(f, data) for f, data in sources.items() if _under(f, directory)]
    names |= requested(trees, every_function)  # judged together: pytest collects across files
    return frozenset(bound(old)), frozenset(names)


def _ini_requests(repo: Path, base: str) -> tuple[bool, set[str]]:
    """#982: (the base sets ``python_functions``, so any function may be a test; the names its
    ``usefixtures`` option requests for every test). An unreadable configuration refuses."""
    try:
        _source, config = base_config(repo, base)
        value = config.get("usefixtures") or ()
        words = value.split() if isinstance(value, str) else [str(v) for v in value]
    except (PytestConfigError, GitCommandError, ValueError, TypeError) as exc:
        raise TestCommitRejected(CODE, "cannot read the base pytest configuration") from exc
    return "python_functions" in config, set(words)


def bound(tree: ast.Module) -> set[str]:
    """Top-level names ``tree`` binds, with each fixture's ``name=`` value."""
    names: set[str] = set()
    for node in tree.body:
        names |= bound_by(node)
    return names


def bound_by(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Assign):
        return {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    if isinstance(node, ast.AnnAssign | ast.AugAssign):
        return {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    if isinstance(node, ast.Import | ast.ImportFrom):
        return {(a.asname or a.name).split(".")[0] for a in node.names}
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {node.name} | {
            k.value.value
            for dec in node.decorator_list
            if isinstance(dec, ast.Call)
            for k in dec.keywords
            if k.arg == "name" and isinstance(k.value, ast.Constant)
            and isinstance(k.value.value, str)
        }  # fmt: skip
    return set()


def requested(trees: Iterable[ast.Module], every_function: bool = False) -> set[str]:
    """Fixture names ``trees`` may request: the parameters of each function pytest may hand
    fixtures, and every literal string passed to ``usefixtures`` or ``getfixturevalue``. #982: a
    function counts when its name starts with ``test`` or ``pytest_``, it has a decorator, or its
    name is used in any of ``trees`` other than as a direct call (``pytest.fixture(f)``,
    ``test_x = f``, ``return f``), imported under another name, or written as a string
    (``getattr(m, "f")``); every lambda counts. pytest collects across files, so ``trees`` are
    judged together (r2 of #984). Every function counts, as #916 did, with ``every_function`` (the
    base sets ``python_functions``), when any of ``trees`` defines a collection hook
    (``pytest_pycollect_makeitem``, ``pytest_collect_file``, ...), or when any request's argument
    is not a literal string."""
    names: set[str] = set()
    used: set[str] = set()
    plain: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for tree in trees:
        callees = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            used |= _uses(node, callees)
            if isinstance(node, ast.Call) and _call_name(node.func) in _REQUESTS:
                args = [*node.args, *(k.value for k in node.keywords)]
                strings = [a.value for a in args if isinstance(a, ast.Constant)]
                strings = [v for v in strings if isinstance(v, str)]
                every_function |= len(strings) < len(args)  # one it cannot read: count them all
                names |= set(strings)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                every_function |= _collects(node.name)  # a hook that may collect any function
                if node.decorator_list or node.name.startswith(("test", "pytest_")):
                    names |= _params(node)
                else:
                    plain.append(node)
            elif isinstance(node, ast.Lambda):
                names |= _params(node)
    return names | {p for f in plain if every_function or f.name in used for p in _params(f)}


def _uses(node: ast.AST, callees: set[int]) -> set[str]:
    """Names ``node`` uses other than as a direct call's function."""
    if isinstance(node, ast.Name) and id(node) not in callees:
        return {node.id}
    if isinstance(node, ast.Attribute) and id(node) not in callees:
        return {node.attr}
    if isinstance(node, ast.Import | ast.ImportFrom):  # imported under another name
        return {a.name.rsplit(".", 1)[-1] for a in node.names if a.asname}
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
        return {node.value}
    return set()


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    a = node.args
    return {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _collects(name: str) -> bool:
    """A pytest hook that can collect a function under another rule than ``python_functions``."""
    return name.startswith("pytest_") and "collect" in name


def _base_sources(repo: Path, base: str, directory: str) -> dict[str, bytes]:
    """Every regular ``.py`` blob of the base: those under ``directory`` and the conftests above."""
    try:
        listing = run_git(repo, "ls-tree", "-r", "-z", base).split("\0")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    wanted: dict[str, str] = {}
    for row in filter(None, listing):
        meta, _, file = row.partition("\t")
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755") or not file.endswith(".py"):
            continue
        if _under(file, directory) or file.rsplit("/", 1)[-1] == "conftest.py":
            wanted[file] = oid
    if sum(_under(f, directory) for f in wanted) > MAX_FILES:
        raise TestCommitRejected(CODE, f"more than {MAX_FILES} base Python files under {directory}")
    return dict(zip(wanted, _read(repo, list(wanted.values())), strict=True))


def _read(repo: Path, oids: list[str]) -> list[bytes]:
    if not oids:
        return []
    try:
        out = run_git_bytes(
            repo, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in oids).encode()
        )
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    blobs, at = [], 0
    for oid in oids:
        end = out.find(b"\n", at)
        head = out[at:end].split() if end >= 0 else []
        if len(head) != 3 or head[:2] != [oid.encode(), b"blob"] or not head[2].isdigit():
            raise TestCommitRejected("TEST_COMMIT_UNREADABLE", f"cannot read blob {oid}")
        start = end + 1
        blobs.append(out[start : start + int(head[2])])
        at = start + int(head[2]) + 1
    return blobs


def _parse(path: str, data: bytes) -> ast.Module:
    """A base file the test author cannot change, so one that does not parse (Python 2, newer
    syntax, not UTF-8) is not refused for that (#948 r6, r2 of #916): every identifier in its
    text counts as both bound and requested, as an import and as a test's parameters."""
    try:
        return ast.parse(data)
    except (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError):
        pass
    words = sorted(set(re.findall(r"[A-Za-z_]\w*", data.decode("latin-1"))))
    args = ast.arguments(  # keywords: the field lists differ across Python versions (3.11)
        posonlyargs=[], args=[ast.arg(arg=w) for w in words], kwonlyargs=[], kw_defaults=[],
        defaults=[],
    )  # fmt: skip
    use = ast.Lambda(args=args, body=ast.Constant(value=None))  # every lambda's parameters count
    imports = ast.Import(names=[ast.alias(name=w) for w in words])
    return ast.Module(body=[imports, ast.Expr(value=use)], type_ignores=[])


def _parents(directory: str) -> list[str]:
    """Each directory strictly above ``directory``, the repository root ('') last."""
    out = []
    while directory:
        directory = dirname(directory)
        out.append(directory)
    return out


def _under(file: str, directory: str) -> bool:
    return not directory or file.startswith(f"{directory}/")
