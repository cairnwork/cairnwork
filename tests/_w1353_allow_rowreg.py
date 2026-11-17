"""Runtime registry for immutable witness rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

_issued: list[Any] = []
_issued_hashes: dict[int, str] = {}


def freeze(obj):
    if isinstance(obj, dict):
        return MappingProxyType({key: freeze(value) for key, value in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(freeze(value) for value in obj)
    return obj


def thaw(obj):
    if isinstance(obj, Mapping):
        return {key: thaw(value) for key, value in obj.items()}
    if isinstance(obj, tuple):
        return [thaw(value) for value in obj]
    return obj


def _digest(obj):
    encoded = json.dumps(thaw(obj), sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(encoded.encode()).hexdigest()


def issue(mapping):
    frozen = freeze(mapping)
    _issued.append(frozen)
    _issued_hashes[id(frozen)] = _digest(frozen)
    return frozen


def _hash_matches(row, expected):
    return _digest(row) == expected


def verify_rows(rows):
    problems = []
    for index, row in enumerate(rows):
        expected = _issued_hashes.get(id(row))
        if not any(row is issued for issued in _issued):
            problems.append(f"row {index} is not registered")
        elif expected is None or not _hash_matches(row, expected):
            problems.append(f"row {index} changed after issue")
    return problems
