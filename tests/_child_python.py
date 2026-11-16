"""Build isolated Python argv prefixes that run checkout modules without an editable install."""

import sys
from pathlib import Path

_SRC = str((Path(__file__).resolve().parent.parent / "src").resolve())


def run_module(module):
    """Return argv that runs *module* as __main__ with checkout src available under -I."""
    launcher = (
        "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
        f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)"
    )
    return [sys.executable, "-I", "-B", "-c", launcher, _SRC]
