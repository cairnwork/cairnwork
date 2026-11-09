"""Install the audit hook before pytest, then run pytest in this same interpreter."""

from __future__ import annotations

import atexit
import json
import os
import sys

SOURCES = (".py", ".pyc")


def module_of(path: str) -> str:
    """The top-level module name a source or cache file loads as (``pkg/__init__.py``: ``pkg``)."""
    parts = path.replace(os.sep, "/").split("/")
    stem = parts[-1].split(".", 1)[0]
    if stem == "__init__" and len(parts) > 1:
        return parts[-2]
    if parts[-2:-1] == ["__pycache__"] and stem == "__init__" and len(parts) > 2:
        return parts[-3]
    return stem


def main() -> int:
    fd_text, root, ini, target, guard, extra = sys.argv[1:]
    fd = int(fd_text)
    pytest_args = json.loads(extra)
    write = os.write

    def record(row: dict) -> None:
        try:
            write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass  # Best effort only; the parent labels this same-process measurement.

    def audit(event: str, args: tuple) -> None:
        if event == "import" and args:
            record({"module": str(args[0]), "origin": args[1] if len(args) > 1 else None})
        elif event == "open" and args and isinstance(args[0], str) and args[0].endswith(SOURCES):
            # the loader reads the file at import time: the path is written before any later
            # candidate code can run (the import event itself rarely carries a filename)
            record({"module": module_of(args[0]), "origin": args[0], "source": "open"})

    def dump_modules() -> None:
        for name, module in tuple(sys.modules.items()):
            try:
                origin = getattr(module, "__file__", None)
                if origin:
                    record({"module": name, "origin": str(origin), "source": "sys.modules"})
            except BaseException:  # noqa: BLE001 - exit dump must remain best effort
                break

    sys.addaudithook(audit)
    atexit.register(dump_modules)
    sys.path.insert(0, guard)
    sys.argv = [
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        ini,
        f"--rootdir={root}",
        f"--confcutdir={root}",
        "-o",
        "addopts=",
        *pytest_args,
        target,
    ]
    import pytest  # noqa: PLC0415 - hook must precede this and all candidate imports

    return int(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
