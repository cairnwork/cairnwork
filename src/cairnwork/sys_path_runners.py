"""Test runners a file may start in its own process, tracked by name for ``sys_path_scan`` (#579).

unittest's ``main``/``TestProgram`` run test discovery when the module is not given as a string,
and discovery puts its start directory first on sys.path. pytest's ``main``/``console_main`` run
a whole pytest session in the calling process, whose options (``-o pythonpath=…``, ``-c
other.ini``) can put a directory on sys.path.

The entry points are followed under these import spellings: ``import unittest.util``, ``import
unittest.main as m``, ``from unittest import TestProgram as T``, ``from unittest.main import
main``, ``from unittest import *``. A file may edit sys.path when it uses unittest's entry points
other than in a direct call whose module is a string or not given (a subclass, a name bound to
one, an argument such as ``functools.partial(unittest.main)`` all count), or uses pytest's at all.
"""

from __future__ import annotations

import ast

# Entry points by top-level package. Only unittest's may be called directly with a string module.
RUNNERS = {
    "unittest": frozenset({"main", "TestProgram"}),
    "pytest": frozenset({"main", "console_main"}),
    "_pytest": frozenset({"main", "console_main"}),
}


class Runners:
    """The names one file binds to a runner package by ``import`` (roots) or to an entry point
    by ``from`` import (entries). A longer chain such as ``_pytest.config.main`` already counts
    in ``sys_path_scan`` (``config`` is a ``HELD_IF_IMPORTED`` name)."""

    def __init__(self) -> None:
        self.roots: dict[str, str] = {}
        self.entries: dict[str, str] = {}

    def note(self, node: ast.AST) -> None:
        """Record the names an import binds to a runner package or one of its entry points."""
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                if top in RUNNERS:
                    self.roots[alias.asname or top] = top
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").partition(".")[0]
            for alias in node.names if top in RUNNERS else ():
                if alias.name == "*":
                    self.entries.update(dict.fromkeys(RUNNERS[top], top))
                elif alias.name in RUNNERS[top]:
                    self.entries[alias.asname or alias.name] = top

    def run_in_process(self, nodes: list[ast.AST]) -> bool:
        """Whether nodes use an entry point other than in a direct unittest call with a string
        module (or none)."""
        called = {id(n.func) for n in nodes if isinstance(n, ast.Call) and _string_module(n)}
        for node in nodes:
            top = self._entry(node)
            if top and (top != "unittest" or id(node) not in called):
                return True
        return False

    def _entry(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.entries.get(node.id)
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
            return None
        top = self.roots.get(node.value.id)
        return top if top and node.attr in RUNNERS[top] else None


def _string_module(call: ast.Call) -> bool:
    """Whether call gives its module (first argument, ``module=`` or ``**``) only as a string."""
    given = [*call.args[:1], *(k.value for k in call.keywords if k.arg in {"module", None})]
    return all(isinstance(m, ast.Constant) and isinstance(m.value, str) for m in given)
