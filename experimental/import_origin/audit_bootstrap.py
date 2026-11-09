"""Install the audit hook before pytest, then run pytest in this same interpreter."""

from __future__ import annotations

import atexit
import json
import os
import sys

SOURCES = (".py", ".pyc")


def main() -> int:
    fd_text, root, ini, target, guard, extra = sys.argv[1:]
    fd = int(fd_text)
    pytest_args = json.loads(extra)
    write = os.write
    dumps = json.dumps
    pending_module: str | None = None

    def record(row: dict) -> None:
        try:
            write(fd, (dumps(row, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass  # Best effort only; the parent labels this same-process measurement.

    def audit(event: str, args: tuple) -> None:
        nonlocal pending_module
        if event == "import" and args:
            pending_module = str(args[0])
            record({"module": pending_module, "origin": args[1] if len(args) > 1 else None})
        elif event == "open" and args and isinstance(args[0], str) and args[0].endswith(SOURCES):
            # The import event carries the dotted name; the following open carries its file.
            if pending_module:
                record({"module": pending_module, "origin": args[0], "source": "open"})

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
