"""Standalone source for the pytest plugin's runtime exposed-test fact.

This text is concatenated into the materialised plugin. Keep it self-contained: the verifier
checkout may not be importable from the plugin's read-only directory.
"""

EXPOSED_SOURCE = r"""\
import fnmatch
import sys
from pathlib import Path
from types import ModuleType


def _exposed_matches(option, name):
    return any(name.startswith(part) or ("*" in part or "?" in part or "[" in part)
               and fnmatch.fnmatch(name, part) for part in option)


def _exposed_module_classes(value, found, seen):
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, type):
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            _exposed_module_classes(item, found, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _exposed_module_classes(item, found, seen)


def _exposed_live_classes():
    pending, seen, found = list(type.__subclasses__(object)), set(), set()
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        found.add(cls)
        pending.extend(type.__subclasses__(cls))
    return found


def _exposed_uncollected(session):
    root = Path(session.config.rootpath).resolve()
    files = session.config.getini("python_files")
    functions = session.config.getini("python_functions")
    modules = {}
    for module in tuple(sys.modules.values()):
        if not isinstance(module, ModuleType):
            continue
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str):
            continue
        path = Path(filename).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _exposed_matches(files, path.name):
            modules[module.__name__] = (module, relative)
    classes, seen = _exposed_live_classes(), set()
    for module, _ in modules.values():
        for value in module.__dict__.values():
            _exposed_module_classes(value, classes, seen)
    collected = {node.rsplit("[", 1)[0] if node.endswith("]") and "[" in node else node
                 for node in (item.nodeid for item in session.items)}
    result = set()
    for cls in classes:
        module = modules.get(getattr(cls, "__module__", ""))
        if module is None:
            continue
        relative = module[1]
        qualname = getattr(cls, "__qualname__", None)
        if not isinstance(qualname, str):
            continue
        owner = qualname.replace(".", "::")
        for name in dir(cls):
            if not _exposed_matches(functions, name):
                continue
            try:
                value = getattr(cls, name)
            except Exception:
                continue
            if callable(value):
                node = f"{relative}::{owner}::{name}"
                if node not in collected:
                    result.add(node)
                    if len(result) > 5000 or len(node) > 1024:
                        return None
    return sorted(result)


def _exposed_or_none(session):
    try:
        return _exposed_uncollected(session)
    except Exception:  # noqa: BLE001 - collection evidence is optional; never disrupt pytest
        return None
"""


def exposed_values(data: dict) -> tuple[str, ...] | None:
    raw = data.get("uncollected_exposed")
    return (
        tuple(raw) if isinstance(raw, list) and all(isinstance(item, str) for item in raw) else None
    )
