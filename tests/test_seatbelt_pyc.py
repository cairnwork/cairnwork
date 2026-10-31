"""#368: bytecode the run creates under a write root is never loaded (residual of #276).

The candidate can point ``sys.pycache_prefix`` anywhere it may write, plant bytecode for a
protected file and remove it before the after-run scan (test_pycache_redirect_spellings.py). The
profile therefore denies reading ``*.pyc`` under every write root, creating a symlink with
such a name there (Seatbelt matches a symlink's target, not its name), and unlinking one there (so
no symlink can be swapped in for an existing ``.pyc`` with RENAME_SWAP). Writing bytecode stays
allowed: an honest import then misses the cache and compiles from source. A ``.pyc`` symlink in
the candidate commit itself is the after-run scan's (removing it mutates the checkout). On the
default case-insensitive APFS volume Seatbelt matches these rules without regard to letter case, so
a file stored as ``X.PYC`` (what opening ``X.pyc`` reads there) is covered by the lower-case rule.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from test_pycache_redirect_spellings import PLANT, REMOVED
from verify_helpers import base_of, candidate, verify

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
    assert f"(deny file-write-unlink {pyc})" in profile  # no RENAME_SWAP of a symlink onto one
    assert profile.index("(allow file-write*") < profile.index(f"(deny file-read-data {pyc})")


def test_read_roots_keep_their_bytecode(tmp_path):
    profile = seatbelt.build_profile((tmp_path / "r",), (), home=tmp_path / "home").text
    assert "file-read-data" not in profile and "SYMLINK" not in profile
    assert "file-write-unlink" not in profile


PROBE = """
import ctypes, importlib.machinery, os, py_compile, sys
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
libc = ctypes.CDLL(None, use_errno=True)
def swap(a, b):  # renamex_np(RENAME_SWAP): exchanges two existing names
    if libc.renamex_np(a.encode(), b.encode(), 2) != 0:
        raise OSError(ctypes.get_errno(), "renamex_np")
open(os.path.join(w, "x.pyc"), "wb").close()
os.symlink("blob", os.path.join(w, "lnk2"))
attempt("swap_symlink", lambda: swap(os.path.join(w, "lnk2"), os.path.join(w, "x.pyc")))
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
        "swap_symlink": "denied",  # nor be swapped in for an existing .pyc
        "hardlink": "ok",
        "hardlink_read": "denied",  # a hardlink is matched by the name it is opened with
        "import": "1",  # the cache miss compiles from source
    }
    assert not (write / "s.pyc").is_symlink() and not (write / "r.pyc").exists()
    assert not (write / "x.pyc").is_symlink()
    assert Path(write / "e" / "m.pyc").is_file()  # written, only never read back


# REMOVED["built_rmtree"], but each forged pyc is written as a plain .pyc and then swapped with a
# symlink to the forged bytes (renamex_np RENAME_SWAP); the read would then match the target.
SWAPPED = PLANT.replace("import importlib", "import ctypes, importlib").replace(
    "    Path(_pyc).write_bytes(_data)\n",
    "    Path(_pyc).write_bytes(b'x')\n"
    "    Path(_pyc + '.blob').write_bytes(_data)\n"
    "    os.symlink(_pyc + '.blob', _pyc + '.lnk')\n"
    "    ctypes.CDLL(None).renamex_np((_pyc + '.lnk').encode(), _pyc.encode(), 2)\n",
)


@mac_only
@pytest.mark.sandbox
def test_a_pyc_swapped_for_a_symlink_end_to_end_is_not_verified(state, helper_project):
    assert "renamex_np" in SWAPPED and "import ctypes" in SWAPPED
    base = base_of(helper_project)
    files = {"src/calc.py": SWAPPED.format(redirect=REMOVED["built_rmtree"])}
    sha = candidate(helper_project, base, files)
    out = verify(state, helper_project, base, sha, "--import-path", "src")[1]
    assert out["status"] == "FAILED" and not out["verified"], out


CASE_PROBE = """
import os, sys
w = sys.argv[1]
def attempt(name, fn):
    try:
        fn()
        print(name, "ok")
    except OSError:
        print(name, "denied")
open(os.path.join(w, "blob"), "w").close()
attempt("write", lambda: open(os.path.join(w, "U.PYC"), "wb").close())
attempt("read_upper", lambda: open(os.path.join(w, "U.PYC"), "rb").close())
attempt("read_upper_as_lower", lambda: open(os.path.join(w, "u.pyc"), "rb").close())
open(os.path.join(w, "V.Pyc"), "wb").close()
attempt("read_mixed", lambda: open(os.path.join(w, "V.Pyc"), "rb").close())
attempt("symlink_upper", lambda: os.symlink("blob", os.path.join(w, "s.PYC")))
open(os.path.join(w, "t.pYc"), "wb").close()
attempt("unlink_mixed", lambda: os.unlink(os.path.join(w, "t.pYc")))
attempt("read_other", lambda: open(os.path.join(w, "blob"), "rb").close())
"""


@mac_only
@pytest.mark.sandbox
def test_bytecode_names_in_any_letter_case_are_covered(tmp_path):
    write = (tmp_path / "w").resolve()
    write.mkdir()
    py = sys.executable
    profile = seatbelt.build_profile(pytest_config.interpreter_roots(py), (write,), home=tmp_path)
    path = tmp_path / "p.sb"
    path.write_text(profile.text)
    argv = seatbelt.wrap([py, "-I", "-c", CASE_PROBE, str(write)], path)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert dict(line.split(" ", 1) for line in done.stdout.splitlines()) == {
        "write": "ok",
        "read_upper": "denied",
        "read_upper_as_lower": "denied",  # the name an import asks for, stored as U.PYC
        "read_mixed": "denied",
        "symlink_upper": "denied",
        "unlink_mixed": "denied",
        "read_other": "ok",  # other names under the write root stay readable
    }
    assert not (write / "s.PYC").is_symlink() and (write / "t.pYc").is_file()


# REMOVED["built_rmtree"], but each forged pyc is stored under an upper-case suffix; the import
# asks for the lower-case name and the case-insensitive file system returns that file.
UPPER = PLANT.replace(
    "    Path(_pyc).write_bytes(_data)\n", "    Path(_pyc[:-4] + '.PYC').write_bytes(_data)\n"
)


@mac_only
@pytest.mark.sandbox
def test_a_pyc_stored_with_an_upper_case_suffix_end_to_end_is_not_verified(state, helper_project):
    assert ".PYC" in UPPER
    base = base_of(helper_project)
    files = {"src/calc.py": UPPER.format(redirect=REMOVED["built_rmtree"])}
    sha = candidate(helper_project, base, files)
    out = verify(state, helper_project, base, sha, "--import-path", "src")[1]
    assert out["status"] == "FAILED" and not out["verified"], out
