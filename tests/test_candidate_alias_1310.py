import pytest

from cairnwork.candidate_alias import runtime_shadow_added


@pytest.mark.parametrize(
    "snippet",
    [
        "import gzip, shutil\ngzip.open = shutil.copy\ngzip.open('src.py','deep/foo.py')",
        "import gzip\ngzip.open = helper\ngzip.open('deep/foo.py')",
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
    ],
)
def test_runtime_shadow_added_keeps_read_only_openers_false(snippet):
    wrapped = f"import sys\n{snippet}sys.path.insert(0, 'deep')\n"
    assert runtime_shadow_added(None, wrapped.encode()) is False
