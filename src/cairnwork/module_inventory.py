"""The module names the verifier's Python can import, for the protected-input name rules.

A verifier Python of 3.10 or newer runs ``_MODULE_NAMES`` as before, then old_python_probe.py
in its 3.10+ part (#811, #841): the probe refuses import hooks it cannot enumerate, or standard
ones with other loaders or suffixes, and adds the meta_path finder names and namespace directories
the three listings miss. An older one (3.6 to 3.9, #790) lacks ``sys.stdlib_module_names`` and
``packages_distributions``, so it runs the whole of old_python_probe.py instead, and the
standard-library names of Cairnwork's own Python are added. The version is asked first, by
itself; any failure, any other version and any output that is not exactly the expected shape raise
VerifierInventoryError (an environment reason): the task is refused before binding, as before, but
with that code instead of a bare PROTECTED_VERIFIER_INPUT.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from cairnwork.pytest_config import PytestConfigError

_VERSION = "import sys; print(sys.version_info[0], sys.version_info[1])"
_VERSION_LINE = re.compile(r"(\d{1,2}) (\d{1,3})\n?")
_MODULE_NAMES = (
    "import json, pkgutil, sys; from importlib.metadata import packages_distributions; "
    "print(json.dumps(sorted(sys.stdlib_module_names | "
    "{module.name for module in pkgutil.iter_modules()} | "
    "set(packages_distributions()))))"
)
_OLD_PROBE = Path(__file__).with_name("old_python_probe.py")
_PART_310 = 'PART = "3.10+"\n'


class VerifierInventoryError(PytestConfigError):
    """The verifier's Python cannot list its modules: an environment reason, not a verdict on the
    candidate (#790). The task is still refused before binding, with this code."""

    code = "VERIFIER_PYTHON_INVENTORY_UNAVAILABLE"


def importable_names(python: str) -> set[str]:
    try:
        major, minor = _version(python)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise VerifierInventoryError(f"the version question failed: {_why(exc)}") from exc
    if (major, minor) < (3, 6):
        raise VerifierInventoryError(f"Python {major}.{minor} is not supported")
    modern = (major, minor) >= (3, 10)
    if modern:
        names = _names(json.loads(_run(python, _MODULE_NAMES)))
    else:
        names = set(sys.stdlib_module_names)
    try:
        probe = (_PART_310 if modern else "") + _OLD_PROBE.read_text(encoding="utf-8")
        names |= _old_names(_run(python, probe), [major, minor])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = f"Python {major}.{minor} cannot list its modules: {_why(exc)}"
        raise VerifierInventoryError(reason) from exc
    return {name.casefold() for name in names} | {"_cairnwork_report_guard"}


def _why(exc: Exception) -> str:
    """The kind of failure and, for a probe that exited, its last stderr line (printable ASCII,
    cut, no leading ``::`` or ``##[`` that a CI log would read as a command)."""
    text = str(getattr(exc, "stderr", "") or "").strip().splitlines()
    detail = re.sub(r"[^ -~]", "?", text[-1] if text else str(exc))[:160]
    return f"{type(exc).__name__}: {detail}".replace("::", ": :").replace("##[", "# #[")


def _run(python: str, code: str) -> str:
    return subprocess.run(  # noqa: S603 - the configured verifier Python, fixed code
        [python, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/",
    ).stdout


def _version(python: str) -> tuple[int, int]:
    match = _VERSION_LINE.fullmatch(_run(python, _VERSION))
    if match is None:
        raise ValueError("verifier Python version is unreadable")
    return int(match[1]), int(match[2])


def _names(names: object) -> set[str]:
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("verifier module inventory is invalid")
    return set(names)


def _old_names(stdout: str, version: list[int]) -> set[str]:
    """Exactly one JSON line ``{"v": 1, "version": [major, minor], "names": [str, ...]}``."""
    if stdout.count("\n") != 1 or not stdout.endswith("\n"):
        raise ValueError("verifier module inventory is not one line")
    data = json.loads(stdout)
    if not isinstance(data, dict) or set(data) != {"v", "version", "names"} or data["v"] != 1:
        raise ValueError("verifier module inventory is invalid")
    found = data["version"]
    if not isinstance(found, list) or [type(part) for part in found] != [int, int]:
        raise ValueError("verifier module inventory is invalid")
    if found != version:
        raise ValueError("verifier Python changed between probes")
    return _names(data["names"])
