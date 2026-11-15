"""Regression cases for conservative opener alias handling (#1225)."""

from __future__ import annotations

import pytest

from cairnwork.candidate_alias import runtime_shadow_added


@pytest.mark.parametrize(
    "snippet",
    [
        "from pathlib import Path\nimport gzip as p\np = Path('m.py')\np.open(m)",
        "from pathlib import Path\nimport io as p\np = Path('m.py')\np.open(m, encoding='utf-8')",
        "from pathlib import Path\nimport tarfile as p\np = Path('m.py')\np.open(m, buffering=-1)",
        "from pathlib import Path\nimport codecs as p\np = Path('m.py')\np.open(f'w')",
        "from pathlib import Path\nimport bz2 as p\np = Path('m.py')\np.open('w' + '')",
        "from pathlib import Path\nimport lzma as p\np = Path('m.py')\np.open(str(m))",
        "import builtins as p\ndef f(p, m):\n    p.open(m)",
        "import io as p\nfor p in paths:\n    p.open(m)",
        "import gzip as p\nwith x as p:\n    p.open(m)",
        "from pathlib import Path\nif False:\n    import io as p\np = Path('m.py')\np.open(m)",
        (
            "from pathlib import Path\ntry:\n    import io as p\n"
            "except ImportError:\n    pass\np = Path('m.py')\np.open(m)"
        ),
        "from pathlib import Path\nimport codecs as shelve\nshelve = Path('m.py')\nshelve.open(m)",
        "from pathlib import Path\nimport gzip as zipfile\nzipfile = Path('m.py')\nzipfile.open(m)",
        "from pathlib import Path\nimport io as dbm\ndbm = Path('m.py')\ndbm.open(m)",
        "from pathlib import Path\nFileIO = Path('m.py').open\nFileIO('w')",
        "from pathlib import Path\nFileIO = Path('m.py').write_text\nFileIO('x')",
        "from pathlib import Path\nfrom io import FileIO\nFileIO = Path('m.py').open\nFileIO('w')",
        "from dbm import open as FileIO\nFileIO('m.py', 'c')",
        "from shelve import open as FileIO\nFileIO('w')",
        "import io as g\nif c:\n    import shelve as g\ng.open('m.py')",
    ],
    ids=[f"case_{index}" for index in range(20)],
)
def test_rebound_module_alias_write_is_flagged(snippet):
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, source) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "import gzip as g\ng.open(path)",
        "import io as g\ng.open(path, 'rb')",
        "import codecs as c\nc.open(path, 'r', 'utf-8')",
        "import gzip as g\ndef f():\n    return g.open(path)",
        "import tarfile as t\nt.open(path)",
        "import gzip\ngzip.open(path)",
        "from io import FileIO as F\nF('f')",
        "from io import FileIO\nFileIO(path, 'r')",
        "import shelve as s\ns.open(path, 'r')",
    ],
    ids=[f"case_{index}" for index in range(9)],
)
def test_unrebound_module_alias_read_is_allowed(snippet):
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, source) is False
