"""Ids that only the unresolved imported-base branch of ``deferred_nodes._members`` invents (#1430).

``_members`` conservatively assumes an imported base may be any earlier class in the file, so a
test added to one class is also emitted under a later class with an imported base, where pytest may
never collect it. ``not_run`` may drop only these ids, and only when collection proves them absent.
"""

from __future__ import annotations

import ast

from cairnwork.deferred_aliases import class_aliases
from cairnwork.deferred_nodes import _DEFS, _Class, _members, _Reach


def speculative_ids(tree: ast.Module) -> frozenset[str]:
    """Ids contributed only by an unresolved imported-base branch of ``_members``."""
    classes: dict[str, _Class] = {}
    speculative: set[str] = set()
    aliases = class_aliases(tree.body)
    reach = _Reach(tree)
    # a name bound twice (class redefined, or assigned): pytest collects only its last binding
    rebound = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}  # fmt: skip
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in classes:
            rebound.add(node.name)
            speculative = {i for i in speculative if not i.startswith(f"{node.name}::")}
        own = {item.name for item in node.body if isinstance(item, _DEFS)}
        targets = [aliases.get(id(node), {}).get(b.id, b.id) if isinstance(b, ast.Name) else ""
                   for b in node.bases]  # fmt: skip
        resolved = {name for target in targets if target in classes for name in classes[target][0]}
        unresolved = {name for target in targets if target and target not in classes
                      and not target.endswith("TestCase") for cls, *_ in classes.values()
                      for name in cls}  # fmt: skip
        members = _members(node, classes, reach, aliases.get(id(node), {}))
        methods, _, case, _ = members
        if node.name not in rebound and (node.name.startswith("Test") or case):
            speculative.update(
                f"{node.name}::{name}"
                for name in methods
                if name.startswith("test")
                and name not in own
                and name in unresolved
                and name not in resolved
            )
        classes[node.name] = members
    return frozenset(speculative)
