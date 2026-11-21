"""Build isolated Python argv prefixes for scripts that run checkout modules."""

import sys
from pathlib import Path

_SRC = str((Path(__file__).resolve().parent.parent / "src").resolve())


def run_module(module: str) -> list[str]:
    """Run *module* as __main__ with this checkout's source available under -I."""
    launcher = (
        "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
        f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)"
    )
    return [sys.executable, "-I", "-B", "-c", launcher, _SRC]
