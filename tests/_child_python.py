"""Build isolated Python argv prefixes that run checkout modules without an editable install."""

import os
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


def child_env(env=None):
    """Return *env* (default: a copy of os.environ) with the parent's sys.path as PYTHONPATH.

    For children started as ``python script.py`` (no ``-I``) that import cairnwork: pytest's
    ``pythonpath`` only changes the parent's sys.path, so without an editable install the child
    would not find the checkout under test, or would find another checkout's code.
    """
    out = dict(os.environ if env is None else env)
    out["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return out
