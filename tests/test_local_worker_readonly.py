"""The read-only profile of list, read and search (Codex review of PR 251, must-fix 1 and
should-fix 2): Seatbelt, not only the wrapper's path checks, keeps their reads inside the checkout
and denies every write, devices included; search's CPU-time limit (must-fix 2, second layer)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from local_worker_fakes import TAG, WRAPPER, make_runs
from local_worker_fakes import run_wrapper as run

SECRET = "hello from outside the checkout"
SEARCH_CHECK = '    require_dir_inside_checkout "$dir" search\n'
READ_CHECK = '    require_dir_inside_checkout "${TARGET%/*}" read\n'
LIST_CHECK = '    require_dir_inside_checkout "$TARGET" list\n'
GREP = '/usr/bin/grep -r -n -I -D skip "$GREP_MODE" --exclude-dir=.git \\\n'
GREP += '      -e "$PATTERN" -- "$TARGET"'


@pytest.fixture
def runs(tmp_path):
    runs = make_runs(tmp_path)
    root = runs / TAG
    for where in (root / "tmp", root / "venv", root / "checkout" / ".git"):
        (where / "d").mkdir(parents=True)
        (where / "x.txt").write_text(SECRET + "\n")
        (where / "d" / "x.txt").write_text(SECRET + "\n")
    return runs


def copy_wrapper(tmp_path: Path, *edits: tuple[str, str]) -> Path:
    """The wrapper with ``edits`` applied, each of whose old text occurs exactly once."""
    text = WRAPPER.read_text()
    for old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    copy = tmp_path / "local-worker-tool"
    copy.write_text(text)
    return copy


def run_copy(
    copy: Path, runs: Path, *args: str, timeout: float = 60
) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(runs),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    argv = ["/bin/sh", str(copy), *args]
    return subprocess.run(argv, capture_output=True, env=env, cwd="/", timeout=timeout, check=False)


# The symlinked ancestor a process a sweep missed could swap in after the wrapper's checks: the
# copies below drop those checks, so only Seatbelt stands between the command and the target.
LINKS = [
    ("td", "../tmp", "td/x.txt"),  # the run's tmp
    ("vd", "../venv", "vd/x.txt"),  # the run's venv
    ("gd", ".git", "gd/x.txt"),  # the checkout's own .git
]


@pytest.mark.sandbox
@pytest.mark.parametrize(("link", "target", "path"), LINKS)
def test_search_cannot_read_through_a_symlinked_ancestor(runs, tmp_path, link, target, path):
    (runs / TAG / "checkout" / link).symlink_to(target)
    copy = copy_wrapper(tmp_path, (SEARCH_CHECK, ""))
    proc = run_copy(copy, runs, "search", str(runs / TAG), "fixed", "hello", path)
    assert proc.returncode == 2 and b"Operation not permitted" in proc.stdout, proc.stdout
    assert SECRET.encode() not in proc.stdout
    # The control: under the tool profile (the old search profile's reads) the same call leaks.
    leaky = copy_wrapper(
        tmp_path,
        (SEARCH_CHECK, ""),
        (
            '"$(readonly_profile)" "$RUN_ROOT/tmp" /usr/bin/grep',
            '"$(profile)" "$RUN_ROOT/tmp" /usr/bin/grep',
        ),
    )
    proc = run_copy(leaky, runs, "search", str(runs / TAG), "fixed", "hello", path)
    assert SECRET.encode() in proc.stdout


@pytest.mark.sandbox
@pytest.mark.parametrize(("link", "target", "path"), LINKS)
def test_read_and_list_cannot_read_through_a_symlinked_ancestor(runs, tmp_path, link, target, path):
    (runs / TAG / "checkout" / link).symlink_to(target)
    copy = copy_wrapper(tmp_path, (READ_CHECK, ""), (LIST_CHECK, ""))
    proc = run_copy(copy, runs, "read", str(runs / TAG), path)
    assert proc.returncode != 0 and b"Operation not permitted" in proc.stdout, proc.stdout
    assert SECRET.encode() not in proc.stdout
    proc = run_copy(copy, runs, "list", str(runs / TAG), link + "/d")
    assert proc.returncode != 0 and b"Operation not permitted" in proc.stdout, proc.stdout
    assert b"x.txt" not in proc.stdout


@pytest.mark.sandbox
def test_search_cannot_read_the_worker_python_tree_through_a_symlink(runs, tmp_path):
    os_py = Path(os.__file__).resolve()
    assert os_py.is_relative_to(Path(sys.base_prefix).resolve())
    (runs / TAG / "checkout" / "py").symlink_to(os_py.parent)
    copy = copy_wrapper(tmp_path, (SEARCH_CHECK, ""))
    proc = run_copy(copy, runs, "search", str(runs / TAG), "fixed", "import", "py/os.py")
    assert proc.returncode == 2 and b"Operation not permitted" in proc.stdout, proc.stdout


@pytest.mark.sandbox
def test_the_real_tools_still_read_the_checkout(runs):
    root = str(runs / TAG)
    (runs / TAG / "checkout" / "u.txt").write_text("café\n")
    assert run(runs, "read", root, "a.txt").stdout == b"hello\n"
    assert b"a.txt" in run(runs, "list", root, ".").stdout
    proc = run(runs, "search", root, "regex", "^caf.$", "u.txt")  # UTF-8: the locale is readable
    assert proc.returncode == 0 and proc.stdout == "u.txt:1:café\n".encode(), proc.stdout


# -- should-fix 2: no device write, and a probe that follows no symlink ----------------------------
@pytest.mark.sandbox
@pytest.mark.parametrize("device", ["/dev/null", "/dev/tty", "/dev/dtracehelper"])
def test_the_read_only_profile_writes_no_device(runs, tmp_path, device):
    """A copy whose search runs touch (it changes a device's times, a write) and opens it."""
    script = '/bin/sh -c \'/usr/bin/touch -- "$1"; : >"$1"\' sh "$PATTERN"'
    copy = copy_wrapper(tmp_path, (GREP, script))
    proc = run_copy(copy, runs, "search", str(runs / TAG), "fixed", device, ".")
    assert proc.stdout.count(b"Operation not permitted") == 2, proc.stdout


@pytest.mark.sandbox
@pytest.mark.parametrize("where", ["checkout", "tmp"])
@pytest.mark.parametrize("planted", ["/dev/null", "missing", "dir"])
def test_search_probe_refuses_a_planted_probe_path(runs, where, planted):
    probe = runs / TAG / where / "search-probe"
    if planted == "dir":
        probe.mkdir()
    else:
        probe.symlink_to(planted)
    proc = run(runs, "search-probe", str(runs / TAG))
    assert proc.returncode == 64 and b"search-probe path exists" in proc.stderr, proc.stderr


@pytest.mark.sandbox
def test_search_probe_follows_no_symlink_planted_after_its_check(runs, tmp_path):
    """The race the refusal above cannot see: a copy without it, the probe path a dangling
    symlink. mkdir fails on the link itself (EEXIST), never reaching its target; a following
    create (touch) would get as far as Seatbelt's denial of the target."""
    probe = runs / TAG / "checkout" / "search-probe"
    probe.symlink_to("landed")
    check = '[ ! -e "$p" ] && [ ! -L "$p" ] || refuse'
    copy = copy_wrapper(tmp_path, (check, "true || refuse"))
    proc = run_copy(copy, runs, "search-probe", str(runs / TAG))
    assert f"{probe}: File exists".encode() in proc.stdout, proc.stdout
    assert proc.stdout.count(b"Operation not permitted") == 2  # tmp and /dev/null
    assert not (runs / TAG / "checkout" / "landed").exists()


@pytest.mark.sandbox
def test_search_probe_denies_all_three_writes(runs):
    root = runs / TAG
    proc = run(runs, "search-probe", str(root))
    assert proc.returncode == 1 and proc.stdout.count(b"Operation not permitted") == 3
    assert b"/dev/null: Operation not permitted" in proc.stdout
    assert not os.path.lexists(root / "checkout" / "search-probe")
    assert not os.path.lexists(root / "tmp" / "search-probe")


# -- must-fix 2, second layer: grep's CPU-time limit ---------------------------------------------
@pytest.mark.sandbox
def test_search_has_a_cpu_time_limit(runs, tmp_path):
    """The Python cap refuses back-references; the wrapper alone (no cap) still stops this one."""
    text = WRAPPER.read_text()
    assert text.count("SEARCH_CPU_SECONDS=20 ") == 1
    assert text.count('ulimit -t "$SEARCH_CPU_SECONDS"\n') == 1
    (runs / TAG / "checkout" / "br.txt").write_text("a" * 40 + "b\n")
    copy = copy_wrapper(tmp_path, ("SEARCH_CPU_SECONDS=20 ", "SEARCH_CPU_SECONDS=1 "))
    started = time.monotonic()
    proc = run_copy(copy, runs, "search", str(runs / TAG), "regex", r"^(a+)+\1$", "br.txt")
    assert proc.returncode == -24, (proc.returncode, proc.stdout)  # SIGXCPU
    assert time.monotonic() - started < 20
