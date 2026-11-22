"""Record fixture names seen during pytest collection. Record only: never alters the run."""

import functools
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

_DIR = os.environ.get("CAIRNWORK_FIXTURE_RECORD_DIR")  # read once: checkout code cannot move it
_extra: dict[str, Any] = {}
_stages: dict[str, dict[str, Any]] = {}
_seen: set[str] = set()
_count, _failed = [0], [False]
_MISSING: Any = object()


def _str_items(items: list[Any]) -> list[str]:
    kept = [item for item in items if isinstance(item, str)]
    if len(kept) != len(items):
        _failed[0] = True  # non-string sys.path entry: keep only strings, void complete
    return kept


def _snapshot_sys_path() -> list[str]:
    try:  # checkout code can make sys.path raise anything, even SystemExit
        return _str_items(list(sys.path))
    except BaseException:  # noqa: BLE001 - voids the record only
        _failed[0] = True
        return []


def _take_nonce() -> str:
    """The parent's nonce, read to EOF from an inherited pipe and closed before checkout code runs.

    The variable is removed. Missing or unreadable gives "" (the parent then rejects the record)."""
    spec = os.environ.pop("CAIRNWORK_FIXTURE_NONCE_FD", None)
    try:
        fd = int(spec) if spec is not None else -1
    except ValueError:
        return ""
    if fd < 3:
        return ""
    try:
        chunks = []
        while chunk := os.read(fd, 64):
            chunks.append(chunk)
        return b"".join(chunks).decode("ascii")
    except Exception:  # noqa: BLE001 - no nonce means the parent discards the record
        return ""
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


_META = {
    "recorder_version": 2,
    "nonce": _take_nonce(),
    "settings_digest": os.environ.get("CAIRNWORK_SETTINGS_DIGEST", ""),
    "base_sha": os.environ.get("CAIRNWORK_BASE_SHA", ""),
    "sys_path": _snapshot_sys_path(),
}


def _safe(hook: Any) -> Any:
    @functools.wraps(hook)
    def run(*args: Any, **kwargs: Any) -> None:
        try:
            hook(*args, **kwargs)
        except BaseException:  # noqa: BLE001 - checkout code may raise anything; it voids the record only
            _failed[0] = True

    return run


def _names(items: list[Any]) -> set[str]:
    return {n for item in items for n in getattr(item, "fixturenames", ())}


def _record(stage: str, items: list[Any], complete: bool = False) -> None:
    _stages[stage] = {"names": sorted(_names(items)), "count": len(items)}
    if not _DIR:
        return
    record = {
        **_META,
        **_extra,
        "stages": _stages,
        "names": sorted({n for s in _stages.values() for n in s["names"]}),
        "complete": complete and not _failed[0] and len(_stages) == 4,
    }
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=_DIR, delete=False)
    try:
        with handle:
            json.dump(record, handle, sort_keys=True)
        os.replace(handle.name, Path(_DIR) / "record.json")
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


@_safe
def pytest_itemcollected(item: Any) -> None:
    _count[0] += 1
    _seen.update(getattr(item, "fixturenames", ()))


@pytest.hookimpl(tryfirst=True)
@_safe
def pytest_collection_modifyitems(items: list[Any]) -> None:
    _stages["itemcollected"] = {"names": sorted(_seen), "count": _count[0]}
    _record("modifyitems_before", items)


@pytest.hookimpl(trylast=True, specname="pytest_collection_modifyitems")
@_safe
def pytest_collection_modifyitems_last(items: list[Any]) -> None:
    _record("modifyitems_after", items)


def _plugin_modules(config: Any) -> list[dict[str, Any]]:
    try:  # checkout code: any raise, even SystemExit, voids the record only
        raw = []
        for plugin in config.pluginmanager.get_plugins():
            name = getattr(plugin, "__name__", _MISSING)
            if name is _MISSING:
                continue  # no __name__ at all: silently skipped, complete kept
            if not isinstance(name, str):
                _failed[0] = True  # wrong-typed name, even falsey: skip, void complete
                continue
            if name:
                raw.append((name, getattr(plugin, "__file__", None)))
    except BaseException:  # noqa: BLE001 - must not escape into the pytest exit code
        _failed[0] = True
        return []
    entries = []
    for name, file in raw:
        if isinstance(name, str) and (file is None or isinstance(file, str)):
            entries.append({"name": name, "file": file})
        else:
            _failed[0] = True  # non-serializable or wrong-typed metadata: skip, void complete
    return sorted(entries, key=lambda e: (e["name"], e["file"] or ""))


@_safe
def pytest_collection_finish(session: Any) -> None:
    _extra["plugin_modules"] = _plugin_modules(session.config)
    _extra["sys_path_finish"] = _snapshot_sys_path()
    _record("finish", session.items, complete=session.testsfailed == 0)
