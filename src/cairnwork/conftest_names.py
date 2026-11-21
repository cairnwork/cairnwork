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

from cairnwork.conftest_reach import _mentions, _paired_values, _pattern_bound, _with_expr
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.fixture_forms import (
    CODE,
    _aliases,
    _fixture,
    module_level,
    registered,
    resolve_source_aliases,
)
from cairnwork.fixture_forwarding import external_decorator, external_roots, voided_modules
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.imported_callable_uses import assignment_callables, imported_callable_uses

MAX_FILES = 5000  # base Python files read under one conftest's directory
_REQUESTS = frozenset({"usefixtures", "getfixturevalue"})


def taken_names(
    repo: Path, base: str, path: str, old: ast.Module, candidate: dict[str, bytes] | None = None
) -> tuple[frozenset[str], frozenset[str]]:
    """(names the conftest at ``path``, whose base version is ``old``, binds; fixture names the
    base requests or a conftest above binds): a new binding may take neither."""
    names: set[str] = set()
    directory = dirname(path)
    listing, links = _listing(repo, base)
    sources = _base_sources(repo, listing, directory)
    trees: dict[str, ast.Module] = {}
    parent_conftests: list[tuple[str, ast.Module]] = []
    for parent in _parents(directory):
        conftest = join(parent, "conftest.py") if parent else "conftest.py"
        if conftest in sources:  # r2: what its own fixtures request may not be overridden either
            trees[conftest] = tree = _parse(conftest, sources[conftest])
            parent_conftests.append((conftest, tree))
    for file, data in sources.items():
        if _under(file, directory):
            trees[file] = tree = _parse(file, data)
            names |= requested(tree)
    conftests = {f: t for f, t in trees.items() if f.rsplit("/", 1)[-1] == "conftest.py"}
    index = _module_index(listing)
    imported = _imported(repo, listing, conftests, index)
    all_trees = {**trees, **imported}
    if path not in all_trees:
        all_trees[path] = old
    resolved = resolve_source_aliases(all_trees, index)
    for conftest, tree in parent_conftests:
        names |= bound(tree, extra=resolved.get(conftest)) | requested(tree)
    called = imported_callable_uses(imported, index)
    scan = None if links else _scan_trees(repo, listing, candidate or {})
    voided = voided_modules({**scan, **all_trees}) if scan is not None else {"*"}
    for f, tree in imported.items():
        names |= _fixtures(
            tree, extra=resolved.get(f), called=called.get(f, set()), index=index, voided=voided
        )
    return frozenset(bound(old, extra=resolved.get(path))), frozenset(names)


def bound(tree: ast.Module, extra: set[str] | None = None) -> set[str]:
    """Names ``tree`` binds at module level (inside ``if``, ``try``, ``with`` and loops too), and
    every name a fixture in it may register (fixture_forms.registered)."""
    names = registered(tree, extra=extra)
    for node in module_level(tree.body):
        names |= bound_by(node)
    return names


def bound_by(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Assign):
        return {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    if isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For | ast.AsyncFor):
        return {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    if isinstance(node, ast.With | ast.AsyncWith):
        targets = [i.optional_vars for i in node.items if i.optional_vars is not None]
        return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    if isinstance(node, ast.Match):
        return {n for c in node.cases for p in ast.walk(c.pattern) if (n := _pattern_bound(p))}
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
    names = _req_args(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            a = node.args
            names |= {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return names


def _req_args(tree: ast.AST) -> set[str]:
    return {
        s.value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n.func) in _REQUESTS
        for arg in n.args for s in ast.walk(arg)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    }  # fmt: skip


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _fixtures(
    tree: ast.Module,
    extra: set[str] | None = None,
    called: set[str] | None = None,
    index: dict[str, set[str]] | None = None,
    voided: set[str] | None = None,
) -> set[str]:
    """#982 item 4 r2: what an imported module's fixtures take: each fixture's name (a star
    import, ``as`` or ``pytest_plugins`` registers it under that name), what ``registered`` reads,
    its parameters, and every literal string passed to ``usefixtures`` or ``getfixturevalue``. A
    fixture is a function with a decorator naming ``fixture`` or an alias of it, any decorated
    module-level function (a factory decorator from another module may call ``fixture``, #982;
    a method's ``@property`` is not), or one passed to a ``fixture`` call."""
    names = registered(tree, extra=extra) | _req_args(tree)
    selectors = names | set(called or ()) | assignment_callables(tree)
    top, aliases = {id(n) for n in module_level(tree.body)}, _aliases(tree, extra=extra)
    roots = external_roots(tree, index, voided)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        marked = any(  # main's rule kept: "fixture" anywhere in a decorator, even in a string
            _fixture(d, aliases) or "fixture" in ast.unparse(d) for d in node.decorator_list
        )
        possible_factory = any(not external_decorator(d, roots) for d in node.decorator_list)
        if marked or (possible_factory and id(node) in top) or node.name in selectors:
            a = node.args
            names |= {node.name} | {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return names


def _scan_trees(
    repo: Path, listing: dict[str, str], candidate: dict[str, bytes]
) -> dict[str, ast.Module] | None:
    """#1004: every base ``.py`` and every ``.py`` the candidate adds or changes, parsed strictly,
    for the stdlib-patch scan (a patch may sit in a file nothing imports and be loaded by name at
    run time). None, which turns the relaxation off, when there are more than MAX_FILES or one
    does not parse. Not seen: a plugin outside the repository, ``.pth`` files, PYTHONSTARTUP."""
    if len(listing) > MAX_FILES:
        return None
    data = dict(zip(listing, _read(repo, list(listing.values())), strict=True)) | candidate
    trees: dict[str, ast.Module] = {}
    for file, blob in data.items():
        try:
            trees[f"scan:{file}"] = ast.parse(blob)
        except (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError):
            return None
    return trees


def _listing(repo: Path, base: str) -> tuple[dict[str, str], bool]:
    """Every regular ``.py`` blob of the base (path to object id), and whether a symlink named
    ``.py`` exists (#1004: it cannot be scanned, so the relaxation goes off)."""
    try:
        rows = run_git(repo, "ls-tree", "-r", "-z", base).split("\0")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    listing: dict[str, str] = {}
    links = False
    for row in filter(None, rows):
        meta, _, file = row.partition("\t")
        mode, kind, oid = meta.split()
        links = links or (mode == "120000" and file.endswith(".py"))
        if kind == "blob" and mode in ("100644", "100755") and file.endswith(".py"):
            listing[file] = oid
    return listing, links


def _base_sources(repo: Path, listing: dict[str, str], directory: str) -> dict[str, bytes]:
    """The base files under ``directory`` and every base conftest."""
    wanted = {
        f: oid for f, oid in listing.items()
        if _under(f, directory) or f.rsplit("/", 1)[-1] == "conftest.py"
    }  # fmt: skip
    if sum(_under(f, directory) for f in wanted) > MAX_FILES:
        raise TestCommitRejected(CODE, f"more than {MAX_FILES} base Python files under {directory}")
    return dict(zip(wanted, _read(repo, list(wanted.values())), strict=True))


def _module_index(listing: dict[str, str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for file in listing:
        parts = file.removesuffix(".py").removesuffix("/__init__").split("/")
        for i in range(len(parts)):
            index.setdefault(".".join(parts[i:]), set()).add(file)
    return index


def _imported(
    repo: Path,
    listing: dict[str, str],
    trees: dict[str, ast.Module],
    index: dict[str, set[str]] | None = None,
) -> dict[str, ast.Module]:
    """#982 item 4: a fixture is registered where it is imported but requests its dependencies in
    its own module. So the base modules ``trees`` import are read, followed through re-exports. A
    module matches every base file whose path ends with it; one in no base file is third-party
    (not seen). A ``pytest_plugins`` that is not a literal string or list of them imports every
    base file."""
    if index is None:
        index = _module_index(listing)
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
        elif values := _plugins_values(node, tree if "contextlib" not in index else None):
            for value in values:
                items = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
                if not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in items):
                    return set(listing)  # cannot read which modules: all of them
                strings = [v.value for v in items if isinstance(v, ast.Constant)]
                modules |= {m.strip() for v in strings if isinstance(v, str) for m in v.split(",")}
    return {f for m in modules if m for f in index.get(m, ())}


def _match_case_values(pattern: ast.pattern, subject: ast.expr) -> list[ast.expr | None]:
    if not any(_pattern_bound(p) == "pytest_plugins" for p in ast.walk(pattern)):
        return []
    if isinstance(pattern, ast.MatchAs):
        if pattern.name != "pytest_plugins":
            return _match_case_values(pattern.pattern, subject) if pattern.pattern else []
        if isinstance(pattern.pattern, ast.MatchValue):
            return [pattern.pattern.value]
        if isinstance(pattern.pattern, ast.MatchSequence):
            elts = [p.value for p in pattern.pattern.patterns if isinstance(p, ast.MatchValue)]
            if len(elts) == len(pattern.pattern.patterns):
                return [ast.List(elts=elts, ctx=ast.Load())]
        return [subject]
    if isinstance(pattern, ast.MatchSequence) and isinstance(subject, ast.List | ast.Tuple):
        no_star = not any(
            isinstance(x, ast.MatchStar | ast.Starred) for x in (*pattern.patterns, *subject.elts)
        )
        if no_star and len(pattern.patterns) == len(subject.elts):
            pairs = zip(pattern.patterns, subject.elts, strict=True)
            return [v for p, s in pairs for v in _match_case_values(p, s)]
    if isinstance(pattern, ast.MatchOr):
        return [v for sub in pattern.patterns for v in _match_case_values(sub, subject)]
    return [None]


def _plugins_values(node: ast.AST, tree: ast.Module | None = None) -> list[ast.expr | None]:
    """Values bound to or added to ``pytest_plugins``; ``[None]`` stops fail-closed (#1010)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest_plugins":
            ok = node.func.attr in ("append", "extend") and len(node.args) == 1
            return [node.args[0] if ok and not node.keywords else None]
    if isinstance(node, ast.NamedExpr) and node.target.id == "pytest_plugins":
        return [node.value]
    if isinstance(node, ast.AugAssign) and _mentions(node.target):
        return [node.value] if isinstance(node.target, ast.Name) else [None]
    if isinstance(node, ast.AnnAssign):
        return _paired_values(node.target, node.value)
    if isinstance(node, ast.Assign):
        return [v for t in node.targets for v in _paired_values(t, node.value)]
    if isinstance(node, ast.For | ast.AsyncFor) and _mentions(node.target):
        if isinstance(node.iter, ast.List | ast.Tuple) and node.iter.elts:
            if not any(isinstance(e, ast.Starred) for e in node.iter.elts):
                return [v for item in node.iter.elts for v in _paired_values(node.target, item)]
        return [None]
    if isinstance(node, ast.With | ast.AsyncWith):
        out: list[ast.expr | None] = []
        for item in node.items:
            if item.optional_vars is not None and _mentions(item.optional_vars):
                inner = _with_expr(item.context_expr, tree)
                out += [None] if inner is None else _paired_values(item.optional_vars, inner)
        return out
    if isinstance(node, ast.Match):
        return [v for c in node.cases for v in _match_case_values(c.pattern, node.subject)]
    return []


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
