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
        "import gzip\ngzip.open(path)",
        "from io import FileIO as F\nF('f')",
    ],
)
def test_unrebound_module_alias_read_is_allowed(snippet):
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, source) is False


@pytest.mark.parametrize(
    "snippet",
    [
        "from pathlib import Path\nimport io as X\nX = Path('deep/foo.py')\nX.open(m)",
        (
            "from pathlib import Path\nif False:\n    import io as X\n"
            "X = Path('deep/foo.py')\nX.open(m)"
        ),
        "import gzip as X\ndef f(X):\n    X.open(m)",
        "import builtins as X\nfor X in paths:\n    X.open(m, encoding='utf-8')",
        "import codecs as X\nwith ctx() as X:\n    X.open(get_mode())",
        (
            "from pathlib import Path\nimport tarfile as X\n"
            "(X := Path('deep/foo.py'))\nX.open(m, buffering=1)"
        ),
        "import bz2 as X\nfrom helpers import target as X\nX.open(mode)",
        (
            "from pathlib import Path\nimport lzma as X\n"
            "X: Path = Path('deep/foo.py')\nX.open(m, newline='')"
        ),
        "import shutil\nFileIO = shutil.copy\nFileIO('src.py', 'deep/foo.py')",
        "import shutil\nfrom io import FileIO\ndef f(FileIO):\n    FileIO('src.py', 'deep/foo.py')",
        "import io as X\nimport gzip as X\nX.open(m)",
        "import shelve\nshelve.open('f')",
        "import shelve\nshelve.open('f', 'c')",
        "import dbm\ndbm.open('f', 'c')",
        "import dbm\ndbm.open('f', 'n')",
        "import dbm\ndbm.open('f', 'w')",
        "import zipfile\nzipfile.ZipFile('f', 'a')",
        "import zipfile\nzipfile.ZipFile('f', 'x')",
        # Review r3: legacy unknown-receiver behavior for renamed modules and FileIO.
        "import io as X\nfrom helpers import *\nX.open(m)",
        "import codecs as X\nfrom helpers import *\nX.open(m, encoding='utf-8')",
        "import tarfile as X\nfrom helpers import *\nX.open(get_mode())",
        "from io import FileIO\nfrom helpers import *\nFileIO('src.py', 'deep/foo.py')",
        "from io import FileIO as FileIO\nfrom helpers import *\nFileIO('src.py', 'deep/foo.py')",
        "from pathlib import Path\nimport gzip as X\nX.open = Path('deep/foo.py').open\nX.open(m)",
        (
            "import functools\nimport io as X\nX.open = functools.partial(open, "
            "'deep/foo.py')\nX.open(m)"
        ),
        (
            "import functools\nimport gzip\nimport gzip as X\ngzip.open = "
            "functools.partial(open, 'deep/foo.py')\nX.open(m)"
        ),
        (
            "import io, shutil\nio.FileIO = shutil.copy\nfrom io import FileIO\n"
            "FileIO('src.py', 'deep/foo.py')"
        ),
        "import hp\nimport gzip as X\nX.open(m)",
        "import hp\nfrom io import FileIO\nFileIO('src.py', 'deep/foo.py')",
        "import io as X\nX.open(0)",
        "from io import FileIO\nFileIO('f', 'rb')",
        # Review r3: reads whose apparent module identity can be patched externally.
        "import gzip as g\ng.open(path)",
        "import io as g\ng.open(path, 'rb')",
        "import codecs as c\nc.open(path, 'r', 'utf-8')",
        "import gzip as g\ndef f():\n    return g.open(path)",
        "import tarfile as t\nt.open(path)",
        "from io import FileIO\nFileIO(path, 'r')",
        "import shelve as s\ns.open(path, 'r')",
        "import io as X\nX.open(p)",
        "import gzip as X\nX.open(path, 'rb')",
        "from io import FileIO\nFileIO('f')",
        "import io\nio.open('w', 'r')",
    ],
)
def test_review_r3_must_block_inputs_are_rejected(snippet):
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n".encode()
    assert runtime_shadow_added(None, source) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "import io as X\nX.open('f', 'r')",
        "from io import FileIO as F\nF('f', 'r')",
        "import io\nio.open(p, 'r')",
        "import os\nos.fdopen(3)",
        "import os\nos.fdopen(3, 'rb')",
        "import shelve\nshelve.open('f', 'r')",
        "import dbm\ndbm.open('f', 'r')",
        # Existing must-pass cases from test_runtime_shadow_added_review_cases.
        "open('w')",
        "open('f')",
        "import gzip\ngzip.open('data/raw.gz')",
        "import gzip\ngzip.open(path, 'rb')",
        "Path('f').open('r', 1)",
        "from tarfile import open as t\nt('f.tgz','r:gz')",
        "import shelve\nshelve.open('f', flag='r')",
        "from io import FileIO as F\nF('f')",
        "from zipfile import ZipFile as Z\nZ('f','r')",
        "from webbrowser import open as emit\nemit('u')",
        # Existing read and unrelated-call cases from the review's named test.
        "from bz2 import BZ2File as e\ne('f','rb')",
        "import bz2\nbz2.BZ2File('f')",
        "import bz2\nbz2.BZ2File('f', mode='r')",
        "import lzma\nlzma.LZMAFile('f','rb')",
        "import gzip\ngzip.GzipFile('f','rb')",
        "from io import FileIO as F\nF('f','r')",
        "from zipfile import ZipFile as Z\nZ('f')",
        "from zipfile import ZipFile as Z\nZ('f','r')",
        "from json import dumps as emit\nemit('f')",
        "from webbrowser import open as emit\nemit('u')",
        "def emit(a,b): return a\nemit('f','w')",
        "from .bz2 import BZ2File as e\ne('f','w')",
        "from bz2 import BZ2File as e",
        "import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__),'lib'))",
        "from tarfile import open as t\nt('f.tgz','r:gz')",
    ],
)
def test_review_r3_must_pass_inputs_remain_allowed(snippet):
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n".encode()
    assert runtime_shadow_added(None, source) is False


def test_dead_import_ambiguity_for_non_os_receiver_fails_closed():
    snippet = (
        "if False:\n    import io as reader\nimport gzip as reader\nreader.open('file.txt', 'rb')"
    )
    source = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n".encode()
    assert runtime_shadow_added(None, source) is True
