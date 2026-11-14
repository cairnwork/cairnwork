"""#793 Step 2: which base tests the test commit removed, from what pytest collected.

The P2P base probe (p2p.py) records the node ids pytest ran on the base for the selected files
(``base_ids``). The same files are then collected, ``--collect-only`` in the same sandbox, ini and
guard (collect_reader.py), on the test commit (``test_ids``). A base id missing there is:

* ``removed`` when the test commit edited its file (a deferred test file), the base file defines
  the test there (a ``def`` of that name in the node id's class, or at module level), and the
  name no longer appears in that file's source on the test commit: the author removed it, so
  its base outcome says nothing about the candidate and it is not required;
* ``dropped`` otherwise. The test commit made pytest stop collecting a base test without
  removing it (a changed base class, decorator, condition, or a test the base file inherits or
  builds dynamically, whose name was never in that file): the test commit is refused
  (``TEST_COMMIT_DROPS_BASE_TESTS``), never read as "nothing to run".

A base id in a file whose collection failed or was skipped on the test commit is unknown, never
removed: refused (``TEST_COMMIT_COLLECT_ERROR``). A collect-only run that cannot be read is
UNCOMPARABLE (``P2P_TEST_COLLECT_UNUSABLE``). ``changed`` is the AST body comparison of tests
present on both commits (p2p_nodes.changed_tests), limited to tests pytest collected on both
sides; a test the AST reader cannot see stays a P2P test (at worst a wrong refusal, never a
wrong acceptance). The removed tests join ``changed``: both are left to the author's verifiers.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from cairnwork.collect_reader import read_collected
from cairnwork.conftest_check import conftest_of
from cairnwork.deferred_tests import BaseUncomparable, Comparison, TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.node_ids import show, unmatched_note
from cairnwork.p2p_bindings import (
    binds_in_scope,
    hidden_bindings,
    is_pytest_decorator,
    last_binding,
    last_module_def,
    mentions_ast,
    rebinds_by_string,
    walk_without_nested_classes,
)
from cairnwork.p2p_nodes import changed_tests, module_changed
from cairnwork.p2p_selection import MODULE_DIR_CAP
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers


@dataclass(frozen=True)
class CollectedSide:
    base_ids: frozenset[str]  # node ids the base probe ran in the selected files
    test_ids: frozenset[str]  # node ids pytest collected in those files on the test commit
    removed: frozenset[str]  # base ids the test commit removed (not required on the candidate)
    dropped: frozenset[str]  # base ids no longer collected but not removed: refused
    changed_files: frozenset[str] = frozenset()  # selected files the test commit edits
    changed: frozenset[str] = frozenset()  # path::function ids left to the author's verifiers
    # #798: base tests of a file whose module-level code the test commit changes (with a conftest
    # in the commit): left out of the differential probe's comparison only; P2P still runs them
    probe_only: frozenset[str] = frozenset()

    def note(self) -> dict:
        """Bound into the P2P rule: counts and hashes, so the claim is auditable."""
        base, test = self.base_ids, self.test_ids
        return {"base_ran": len(base), "base_ran_sha256": _digest(base),
                "test_collected": len(test), "test_collected_sha256": _digest(test),
                "removed": len(self.removed)}  # fmt: skip


def collected_side(task, files, outcomes, test_files, ctx, budget, attempt_id) -> CollectedSide:
    """The test-commit side of the selected ``files``; see the module docstring."""
    base_ids = frozenset(outcomes)
    present = _present(task, files)
    test_ids: frozenset[str] = frozenset()
    if present:
        got = _collect(task, present, ctx, budget, attempt_id)
        unknown = sorted(n for n in base_ids if _file(n) in {*got.error_files, *got.skipped_files})
        if unknown:
            raise TestCommitRejected("TEST_COMMIT_COLLECT_ERROR", _named(unknown))
        test_ids = frozenset(got.ids)
    edited = frozenset(test_files) & frozenset(files)
    there = frozenset(present)
    side = classify(
        base_ids,
        test_ids,
        edited,
        lambda p: _source(task, task.test_commit, p) if p in there else "",
        lambda p: _source(task, task.base_commit, p),
    )
    if side.dropped:
        detail = _named(sorted(side.dropped))
        note = unmatched_note(side.dropped, side.test_ids)
        raise TestCommitRejected(
            "TEST_COMMIT_DROPS_BASE_TESTS", f"{detail}; {note}" if note else detail
        )
    paths = tuple(sorted(edited))
    both = {_function(n) for n in base_ids & test_ids}
    body = changed_tests(task.repo, task.base_commit, task.test_commit, paths) if paths else set()
    changed = frozenset(body & both) | {_function(n) for n in side.removed}
    probe_only: frozenset[str] = frozenset()
    if conftest_of(test_files):  # #798: module-level code of an edited file may use a new fixture
        over = module_changed(task.repo, task.base_commit, task.test_commit, paths)
        probe_only = frozenset(_function(n) for n in base_ids & test_ids if _file(n) in over)
    return CollectedSide(
        side.base_ids, side.test_ids, side.removed, side.dropped, edited, changed, probe_only
    )


def classify(base_ids, test_ids, edited, source, base_source) -> CollectedSide:
    """Split the base ids the test commit no longer collects into removed and dropped.

    ``source`` / ``base_source`` give a file's text on the test commit / the base."""
    removed, dropped, texts, bases, test_trees = set(), set(), {}, {}, {}
    collected_funcs = {_function(n) for n in test_ids}
    for nodeid in base_ids - test_ids:
        path = _file(nodeid)
        gone = False
        if path in edited and _function(nodeid) not in collected_funcs:
            if path not in texts:
                texts[path] = source(path)
                bases[path] = _tree(base_source(path))
                test_trees[path] = _tree(texts[path])
            if parts := _function(nodeid).split("::")[1:]:
                *owners, name = parts
                gone = (
                    _defines(bases[path], owners, name)
                    and test_trees[path] is not None
                    and not _scope_mentions(test_trees[path], owners, name, texts[path])
                    and not _hidden(test_trees[path], bases[path], name, path, test_ids, base_ids)
                )
        (removed if gone else dropped).add(nodeid)
    return CollectedSide(
        frozenset(base_ids), frozenset(test_ids), frozenset(removed), frozenset(dropped)
    )


def _hidden(tree, base, name, path, test_ids, base_ids) -> bool:
    """#1127: whether the test commit keeps ``name`` bound where pytest does not collect it (a
    shadowed same-name class or method, a class pytest skips, a nested class, a function body).
    The base test's code is then still there, only hidden: dropped, never removed. A binding the
    base already had in the same scope, unchanged and not collected there either, does not
    count; each copy counts once, so a second copy of an excused one is hidden."""

    def got(ids):
        return {_function(n).partition("::")[2] for n in ids if _file(n) == path}

    try:
        kept = hidden_bindings(tree, name, got(test_ids))
        # #1122: the base side keeps the narrow shadowing check, so its excuse never grows (#1144)
        excused = hidden_bindings(base, name, got(base_ids), annotations=False)
        return bool(kept - excused)  # counts, not a set
    except RecursionError:  # ast.dump of a very deep expression: not provably gone
        return True


OVER_BUDGET = ("base-probe-timeout", "base-probe-report-over-cap", "base-probe-log-over-cap")


def no_targets(rule: dict, existing, why: str = "") -> BaseUncomparable:
    """#793 Step 3: the base has test files under the roots but none can be a regression target
    (the selection holds none, the whole base is over the cap or the budget): UNCOMPARABLE
    ``P2P_NO_TARGETS``, never verified."""
    detail = f"{len(existing)} base test file(s), none usable in {rule['applied']} mode"
    detail += f" ({why})" if why else ""
    cut = rule.get("capped_dirs")  # #333: a cut is named
    detail += f"; capped: {', '.join(cut)} (over {rule['cap']} test files)" if cut else ""
    return BaseUncomparable(Comparison("UNCOMPARABLE", "P2P_NO_TARGETS", detail))


def whole_base(rule: dict, existing) -> tuple[str, ...]:
    """#793 Step 3 (c): module-dir selected nothing, so the regression comparison is every base
    test file under the roots, when that is within the cap; a cut or a larger base is
    UNCOMPARABLE (the budget is checked by the probe that runs them)."""
    if rule.get("capped_dirs") or len(existing) > MODULE_DIR_CAP:
        raise no_targets(rule, existing, f"over {MODULE_DIR_CAP} test files")
    rule |= {"selected_files": len(existing), "fallback": "all-base-tests"}
    return existing


def _collect(task, files, ctx, budget, attempt_id):
    spec = VerifierSpec("p2p-collect", files, collect_only=True)
    runs = run_verifiers(ctx, task.test_commit, (spec,), f"{attempt_id}-c", timeout=budget)
    try:
        got = read_collected(runs[0])
    finally:
        for run in runs:  # evidence for this plan only, not for Core (as the base probe)
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if got.unusable:
        detail = f"{got.unusable} on the test commit"
        raise BaseUncomparable(Comparison("UNCOMPARABLE", "P2P_TEST_COLLECT_UNUSABLE", detail))
    return got


def _present(task, files) -> tuple[str, ...]:
    """The selected files that exist on the test commit (a deleted one is collected as absent)."""
    try:
        raw = run_git(
            task.repo, "ls-tree", "-r", "-z", "--name-only", task.test_commit, "--", *files
        )
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    there = set(raw.split("\0"))
    return tuple(f for f in files if f in there)


def _tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):  # 3.11 cannot build a very deep AST
        return None  # nothing is provably defined: the id is dropped, never removed


def _scope_mentions(tree: ast.Module, owners: list[str], name: str, text: str = "") -> bool:
    """Whether static source evidence can account for a collected name.

    This cannot see dynamically built names (for example ``setattr(A, 'test_' + 'x', f)``),
    setattr executed from inside another class, or rebinding through ``type(...)``.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    if not owners:
        module = [n for n in tree.body if not isinstance(n, ast.ClassDef)]
        return _mentions(module, name, pattern)
    body = tree.body
    target_class: ast.ClassDef | None = None
    for owner in owners:
        found = [n for n in body if isinstance(n, ast.ClassDef) and n.name == owner]
        if not found:  # #1127: identifiers are NFKC-normalized, the raw text is not
            return bool(pattern.search(text)) or _mentions(tree.body, name, pattern)
        target_class = found[-1]
        body = target_class.body
    if target_class is None:
        return bool(pattern.search(text))
    if target_class.keywords or not all(map(is_pytest_decorator, target_class.decorator_list)):
        return True
    imported_testcase = _is_unambiguous_testcase(tree)
    if any(
        _base_may_supply(base, name, tree, set(), imported_testcase) for base in target_class.bases
    ):
        return True
    module = [n for n in tree.body if not isinstance(n, ast.ClassDef)]
    nodes = [*body, *module]
    if any(_mentions_dunder_test(node) for node in nodes):
        return True
    if _mentions(body, name, pattern, include_nested_classes=False):
        return True
    return _mentions(module, name, pattern)


def _base_may_supply(
    base: ast.expr, name: str, tree: ast.Module, seen: set[str], imported_testcase: bool
) -> bool:
    """Conservatively decide whether a class base can supply a test name."""
    if isinstance(base, ast.Name):
        if base.id == "object":
            return False
        if base.id == "TestCase":
            return not imported_testcase
        if base.id in seen:
            return True
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base.id]
        bindings = [n for n in tree.body if binds_in_scope(n, base.id, module=True)]
        if len(classes) != 1 or len(bindings) != 1 or bindings[0] is not classes[0]:
            return True
        cls = classes[0]
        if cls.keywords or any(not is_pytest_decorator(d) for d in cls.decorator_list):
            return True  # an inherited metaclass or a decorator on the base can add tests
        if _module_mutates_base(tree, base.id) or _class_binds_hook(cls):
            return True  # __init_subclass__ runs on A and can add tests the source never names
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        if _mentions(cls.body, name, pattern, include_nested_classes=False):
            return True
        return any(
            _base_may_supply(b, name, tree, seen | {base.id}, imported_testcase) for b in cls.bases
        )
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        if base.value.id == "unittest" and base.attr == "TestCase":
            return False
    return True


def _is_unambiguous_testcase(tree: ast.Module) -> bool:
    bindings = [n for n in tree.body if binds_in_scope(n, "TestCase", module=True)]
    return (
        len(bindings) == 1
        and isinstance(bindings[0], ast.ImportFrom)
        and bindings[0].module == "unittest"
        and any(a.name == "TestCase" and a.asname in {None, "TestCase"} for a in bindings[0].names)
    )


def _module_mutates_base(tree: ast.Module, name: str) -> bool:
    for stmt in tree.body:
        pending: list[ast.AST] = [stmt]
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "__init_subclass__"
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == name
            ):
                return True
            pending.extend(ast.iter_child_nodes(node))
    return False


def _class_binds_hook(cls: ast.ClassDef) -> bool:
    return any(binds_in_scope(stmt, "__init_subclass__") for stmt in cls.body)


def _mentions_dunder_test(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "__test__")
        or (isinstance(n, ast.Constant) and n.value == "__test__")
        or (isinstance(n, ast.arg) and n.arg == "__test__")
        for n in ast.walk(node)
    )


def _mentions(
    nodes: list[ast.stmt], name: str, pattern: re.Pattern, *, include_nested_classes: bool = True
) -> bool:
    """Search source-level identifiers, bindings, attributes and string constants."""
    for node in nodes:
        walk = ast.walk(node) if include_nested_classes else walk_without_nested_classes(node)
        for n in walk:
            if mentions_ast(n, name, pattern, defs=True):
                return True
    return False


def _defines(tree: ast.Module | None, owners: list[str], name: str) -> bool:
    """Whether the base file itself has ``def name`` in the class chain ``owners``."""
    if tree is None or rebinds_by_string(tree, name):  # #1127: may collect other code
        return False
    body = tree.body
    module = True
    for owner in owners:
        try:
            last = last_binding(body, owner, module=module)
        except ValueError:
            return False
        if not isinstance(last, ast.ClassDef) or last.name != owner:  # #1122: a walrus in
            return False  # another class's header rebinds the name; it is not this class
        body = last.body
        module = False
    try:
        last = last_binding(body, name, module=module)
    except ValueError:
        if not module:
            return False
        try:
            last = last_module_def(body, name)
        except RecursionError:
            return False
    # #1122: a walrus in another def's header (annotation, default, decorator) is the last binding
    return isinstance(last, (ast.FunctionDef, ast.AsyncFunctionDef)) and last.name == name


def _source(task, commit: str, path: str) -> str:
    """The file's text on ``commit`` (it is there: a failed read is a refusal)."""
    try:
        return run_git(task.repo, "show", f"{commit}:{path}")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", f"{path} is not UTF-8") from exc


def _file(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _function(nodeid: str) -> str:
    return (p := nodeid.partition("::"))[0] + p[1] + p[2].split("[", 1)[0]


def _digest(ids) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def _named(ids: list[str]) -> str:
    shown = ", ".join(map(show, ids[:5]))
    return f"{len(ids)} base test(s): {shown}" + (" ..." if len(ids) > 5 else "")


def under_roots(raw: bytes, roots) -> list[str]:
    """Tracked paths (``ls-tree -z`` output) under the test roots, compared case-folded (#1001: on
    APFS a tracked Tests/ is the tests/ root pytest runs, but a git pathspec spelled tests misses
    it); a name that is not UTF-8 is kept, never a crash."""
    folded = tuple(f"{root.casefold()}/" for root in roots)
    names = raw.decode("utf-8", "surrogateescape").split("\0")
    return [p for p in names if p.casefold().startswith(folded)]
