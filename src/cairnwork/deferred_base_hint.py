"""#1833: say why "tests do not fail on base" when a changed test class has an unread base.

Message only. The reader (``deferred_nodes.test_functions``) lists a class's tests when its name
starts with ``Test`` or a base is named ``*TestCase``. A class with any other name whose base is
imported from another file gives no test ids, so its tests count as never run and the refusal
looks unexplained. The hint adds a count and a fixed sentence to that refusal's detail; it never
changes a kind, a code or a verdict, never reads a name out of the commit (hidden runs, #1186),
and a read that fails gives no hint."""

import ast

from cairnwork.deferred_tests import TestCommitRejected, _tree
from cairnwork.gitcmd import GitCommandError

HINT_CODE = "TESTS_DO_NOT_FAIL_ON_BASE"
_SENTENCE = (
    "; {n} test class(es) in the changed files have a base imported from another file, which "
    "Cairnwork does not read (it reads classes named Test* or with a base named *TestCase): "
    "rename such a class to Test*"
)


def _root(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _base_name(node: ast.expr) -> str:
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")


def unread_classes(tree: ast.Module) -> int:
    """Top-level classes not named ``Test*`` that have an imported base (not a ``*TestCase``
    name, not a class or function this file defines itself)."""
    local = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imported = {
        (a.asname or a.name).split(".")[0]
        for s in tree.body
        if isinstance(s, (ast.Import, ast.ImportFrom))
        for a in s.names
        if a.name != "*"
    } - local
    return sum(
        1
        for s in tree.body
        if isinstance(s, ast.ClassDef)
        and not s.name.startswith("Test")
        and any(_root(b) in imported and not _base_name(b).endswith("TestCase") for b in s.bases)
    )


def base_hint(repo, test_commit: str, paths, code: str) -> str:
    """The text to append to the detail of a refusal with ``code``; empty for any other code."""
    if code != HINT_CODE:
        return ""
    total = 0
    for path in paths:
        if not path.endswith(".py"):
            continue
        try:
            tree = _tree(repo, f"{test_commit}:{path}")
        except (TestCommitRejected, GitCommandError):
            continue
        total += unread_classes(tree) if tree is not None else 0
    return _SENTENCE.format(n=total) if total else ""
