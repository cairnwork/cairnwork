"""The candidate side of a blackbox check (#303): call the declared functions, report their output.

``_RUNNER_SOURCE`` is a small standalone module, materialised by ``install_runner`` into the run's
read-only guard directory (as report_guard's plugin is) and run with ``python -P -m``. It carries no
cairnwork import. It reads the input file the owner wrote next to it (module, function, case ids,
args and kwargs: never the expected answers), puts the checkout's import paths first on
``sys.path``, calls each case and writes, per check, ``{"file": <module __file__, resolved>,
"cases": {id: {"ok": v} or {"error": <exception type>}}}`` to the output path, once.

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

import importlib
import json
import os
import sys


def _plain(value):
    """JSON types only, by exact type: a tuple, float, set or bool subclass is not a list or int."""
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return value
    if kind is list:
        return [_plain(item) for item in value]
    if kind is dict and all(type(key) is str for key in value):
        return {key: _plain(item) for key, item in value.items()}
    raise TypeError(kind.__name__)


def _call(function, case):
    try:
        result = function(*case["args"], **case["kwargs"])
    except BaseException as exc:  # noqa: BLE001 - any failure is the case's outcome
        return {"error": type(exc).__name__}
    try:
        return {"ok": _plain(result)}
    except (TypeError, RecursionError) as exc:
        return {"error": "UNSERIALIZABLE:" + str(exc)}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "blackbox-input.json"), encoding="utf-8") as handle:
        spec = json.load(handle)
    sys.path[:0] = spec["paths"]
    report = {"checks": {}}
    for check in spec["checks"]:
        try:
            module = importlib.import_module(check["module"])
            function = getattr(module, check["function"])
            origin = getattr(module, "__file__", None)
        except BaseException as exc:  # noqa: BLE001
            report["checks"][check["id"]] = {"error": type(exc).__name__}
            continue
        cases = {case["id"]: _call(function, case) for case in check["cases"]}
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
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(path, 0o640)  # not the umask-masked mode: the verifier account reads by group
