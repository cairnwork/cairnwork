"""Read the fixture-name record written by cairnwork.fixture_record.

Returns the set of fixture names the base requests, or None meaning "use the #916 rule
exactly as on main". Any doubt returns None: this reader never yields fewer names than main.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any

_OK_EXIT_CODES = (0, 5)  # pytest ok / no tests collected
_MAX_BYTES = 4 * 1024 * 1024
_STAGES = frozenset({"itemcollected", "modifyitems_before", "modifyitems_after", "finish"})


def collected_names(
    run_dir: Path, *, exit_code: object, settings_digest: str, base_sha: str
) -> frozenset[str] | None:
    if type(exit_code) is not int or exit_code not in _OK_EXIT_CODES:
        return None
    if not settings_digest or not base_sha:
        return None
    record = _load(run_dir / "record.json")
    if not _valid(record, settings_digest, base_sha):
        return None
    return frozenset(record["names"])


def _load(path: Path) -> Any:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            return None
        return json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - any read or parse fault means no record
        return None


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid(record: Any, digest: str, base_sha: str) -> bool:
    return (
        isinstance(record, dict)
        and type(record.get("recorder_version")) is int
        and record["recorder_version"] == 2
        and record.get("complete") is True
        and record.get("settings_digest") == digest
        and record.get("base_sha") == base_sha
        and _stages_ok(record.get("stages"))
        and _names_ok(record)
        and _is_str_list(record.get("sys_path"))
        and _is_str_list(record.get("sys_path_finish"))
        and _plugins_ok(record.get("plugin_modules"))
    )


def _stages_ok(stages: Any) -> bool:
    if not isinstance(stages, dict) or set(stages) != _STAGES:
        return False
    return all(
        isinstance(stage, dict)
        and set(stage) == {"names", "count"}
        and _is_str_list(stage["names"])
        and type(stage["count"]) is int
        for stage in stages.values()
    )


def _names_ok(record: dict[str, Any]) -> bool:
    names = record.get("names")
    if not _is_str_list(names):
        return False
    union = {name for stage in record["stages"].values() for name in stage["names"]}
    return names == sorted(union)


def _plugins_ok(plugins: Any) -> bool:
    if not isinstance(plugins, list):
        return False
    return all(
        isinstance(plugin, dict)
        and set(plugin) == {"name", "file"}
        and isinstance(plugin["name"], str)
        and (plugin["file"] is None or isinstance(plugin["file"], str))
        for plugin in plugins
    )
