"""The candidate side of a blackbox check (#303): call the declared functions, report their output.

``_RUNNER_SOURCE`` is a small standalone module, materialised by ``install_runner`` into the run's
read-only guard directory (as report_guard's plugin is) and run with ``python -P -m``. It carries no
cairnwork import. It reads the input file the owner wrote next to it (module, function or class,
case ids, args and kwargs, method names: never the expected outcomes or tolerances), puts the
checkout's import paths first on ``sys.path``, calls each case and writes, per check,
``{"file": <module __file__, resolved>, "cases": {id: <outcome>}}`` to the output path, once. An
outcome is ``{"ok": value}`` (floats tagged, blackbox_values.py) or ``{"error": <type name>,
"message": <str(exception), cut>}``; a class case is ``{"steps": [outcome, ...]}``, or
``{"init": outcome}`` when the constructor raised.

It never says pass or fail: the owner compares the output with answers it alone holds
(blackbox.py). The candidate shares this interpreter and can forge the output; that is why the
answers stay out of the run, not why the output would be trusted.
"""

from __future__ import annotations

import os
from pathlib import Path

MODULE = "_cairnwork_blackbox_runner"
INPUT_NAME = "blackbox-input.json"
OUTPUT_NAME = "blackbox-out.json"

_RUNNER_SOURCE = '''\
"""Materialised by cairnwork.blackbox_runner into each blackbox run; edit it there."""

import builtins
import importlib
import json
import os
import sys

FLOAT = "$float"
MAX_MESSAGE = 1001


def _plain(value):
    """JSON types only, by exact type: a tuple, set or subclass is not a list, int, str or float."""
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return value
    if kind is float:
        return {FLOAT: float.hex(value)}
    if kind is list:
        return [_plain(item) for item in value]
    if kind is dict and all(type(key) is str for key in value) and FLOAT not in value:
        return {key: _plain(item) for key, item in value.items()}
    raise TypeError(kind.__name__)


def _value(item):
    """The owner's input: a dict holding the float key is always a float (the owner refuses it)."""
    if type(item) is dict:
        if FLOAT in item:
            return float.fromhex(item[FLOAT])
        return {key: _value(value) for key, value in item.items()}
    if type(item) is list:
        return [_value(value) for value in item]
    return item


def _name(kind):
    """A builtin by its bare name only when it is the builtin; otherwise module.qualname."""
    try:
        module, name = kind.__module__, kind.__qualname__
        if type(module) is not str or type(name) is not str:
            return "?"
        if module == "builtins" and getattr(builtins, name, None) is kind:
            return name
        return module + "." + name
    except BaseException:  # noqa: BLE001
        return "?"


def _error(exc):
    report = {"error": _name(type(exc))}
    try:
        message = str(exc)
    except BaseException:  # noqa: BLE001
        message = None
    if type(message) is str:
        report["message"] = message[:MAX_MESSAGE]
    return report


def _call(function, given):
    args, kwargs = _value(given["args"]), _value(given["kwargs"])
    try:
        result = function(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - any failure is the case's outcome
        return _error(exc)
    try:
        return {"ok": _plain(result)}
    except (TypeError, RecursionError) as exc:
        return {"error": "UNSERIALIZABLE:" + str(exc)}


def _method(instance, name):
    return lambda *args, **kwargs: getattr(instance, name)(*args, **kwargs)


def _calls(cls, case):
    """A fresh instance, then each call in order; a call that raises does not stop the next."""
    init = case["init"]
    try:
        instance = cls(*_value(init["args"]), **_value(init["kwargs"]))
    except BaseException as exc:  # noqa: BLE001
        return {"init": _error(exc)}
    return {"steps": [_call(_method(instance, call["method"]), call) for call in case["calls"]]}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "blackbox-input.json"), encoding="utf-8") as handle:
        spec = json.load(handle)
    sys.path[:0] = spec["paths"]
    report = {"checks": {}}
    for check in spec["checks"]:
        try:
            module = importlib.import_module(check["module"])
            target = getattr(module, check.get("function") or check["class"])
            origin = getattr(module, "__file__", None)
        except BaseException as exc:  # noqa: BLE001
            report["checks"][check["id"]] = {"error": _name(type(exc))}
            continue
        run = _call if "function" in check else _calls
        cases = {case["id"]: run(target, case) for case in check["cases"]}
        origin = os.path.realpath(origin) if isinstance(origin, str) else None
        report["checks"][check["id"]] = {"file": origin, "cases": cases}
    data = json.dumps(report).encode()
    fd = os.open(spec["out"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(spec["out"], 0o644)  # the owner reads it (a different user under the account)


if __name__ == "__main__":
    main()
'''


def install_runner(directory: Path, spec: bytes) -> None:
    """Write the runner and its input (no expected answers) into directory, which must be a sandbox
    read root, on the run's PYTHONPATH, and not writable by the candidate."""
    for name, data in ((f"{MODULE}.py", _RUNNER_SOURCE.encode()), (INPUT_NAME, spec)):
        path = directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        # Readable by other, not only the group: written after _prepare_run's chmod stripped the
        # guard's setgid, a file takes the owner's primary group on Linux, not cairnwork
        # (VerifierAccount.grant_read). Neither file holds an answer; the guard stays owner-written.
        os.chmod(path, 0o644)
