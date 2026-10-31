"""bytecode_roots.redirected_bytecode: bytecode is known by its content, and a spelling the walk
cannot check fails the run (Issue #276 family; owner mode, hand-built run trees, no sandbox).

Code in the run's process decides the name cached bytecode gets: ``cache_from_source`` appends
``importlib.machinery.BYTECODE_SUFFIXES[0]`` to ``sys.implementation.cache_tag``, and pytest's
rewriter appends ``PYC_TAIL``. So a file that starts with this Python's magic number is bytecode
whatever it is called. The walk keeps the first spelling that reaches each directory; when the
kernel refuses that spelling as too long, the run fails rather than missing a source that a
shorter spelling reaches.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork.bytecode_roots import redirected_bytecode

MAGIC = importlib.util.MAGIC_NUMBER
TAG = "cpython-312"
LIMIT = os.pathconf("/", "PC_PATH_MAX")  # macOS 1024, Linux 4096


@pytest.fixture
def run(tmp_path):
    work = tmp_path / "r1-v0.work"
    checkout = work / "r1-v0"
    run_dir = tmp_path / "r1-v0.abc" / "run"
    for directory in (checkout / "tests", checkout / "src", checkout / ".git", run_dir / "home"):
        directory.mkdir(parents=True)
    (run_dir / "tmp" / "pycache").mkdir(parents=True)
    (checkout / "tests" / "helper.py").write_text("OK = False\n")
    (checkout / "src" / "a.py").write_text("x = 1\n")
    return checkout, run_dir


def scan(checkout, run_dir):
    return redirected_bytecode(checkout, (checkout.parent, run_dir))


PLANT = """
import importlib.machinery, importlib.util, marshal, os, sys
helper = sys.argv[1]
importlib.machinery.BYTECODE_SUFFIXES[0] = ".dat"
sys.pycache_prefix = sys.argv[2]
st = os.stat(helper)
data = (importlib.util.MAGIC_NUMBER + bytes(4) + int(st.st_mtime).to_bytes(4, "little")
        + st.st_size.to_bytes(4, "little") + marshal.dumps(compile("OK = True", helper, "exec")))
cached = importlib.util.cache_from_source(helper)
os.makedirs(os.path.dirname(cached), exist_ok=True)
with open(cached, "wb") as out:
    out.write(data)
sys.path.insert(0, os.path.dirname(helper))
import helper as loaded
print(cached, loaded.OK)
"""


def test_bytecode_under_another_suffix_is_loaded_and_fails_the_run(run):
    checkout, run_dir = run
    helper = os.path.realpath(checkout / "tests" / "helper.py")
    done = subprocess.run(
        [sys.executable, "-I", "-c", PLANT, helper, str(run_dir / "home" / "alt")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    cached, loaded = done.stdout.split()
    assert cached.endswith(".dat") and loaded == "True"  # Python read it under that name
    (reason,) = scan(checkout, run_dir)
    assert reason.endswith(".dat (bytecode not named .pyc)"), reason


@pytest.mark.parametrize(
    "where",
    [
        "home/alt/helper.cpython-312.dat",  # BYTECODE_SUFFIXES[0] changed
        "tmp/pycache/tests/test_z.dat",  # pytest's PYC_TAIL changed
        "home/alt/tests/helper.x/y.dat",  # a cache_tag holding a slash
        "home/alt/helper",  # no suffix at all
    ],
)
def test_a_file_starting_with_the_magic_number_fails_wherever_it_is(run, where):
    checkout, run_dir = run
    planted = run_dir / where
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(MAGIC + bytes(12) + b"c")
    assert scan(checkout, run_dir) == (f"run/{where} (bytecode not named .pyc)",)


def test_a_committed_data_file_starting_with_the_magic_number_passes(run):
    """In the checkout the comparison shows such a file is the candidate's own committed
    content; its .git is not compared, so there the content still counts."""
    checkout, run_dir = run
    (checkout / "tests" / "data").mkdir()
    (checkout / "tests" / "data" / "header.bin").write_bytes(MAGIC + b"fixture record\n")
    assert scan(checkout, run_dir) == ()
    (checkout / ".git" / "header.bin").write_bytes(MAGIC + b"fixture record\n")
    assert scan(checkout, run_dir) == (".git/header.bin (bytecode not named .pyc)",)


def test_files_beside_the_checkout_are_not_committed(run):
    """The work directory around the checkout is no part of the candidate's commit: there a
    magic-number file and a pytest-rewritten pyc both fail the run."""
    checkout, run_dir = run
    other = checkout.parent / "other"
    other.mkdir()
    (other / "x.bin").write_bytes(MAGIC + b"fixture record\n")
    (other / f"test_a.{TAG}-pytest-8.0.pyc").write_bytes(MAGIC + bytes(12))
    assert scan(checkout, run_dir) == (
        f"r1-v0.work/other/test_a.{TAG}-pytest-8.0.pyc (pytest-rewritten bytecode)",
        "r1-v0.work/other/x.bin (bytecode not named .pyc)",
    )


def test_other_files_closed_files_and_fifos_pass(run):
    checkout, run_dir = run
    home = run_dir / "home"
    (home / "text.dat").write_text("x = 1\n")
    (home / "empty").write_bytes(b"")
    (home / "short").write_bytes(MAGIC[:2])
    (home / "other.dat").write_bytes(bytes([MAGIC[0] ^ 1, MAGIC[1]]) + b"\r\n" + bytes(12))
    (home / "closed").write_text("x = 1\n")
    (home / "closed").chmod(0)
    os.mkfifo(home / "fifo")  # never opened: reading it would block
    try:
        assert scan(checkout, run_dir) == ()
    finally:
        (home / "closed").chmod(0o600)


def nested(top: Path, chars: int) -> Path:
    """``top`` followed by directory names that together add exactly ``chars`` characters."""
    count = -(-chars // 201)
    sizes = [chars // count - 1] * count
    sizes[-1] += chars - sum(size + 1 for size in sizes)
    return top.joinpath(*("z" * size for size in sizes))


@pytest.mark.parametrize("too_long", ["source", "directory"])
def test_a_spelling_too_long_to_check_fails_the_run(tmp_path, too_long):
    """The walk reaches the checkout first through a short chain of long names (``top/z…/z…``,
    the last a link to the checkout) and keeps that spelling, while Python's own lookup, through
    the checkout's real path, is short. When the kernel refuses ``<that spelling>/tests`` or
    ``<that spelling>/tests/helper.py`` as too long, the scan cannot say whether the planted
    bytecode stands for a protected test, so the run fails."""
    top = Path(os.path.realpath(tmp_path)) / "d"
    spelled = len("/tests") + len("/helper.py") // 2 if too_long == "source" else 3
    shortcut = nested(top, LIMIT - spelled - len(str(top)))
    steps = len(shortcut.relative_to(top).parts)
    work = top.joinpath(*["p"] * (steps + 1)) / "r1-v0.work"  # more steps than the shortcut
    checkout, run_dir = work / "r1-v0", tmp_path / "r1-v0.abc" / "run"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests" / "helper.py").write_text("OK = False\n")
    shortcut.parent.mkdir(parents=True)
    shortcut.symlink_to(checkout)
    planted = run_dir / "home" / "s"  # where both spellings lead under the prefix home/alt
    (planted / "tests").mkdir(parents=True)
    (planted / "tests" / f"helper.{TAG}.pyc").write_bytes(b"x")
    alt = run_dir / "home" / "alt"
    for spelling in (shortcut, checkout):  # the shortcut's mirror is itself too long a path
        mirror = alt / str(spelling).lstrip("/")
        mirror.parent.mkdir(parents=True, exist_ok=True)
        parent = os.open(mirror.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.symlink(planted, mirror.name, dir_fd=parent)
        finally:
            os.close(parent)
    assert len(str(alt / str(checkout).lstrip("/") / "tests" / f"helper.{TAG}.pyc")) < LIMIT
    reasons = scan(checkout, run_dir)
    if too_long == "source":  # the planted pyc's own row, not one of the listing's
        pyc = f"run/home/s/tests/helper.{TAG}.pyc"
        assert any(r.startswith(pyc) and "too long" in r for r in reasons), reasons
    else:
        assert any("too long" in reason for reason in reasons), reasons
