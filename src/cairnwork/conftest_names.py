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
base file that does not parse counts every identifier in it (``_parse``). Not seen: a name a
third-party plugin's fixture requests internally, the ini ``usefixtures`` option, and fixtures
of a ``pytest_plugins`` module outside the directory.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from posixpath import dirname, join

from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes

CODE = "TEST_COMMIT_CONFTEST_STATIC"
MAX_FILES = 5000  # base Python files read under one conftest's directory
_REQUESTS = frozenset({"usefixtures", "getfixturevalue"})


def taken_names(
    repo: Path, base: str, path: str, old: ast.Module
) -> tuple[frozenset[str], frozenset[str]]:
    """(names the conftest at ``path``, whose base version is ``old``, binds; fixture names the
    base requests or a conftest above binds): a new binding may take neither."""
    names: set[str] = set()
    directory = dirname(path)
    sources = _base_sources(repo, base, directory)
    for parent in _parents(directory):
        conftest = join(parent, "conftest.py") if parent else "conftest.py"
        if conftest in sources:  # r2: what its own fixtures request may not be overridden either
            tree = _parse(conftest, sources[conftest])
            names |= bound(tree) | requested(tree)
    for file, data in sources.items():
        if _under(file, directory):
            names |= requested(_parse(file, data))
    return frozenset(bound(old)), frozenset(names)


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


def requested(tree: ast.Module) -> set[str]:
    """Fixture names ``tree`` may request: every parameter of every function, and every literal
    string passed to ``usefixtures`` or ``getfixturevalue``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            a = node.args
            names |= {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
        elif isinstance(node, ast.Call) and _call_name(node.func) in _REQUESTS:
            names |= {
                s.value
                for arg in node.args
                for s in ast.walk(arg)
                if isinstance(s, ast.Constant) and isinstance(s.value, str)
            }
    return names


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


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
