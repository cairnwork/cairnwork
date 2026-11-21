"""Record fixture names observed during pytest collection."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_stages: dict[str, dict[str, Any]] = {}
_collected_names: set[str] = set()
_collected_count = 0


def _record(stage: str, items: list[Any], complete: bool = False) -> None:
    """Update the collection record atomically when a run directory is available."""
    names: set[str] = set()
    for item in items:
        names.update(item.fixturenames)
    _stages[stage] = {"names": sorted(names), "count": len(items)}
    directory = os.environ.get("CAIRNWORK_FIXTURE_RECORD_DIR")
    if not directory:
        return
    record = {
        "stages": _stages,
        "names": sorted({name for value in _stages.values() for name in value["names"]}),
        "recorder_version": 1,
        "settings_digest": os.environ.get("CAIRNWORK_SETTINGS_DIGEST", ""),
        "base_sha": os.environ.get("CAIRNWORK_BASE_SHA", ""),
        "complete": complete and len(_stages) == 4,
    }
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False
        ) as handle:
            temp_path = handle.name
            json.dump(record, handle, sort_keys=True)
        os.replace(temp_path, Path(directory) / "record.json")
    except OSError:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def pytest_itemcollected(item: Any) -> None:
    global _collected_count
    _collected_count += 1
    _collected_names.update(item.fixturenames)


def pytest_collection_modifyitems(items: list[Any]) -> None:
    _stages["itemcollected"] = {
        "names": sorted(_collected_names),
        "count": _collected_count,
    }
    _record("modifyitems_before", items)


pytest_collection_modifyitems.pytest_impl = {"tryfirst": True}


def pytest_collection_modifyitems_last(items: list[Any]) -> None:
    _record("modifyitems_after", items)


pytest_collection_modifyitems_last.pytest_impl = {
    "trylast": True,
    "specname": "pytest_collection_modifyitems",
}


def pytest_collection_finish(session: Any) -> None:
    _record("finish", session.items, complete=session.testsfailed == 0)
