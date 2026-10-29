"""A throwaway verifier interpreter for the namespace shadow tests (#384, #390).

``make_site`` creates a venv (no pip, no network) and writes stand-in packages into its
site-packages, each with ``answer() == 42`` in the installed copy:

- ``regpkg``: a regular package; ``nsco``: a PEP 420 namespace with a namespace ``somelib``
  (``impl``, ``oracle``) and a regular ``reglib`` (``core``);
- ``pkuco``: pkgutil-style (``extend_path``); ``legacyco``: setuptools' legacy ``-nspkg.pth``;
- ``mylib`` and ``ownproj``: regular packages outside sys.path, found only by a ``sys.meta_path``
  finder appended from a ``.pth`` file, as a setuptools >= 64 editable install does (#390);
  ``metans``: a namespace that only that finder supplies; ``sidefx``: a finder-backed package
  whose import writes a marker file (the probe must never import it).

With ``pytest_from`` (a site-packages directory) pytest and its dependencies are copied in, for
end-to-end runs of the real verifier: the sandbox reads only the interpreter's own prefixes, so
the stand-ins live inside the venv too.
"""

import shutil
import subprocess
import sys
from pathlib import Path

LEGACY_PTH = (
    "import sys, types, os;p = os.path.join(sys._getframe(1).f_locals['sitedir'], 'legacyco');"
    "import importlib.util, importlib.machinery;"
    "m = sys.modules.setdefault('legacyco', importlib.util.module_from_spec("
    "importlib.machinery.PathFinder.find_spec('legacyco', [os.path.dirname(p)])));"
    "mp = m.__dict__.setdefault('__path__', []);(p not in mp) and mp.append(p)\n"
)
FINDER = """\
import importlib.machinery, importlib.util, os, sys

_HOME = {home!r}


class _EditableFinder:
    @classmethod
    def find_spec(cls, name, path=None, target=None):
        top = name.split(".")[0]
        if top == "metans" and name == top:
            spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
            spec.submodule_search_locations = [os.path.join(_HOME, "metans")]
            return spec
        if top not in ("mylib", "ownproj", "sidefx"):
            return None
        where = [_HOME] if name == top else path
        return importlib.machinery.PathFinder.find_spec(name, where)


sys.meta_path.append(_EditableFinder)
"""


_PYTEST = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "py.py")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def site_dir(python: str) -> Path:
    code = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    return Path(subprocess.check_output([python, "-I", "-c", code], text=True).strip())


def make_site(root: Path, pytest_from: str | None = None) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")], check=True)
    python = str(root / "venv" / "bin" / "python")
    site = site_dir(python)
    editable = root / "venv" / "editable"
    answer = "def answer():\n    return 42\n"
    oracle = "def reference(x):\n    return x * x\n"
    for rel, text in {
        "regpkg/__init__.py": "",
        "regpkg/calc.py": answer,
        "nsco/somelib/impl.py": answer,
        "nsco/somelib/oracle.py": oracle,
        "nsco/reglib/__init__.py": "",
        "nsco/reglib/core.py": answer,
        "pkuco/__init__.py": "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n",
        "pkuco/somelib/__init__.py": "",
        "pkuco/somelib/impl.py": answer,
        "legacyco/somelib/impl.py": answer,
        "legacyco-nspkg.pth": LEGACY_PTH,
        "_cw_editable_finder.py": FINDER.format(home=str(editable)),
        "_cw_editable.pth": "import _cw_editable_finder\n",
    }.items():
        _write(site / rel, text)
    _write(editable / "mylib" / "__init__.py", "")
    _write(editable / "mylib" / "oracle.py", oracle)
    _write(editable / "ownproj" / "__init__.py", "")
    _write(editable / "metans" / "part.py", answer)
    marker = root / "sidefx-imported"
    _write(editable / "sidefx" / "__init__.py", f"open({str(marker)!r}, 'w').close()\n")
    for entry in Path(pytest_from).iterdir() if pytest_from else ():
        if entry.name.split("-")[0] in _PYTEST or entry.name in _PYTEST:
            copy = shutil.copytree if entry.is_dir() else shutil.copy2
            copy(entry, site / entry.name)
    return python
