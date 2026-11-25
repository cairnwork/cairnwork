"""Execution-time pin for design C' (#1807): whatever a file does to ``__package__``, ``__spec__``
or ``__name__`` before ``from .types import MARK``, the candidate's ``types.py`` is never loaded
as a top-level module (only ever as ``pkg.types``). Real interpreter, one subprocess per variant."""

from __future__ import annotations

import subprocess
import sys

import pytest

VARIANTS = [
    "",
    '__package__ = ""\n',
    "__package__ = None\n",
    '__package__ = "pkg.sub"\n',
    '__package__ = "types"\n',
    '__package__ = "unittest"\n',
    '__name__ = "types.x"\n',
    '__name__ = "x"\n',
    "__spec__ = None\n",
    "__spec__.parent = 'zz'\n",
]


@pytest.mark.parametrize("prefix", VARIANTS, ids=range(len(VARIANTS)))
def test_a_relative_import_never_loads_the_candidate_file_as_a_top_level_name(tmp_path, prefix):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/types.py").write_text("MARK = 'CANDIDATE'\n")
    (tmp_path / "pkg/f.py").write_text(prefix + "from .types import MARK as T\n")
    code = (
        f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n"
        "try:\n    import pkg.f\nexcept BaseException:\n    pass\n"
        "print(sorted(n for n, m in sys.modules.items()"
        " if getattr(m, 'MARK', None) == 'CANDIDATE'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    loaded = set(eval(out.stdout.strip() or "[]"))  # noqa: S307  (our own printed list of names)
    assert loaded <= {"pkg.types"}, (prefix, out.stdout, out.stderr[-200:])
