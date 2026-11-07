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
base file that does not parse counts every identifier in it (``_parse``). What the base modules
these conftests import (``from M import n``, ``pytest_plugins``) request counts too, followed
through re-exports (``_imported``, #982 item 4). Not seen: a name a third-party plugin's fixture
requests internally, the ini ``usefixtures`` option, a module a test module imports, and a module
imported by name at run time (``importlib``).
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
    listing = _listing(repo, base)
    sources = _base_sources(repo, listing, directory)
    trees: dict[str, ast.Module] = {}
    for parent in _parents(directory):
        conftest = join(parent, "conftest.py") if parent else "conftest.py"
        if conftest in sources:  # r2: what its own fixtures request may not be overridden either
            trees[conftest] = tree = _parse(conftest, sources[conftest])
            names |= bound(tree) | requested(tree)
    for file, data in sources.items():
        if _under(file, directory):
            trees[file] = tree = _parse(file, data)
            names |= requested(tree)
    conftests = {f: t for f, t in trees.items() if f.rsplit("/", 1)[-1] == "conftest.py"}
    for tree in _imported(repo, listing, conftests).values():  # #982 item 4
        names |= _fixtures(tree)  # r2: only its fixtures, not every function of app code
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
        return {node.name} | _given(node)
    return set()


def _given(node: ast.AST) -> set[str]:
    """The ``name=`` values of a definition's decorator calls (a fixture's registered name)."""
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return set()
    return {
        k.value.value
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
        for k in dec.keywords
        if k.arg == "name" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)
    }


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


def _fixtures(tree: ast.Module) -> set[str]:
    """#982 item 4 r2: what an imported module's fixtures take: each fixture's name (a star
    import, ``as`` or ``pytest_plugins`` registers it under that name), its ``name=`` and its
    parameters, and every literal string passed to ``usefixtures`` or ``getfixturevalue``. A
    fixture is a function with a decorator naming ``fixture``, or one passed to ``fixture(...)``."""
    wrapped = {
        a.id for n in ast.walk(tree) if isinstance(n, ast.Call) and "fixture" in _call_name(n.func)
        for a in n.args if isinstance(a, ast.Name)
    }  # fmt: skip
    names = {
        s.value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n.func) in _REQUESTS
        for arg in n.args for s in ast.walk(arg)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    }  # fmt: skip
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        marked = any("fixture" in ast.unparse(d) for d in node.decorator_list)
        if marked or node.name in wrapped:
            a = node.args
            names |= {node.name} | _given(node)
            names |= {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return names


def _listing(repo: Path, base: str) -> dict[str, str]:
    """Every regular ``.py`` blob of the base: path to object id."""
    try:
        rows = run_git(repo, "ls-tree", "-r", "-z", base).split("\0")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    listing: dict[str, str] = {}
    for row in filter(None, rows):
        meta, _, file = row.partition("\t")
        mode, kind, oid = meta.split()
        if kind == "blob" and mode in ("100644", "100755") and file.endswith(".py"):
            listing[file] = oid
    return listing


def _base_sources(repo: Path, listing: dict[str, str], directory: str) -> dict[str, bytes]:
    """The base files under ``directory`` and every base conftest."""
    wanted = {
        f: oid for f, oid in listing.items()
        if _under(f, directory) or f.rsplit("/", 1)[-1] == "conftest.py"
    }  # fmt: skip
    if sum(_under(f, directory) for f in wanted) > MAX_FILES:
        raise TestCommitRejected(CODE, f"more than {MAX_FILES} base Python files under {directory}")
    return dict(zip(wanted, _read(repo, list(wanted.values())), strict=True))


def _imported(
    repo: Path, listing: dict[str, str], trees: dict[str, ast.Module]
) -> dict[str, ast.Module]:
    """#982 item 4: a fixture is registered where it is imported but requests its dependencies in
    its own module. So the base modules ``trees`` import are read, followed through re-exports. A
    module matches every base file whose path ends with it; one in no base file is third-party
    (not seen). A ``pytest_plugins`` that is not a literal string or list of them imports every
    base file."""
    index: dict[str, set[str]] = {}
    for file in listing:
        parts = file.removesuffix(".py").removesuffix("/__init__").split("/")
        for i in range(len(parts)):
            index.setdefault(".".join(parts[i:]), set()).add(file)
    out: dict[str, ast.Module] = {}
    seen, todo = set(trees), list(trees.items())
    while todo:
        wanted = sorted(set().union(*(_imports(f, t, index, listing) for f, t in todo)) - seen)
        if len(seen) + len(wanted) > 2 * MAX_FILES:
            raise TestCommitRejected(CODE, f"more than {2 * MAX_FILES} imported base Python files")
        seen.update(wanted)
        blobs = _read(repo, [listing[f] for f in wanted])
        todo = [(f, _parse(f, d)) for f, d in zip(wanted, blobs, strict=True)]
        out |= dict(todo)
    return out


def _imports(
    file: str, tree: ast.Module, index: dict[str, set[str]], listing: dict[str, str]
) -> set[str]:
    """The base files ``tree`` at ``file`` may import (``import``, ``from``, ``pytest_plugins``)."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            up = dirname(file)
            for _ in range(node.level - 1):
                up = dirname(up)
            head = ".".join(filter(None, [*(up.split("/") if node.level else ()), node.module]))
            modules |= {head} | {f"{head}.{a.name}".strip(".") for a in node.names}
        elif (isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) and _assigns(
            node, "pytest_plugins")) or _extends(node):  # fmt: skip
            value = _plugins_value(node)
            items = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
            if not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in items):
                return set(listing)  # cannot read which modules: all of them
            strings = [v.value for v in items if isinstance(v, ast.Constant)]
            modules |= {m.strip() for v in strings if isinstance(v, str) for m in v.split(",")}
    return {f for m in modules if m for f in index.get(m, ())}


def _extends(node: ast.AST) -> bool:
    """A method call on ``pytest_plugins`` (r2): ``append(x)``, ``extend(xs)``, or any other."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    owner = node.func.value
    return isinstance(owner, ast.Name) and owner.id == "pytest_plugins"


def _plugins_value(node: ast.AST) -> ast.expr | None:
    """What a ``pytest_plugins`` statement adds; None (every base file) when it cannot be read."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        ok = node.func.attr in ("append", "extend") and len(node.args) == 1 and not node.keywords
        return node.args[0] if ok else None
    return node.value if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) else None


def _assigns(node: ast.Assign | ast.AnnAssign | ast.AugAssign, name: str) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == name for t in targets)


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
