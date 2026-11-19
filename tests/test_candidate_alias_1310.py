import pytest

from cairnwork.candidate_alias import runtime_shadow_added


@pytest.mark.parametrize(
    "snippet",
    [
        "import gzip, shutil\ngzip.open = shutil.copy\ngzip.open('src.py','deep/foo.py')",
        "import gzip\ngzip.open = helper\ngzip.open('deep/foo.py')",
        "import gzip\n(gzip.open,) = (handler,)\ngzip.open('deep/foo.py')",
        "import shutil\n(w := shutil.copy)\nw('src.py','deep/foo.py')",
        "import shutil\n(w,) = (shutil.copy,)\nw('src.py','deep/foo.py')",
        "import shutil\nw: object = shutil.copy\nw('src.py','deep/foo.py')",
        "import gzip as g\ng.open = helper\ng.open('deep/foo.py')",
        "import shutil\nw = shutil.copy\nw('src.py','deep/foo.py')",
        "import io\nfrom pathlib import Path\nio = Path('deep/foo.py')\nio.open(m)",
        "from io import open as o\nfrom os import open as o\no('deep/foo.py', flags=577)",
    ],
)
def test_runtime_shadow_added_detects_writer_aliases(snippet):
    wrapped = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "from pathlib import Path\nprint('x', file=Path('deep/foo.py').open(m))",
        "print('x', file=target.open(m))",
    ],
)
def test_runtime_shadow_added_detects_print_file_openers(snippet):
    wrapped = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "import gzip\nx = gzip.open('f')\n",
        "from io import open as o\no('f')\n",
        "import io\nio.open('f','r')\n",
        "c.write = handler\n",
        "import gzip\nclass C: ...\ngzip = C()\ngzip.open = handler\n",
        "import gzip\nclass C: ...\ngzip: object = C()\ngzip.open = handler\n",
        "import gzip\nclass C: ...\ngzip += 1\ngzip.open = handler\n",
        "import gzip\nclass C: ...\nfor gzip in C(): pass\ngzip.open = handler\n",
        "import gzip\nclass C: ...\nwith C() as gzip: pass\ngzip.open = handler\n",
        "import gzip\nclass C: ...\ndef gzip(): pass\ngzip.open = handler\n",
        "import gzip\nclass C: ...\nclass gzip: pass\ngzip.open = handler\n",
        "import gzip\nclass C: ...\nimport zlib as gzip\ngzip.open = handler\n",
        "import gzip\nclass C: ...\n(gzip := C())\ngzip.open = handler\n",
        "import shutil\nimport shutil\nshutil.which('git')\n",
        "import io\nbuf = io.BytesIO()\nw = buf.write\nw(b'x')\n",
        "class C: ...\ngzip = C()\ngzip.open = handler\n",
        "c.open = handler\nc.open('f')\n",
    ],
)
def test_runtime_shadow_added_keeps_read_only_openers_false(snippet):
    wrapped = f"import sys\n{snippet}sys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is False


# Pinned by the independent review of b8703f10 (must refuse / must allow), one shape each.
_MUST_REFUSE = [
    "w = open\nw('deep/foo.py','w').close()",
    "from os import open as o\nw = o\nw('deep/foo.py', 577)",
    "from gzip import open as g\nw = g\nw('deep/foo.py','wb').close()",
    "from tempfile import NamedTemporaryFile as N\nw = N\nw(dir='deep', delete=False)",
    "import tempfile\nw = tempfile.NamedTemporaryFile\nw(dir='deep', delete=False)",
    "import io\nio.FileIO = helper\nio.FileIO('deep/foo.py','w')",
    "import io, shutil\nio.FileIO = shutil.copy\nio.FileIO('src.py','deep/foo.py')",
    "w = FileIO\nw('deep/foo.py','w')",
    "import os.path\nw = os.link\nw('src.py','deep/foo.py')",
]
_MUST_ALLOW = [
    "import io\nr = io.open\nr('f','r')\n",
    "import os\nwrite = os.write\nwrite(1, b'x')\n",
    "import sys\nemit = sys.stdout.write\n",
    "from pathlib import Path\norig = Path.write_text\n",
]


@pytest.mark.parametrize("snippet", _MUST_REFUSE)
def test_runtime_shadow_added_refuses_rebound_writers(snippet):
    wrapped = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is True


@pytest.mark.parametrize("snippet", _MUST_ALLOW)
def test_runtime_shadow_added_allows_reader_and_pipe_references(snippet):
    wrapped = f"import sys\n{snippet}\nsys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is False
