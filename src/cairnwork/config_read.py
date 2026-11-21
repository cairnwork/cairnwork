"""Read a JSON config file: absent only when lstat says so (#1576); paths shown escaped."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cairnwork.textclean import shown


class ConfigReadError(Exception):
    """The file exists (or may) but cannot be read as JSON; str() is safe to print."""


def read_json_config(path: Path) -> Any:
    """Return the parsed file, or {} when it is certainly absent."""
    where = shown(str(path))
    try:
        path.lstat()
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        if path.is_symlink():  # a dangling link is not an absent file (#1381)
            raise ConfigReadError(f"{where} could not be read: {shown(exc)}") from exc
        return {}
    # truncated, not UTF-8, too deep (#234, #909); looping symlink (#1381)
    except (ValueError, RecursionError) as exc:
        raise ConfigReadError(f"{where} is not valid JSON: {shown(exc)}") from exc
    except OSError as exc:
        raise ConfigReadError(f"{where} could not be read: {shown(exc)}") from exc
