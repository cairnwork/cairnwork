"""Record fixture names seen during pytest collection. Record only: never alters the run."""

import functools
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_DIR = os.environ.get("CAIRNWORK_FIXTURE_RECORD_DIR")  # read once: checkout code cannot move it
_META = {
    "recorder_version": 1,
    "settings_digest": os.environ.get("CAIRNWORK_SETTINGS_DIGEST", ""),
    "base_sha": os.environ.get("CAIRNWORK_BASE_SHA", ""),
}
_stages: dict[str, dict[str, Any]] = {}
_seen: set[str] = set()
_count, _failed = [0], [False]


def _safe(hook: Any) -> Any:
    @functools.wraps(hook)
    def run(*args: Any, **kwargs: Any) -> None:
        try:
            hook(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a fault voids the record, never the run
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


@_safe
def pytest_collection_finish(session: Any) -> None:
    _record("finish", session.items, complete=session.testsfailed == 0)
