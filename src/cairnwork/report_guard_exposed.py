"""Standalone source for the pytest plugin's optional exposed-test fact."""

EXPOSED_SOURCE = r"""\
import fnmatch
from types import ModuleType

_TYPE_DICT = type.__dict__["__dict__"]
_TYPE_MRO = type.__dict__["__mro__"]
_TYPE_QUALNAME = type.__dict__["__qualname__"]
_TYPE_MODULE = type.__dict__["__module__"]
_TYPE_SUBCLASSES = type.__dict__["__subclasses__"]
_MAX_CLASSES = 200000
_MAX_PAIRS = 2000000
_MAX_REPORT_BYTES = 0
_EXPOSED_COUNT = 2000
_EXPOSED_NAME = 512
_EXPOSED_TOTAL = 512 * 1024
_COLLECTORS = []


def pytest_collectstart(collector):
    import pytest

    if isinstance(collector, pytest.Module):
        _COLLECTORS.append(collector)


def _target_modules(session):
    modules = {}
    for collector in _COLLECTORS:
        module = collector.__dict__.get("_obj")
        if type(module) is not ModuleType:
            continue
        name = ModuleType.__dict__["__dict__"].__get__(module, ModuleType).get("__name__")
        if type(name) is str:
            modules[name] = collector.nodeid.partition("::")[0]
    return modules


def _live_classes():
    pending, found = [object], {}
    while pending:
        cls = pending.pop()
        if id(cls) in found:
            continue
        found[id(cls)] = cls
        if len(found) > _MAX_CLASSES:
            return None
        pending.extend(_TYPE_SUBCLASSES(cls))
    return found.values()


def _is_test_name(name, functions):
    return any(name.startswith(part) or ("*" in part or "?" in part or "[" in part)
               and fnmatch.fnmatch(name, part) for part in functions)


def _without_params(node):
    path, _, rest = node.partition("::")
    return f"{path}::{rest.split('[', 1)[0]}"


def _exposed_uncollected(session):
    if not session.config.option.collectonly:
        return None
    modules, classes = _target_modules(session), _live_classes()
    if classes is None:
        return None
    collected = {_without_params(item.nodeid) for item in session.items}
    functions = session.config.getini("python_functions")
    result, pairs, total = set(), 0, 0
    for cls in classes:
        path = modules.get(_TYPE_MODULE.__get__(cls, type))
        if path is None:
            continue
        owner = _TYPE_QUALNAME.__get__(cls, type).replace(".", "::")
        seen = set()
        for base in _TYPE_MRO.__get__(cls, type):
            for name, value in _TYPE_DICT.__get__(base, type).items():
                if type(name) is not str or name in seen:
                    continue
                seen.add(name)
                pairs += 1
                if pairs > _MAX_PAIRS:
                    return None
                if not _is_test_name(name, functions) or not (
                        callable(value) or issubclass(type(value), (staticmethod, classmethod))):
                    continue
                node = f"{path}::{owner}::{name}"
                if node not in collected and node not in result:
                    result.add(node)
                    total += len(node)
                    if len(node) > _EXPOSED_NAME or len(result) > _EXPOSED_COUNT:
                        return None
                    if total > _EXPOSED_TOTAL:
                        return None
    return sorted(result)


def _exposed_or_none(session):
    try:
        return _exposed_uncollected(session)
    except BaseException:  # optional evidence must not alter the pytest run
        return None
"""


def exposed_values(data: dict) -> tuple[str, ...] | None:
    raw = data.get("uncollected_exposed")
    return (
        tuple(raw) if isinstance(raw, list) and all(isinstance(item, str) for item in raw) else None
    )
