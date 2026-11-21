"""Portable subprocess compatibility harness for the embedded macOS walker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cairnwork.run_scrub_unlock import UNLOCK_SOURCE


def walk_source(top: Path, limits: tuple[int, int], preamble: str = ""):
    compat = """\
import ctypes, os, stat
class FakeLib:
    class Call:
        def __call__(self, *args): return 0
        argtypes = None
    setattrlist = Call()
ctypes.CDLL = lambda _name: FakeLib()
stat.UF_IMMUTABLE = stat.UF_APPEND = 0
if not hasattr(os, "lchflags"):
    os.lchflags = lambda *_args: 0
real_lstat = os.lstat
class Info:
    def __init__(self, value): self.value, self.st_flags = value, 0
    def __getattr__(self, name): return getattr(self.value, name)
os.lstat = lambda *args, _real_lstat=real_lstat, **kwargs: Info(_real_lstat(*args, **kwargs))
"""
    code = f"{compat}\n{preamble}\nexec({UNLOCK_SOURCE!r})"
    return subprocess.run(
        [sys.executable, "-c", code, str(top), *map(str, limits)],
        capture_output=True,
        text=True,
        check=False,
    )
