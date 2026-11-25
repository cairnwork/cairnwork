"""A package whose unchanged ``__init__.py`` rewrites the package search path (#1807).

``from .pickle import loads`` is the package's own module only if the package's search path is
its own directory. When a base ``__init__.py`` along the chain can move it (``__path__``
assignment, ``extend_path``, ``setattr``/``globals()`` writes), the relative import can load a
module from elsewhere, so the exemption is not given and the old comparison applies.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from test_candidate_relative_import_1807 import REFUSED, _verdict

_ELSE = "import os\n__path__ = [os.path.dirname(os.__file__)]\n"
MOVERS = {
    "assign": _ELSE,
    "augmented": "import os\n__path__ += [os.path.dirname(os.__file__)]\n",
    "annotated": "import os\n__path__: list = [os.path.dirname(os.__file__)]\n",
    "extend_path": "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
    "attribute": "import sys\nsys.modules[__name__].__path__ = ['/x']\n",
    "setattr": 'import sys\nsetattr(sys.modules[__name__], "__pa" + "th__", ["/x"])\n',
    "globals": 'globals()["__pa" + "th__"] = ["/x"]\n',
    "dict": 'import sys\nsys.modules[__name__].__dict__["__path__"] = ["/x"]\n',
    "method": "import os\n__path__.insert(0, os.path.dirname(os.__file__))\n",
    "unparseable": "def broken(:\n",
}
QUIET = {
    "empty": "",
    "docstring": '"""Sets nothing; do not assign __path__ or call extend_path."""\n',
    "comment": "# __path__ = []  extend_path\nVERSION = 1\n",
    "all": '__all__ = ["a"]\n__version__ = "1"\n',
}
RUN = "from .pickle import loads\n"


def _files(init: str, where: str = "pkg") -> tuple[dict[str, str], dict[str, str]]:
    base = {f"{where}/__init__.py": init, f"{where}/a.py": "X = 1\n"}
    if where != "pkg":
        base["pkg/__init__.py"] = ""
    return base, {f"{where}/pickle.py": "X = 2\n", f"{where}/v.py": RUN}


@pytest.mark.parametrize("name", list(MOVERS))
def test_a_base_init_that_moves_the_search_path_gets_no_exemption(tmp_path, name):
    assert _verdict(tmp_path, *_files(MOVERS[name])) == REFUSED


@pytest.mark.parametrize("name", list(MOVERS))
def test_the_same_in_a_parent_package_init_gets_no_exemption(tmp_path, name):
    base, new = _files("", "pkg/sub")
    base["pkg/__init__.py"] = MOVERS[name]
    assert _verdict(tmp_path, base, new) == REFUSED


@pytest.mark.parametrize("name", list(QUIET))
def test_an_init_that_only_mentions_the_name_keeps_the_exemption(tmp_path, name):
    base, new = _files(QUIET[name])
    new["pkg/v.py"] = "from .a import X\n"
    new["pkg/a.py"] = "X = 2\n"
    new["pkg/types.py"] = "X = 3\n"
    new["pkg/w.py"] = "from .types import X\n"
    assert _verdict(tmp_path, base, new) is None


def test_the_runtime_really_loads_another_module(tmp_path):
    """The reason: a moved search path makes the relative import load the real ``pickle``."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text(MOVERS["method"])
    (tmp_path / "pkg/pickle.py").write_text("X = 2\n")
    (tmp_path / "pkg/v.py").write_text(RUN)
    code = (
        f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\nimport pkg.v\n"
        "print(sys.modules['pkg.pickle'].__file__)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert out.returncode == 0, out.stderr[-300:]
    assert str(tmp_path) not in out.stdout
