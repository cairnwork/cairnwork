"""#368: bytecode the run creates under a write root is never loaded (residual of #276).

The candidate can point ``sys.pycache_prefix`` anywhere it may write, plant bytecode for a
protected file and remove it before the after-run scan (test_pycache_redirect_spellings.py). The
profile therefore denies reading ``*.pyc`` under every write root, and creating a symlink with
such a name there (Seatbelt matches a symlink's target, not its name). Writing bytecode stays
allowed: an honest import then misses the cache and compiles from source. A ``.pyc`` symlink in
the candidate commit itself is the after-run scan's (removing it mutates the checkout).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork import pytest_config
from cairnwork_sandbox import seatbelt

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS sandbox-exec")


def test_each_write_root_denies_pyc_reads_and_pyc_symlinks_after_the_allow_rules(tmp_path):
    write = tmp_path / "w+x.y"
    write.mkdir()
    profile = seatbelt.build_profile((), (write,), home=tmp_path / "home").text
    root = str(write.resolve()).replace(".", "\\.").replace("+", "\\+")
    pyc = f'(regex #"^{root}/.*\\.pyc$")'
    assert f"(deny file-read-data {pyc})" in profile
    assert f"(deny file-write-create (require-all {pyc} (vnode-type SYMLINK)))" in profile
    assert profile.index("(allow file-write*") < profile.index(f"(deny file-read-data {pyc})")


def test_read_roots_keep_their_bytecode(tmp_path):
    profile = seatbelt.build_profile((tmp_path / "r",), (), home=tmp_path / "home").text
    assert "file-read-data" not in profile and "SYMLINK" not in profile


PROBE = """
import importlib.machinery, os, py_compile, sys
w = sys.argv[1]
def attempt(name, fn):
    try:
        fn()
        print(name, "ok")
    except OSError:
        print(name, "denied")
open(os.path.join(w, "blob"), "w").close()
py_compile.compile(os.path.join(w, "m.py"), cfile=os.path.join(w, "d", "m.pyc"))
attempt("write", lambda: open(os.path.join(w, "d", "n.pyc"), "wb").close())
attempt("read", lambda: open(os.path.join(w, "d", "m.pyc"), "rb").close())
attempt("via_dotdot", lambda: open(os.path.join(w, "d", "..", "d", "m.pyc"), "rb").close())
attempt("case", lambda: open(os.path.join(w, "D", "M.pyc"), "rb").close())
os.rename(os.path.join(w, "d"), os.path.join(w, "e"))
attempt("renamed_dir", lambda: open(os.path.join(w, "e", "m.pyc"), "rb").close())
attempt("sourceless", lambda: importlib.machinery.SourcelessFileLoader(
    "m", os.path.join(w, "e", "m.pyc")).load_module())
attempt("symlink", lambda: os.symlink("blob", os.path.join(w, "s.pyc")))
os.symlink("blob", os.path.join(w, "lnk"))
attempt("rename_symlink", lambda: os.rename(os.path.join(w, "lnk"), os.path.join(w, "r.pyc")))
attempt("hardlink", lambda: os.link(os.path.join(w, "blob"), os.path.join(w, "h.pyc")))
attempt("hardlink_read", lambda: open(os.path.join(w, "h.pyc"), "rb").close())
sys.path.insert(0, w)
import m
print("import", m.X)
"""


@mac_only
@pytest.mark.sandbox
def test_bytecode_under_a_write_root_cannot_be_read_by_any_spelling(tmp_path):
    write = (tmp_path / "w").resolve()
    write.mkdir()
    (write / "m.py").write_text("X = 1\n")
    py = sys.executable
    profile = seatbelt.build_profile(pytest_config.interpreter_roots(py), (write,), home=tmp_path)
    path = tmp_path / "p.sb"
    path.write_text(profile.text)
    argv = seatbelt.wrap([py, "-I", "-c", PROBE, str(write)], path)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert dict(line.split(" ", 1) for line in done.stdout.splitlines()) == {
        "write": "ok",  # py_compile and honest caches still write
        "read": "denied",
        "via_dotdot": "denied",
        "case": "denied",
        "renamed_dir": "denied",
        "sourceless": "denied",
        "symlink": "denied",  # a symlink is matched by its target, so none may carry the name
        "rename_symlink": "denied",
        "hardlink": "ok",
        "hardlink_read": "denied",  # a hardlink is matched by the name it is opened with
        "import": "1",  # the cache miss compiles from source
    }
    assert not (write / "s.pyc").is_symlink() and not (write / "r.pyc").exists()
    assert Path(write / "e" / "m.pyc").is_file()  # written, only never read back
