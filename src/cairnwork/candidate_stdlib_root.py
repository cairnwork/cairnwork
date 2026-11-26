"""Refuse a new standard-library-named file where it can be an import root (#1829).

The path gate (candidate_sys_path.py) looks for a shadow module only when a changed file carries a
path or import-machinery signal. A candidate that adds a file named like a standard-library module
(``json.py``, a ``json/__init__.py`` package, a compiled ``json.so``) in a directory the base
already makes a search root has no such signal, yet at run time the added file wins over the real
module. This check does not need a signal: it judges the added name and the directory alone.

A newly added (or type-changed) name that equals a standard-library module or a pytest-family name
is refused unless the directory is provably not a search root: it holds an ``__init__.py`` in the
candidate tree, it is not named by a ``pythonpath`` setting, and no ``.pth`` / ``sitecustomize`` /
``usercustomize`` file or Python source with a path signal exists in the tree. The repository root
(depth 0) and any directory without ``__init__.py`` always count as possible roots, because a test
file, a ``conftest.py``, a script run in it or ``python -m`` from it each make it one. A
configuration file that cannot be read, is over the read cap, is not UTF-8 or does not parse counts
as "may name any directory" (only more refusals).
Residuals (#1829), left to a run-time module-origin check: a directory with ``__init__.py`` that
the verifier command or the environment outside the repository (``PYTHONPATH``, ``--import-mode``
flags, a script launched in that directory) makes a root; names outside the fixed list below.
Accepted false refusals: a bare directory that is never a root (documentation or example
directories, a namespace-package layout) and a directory a ``.pth`` file adds after the standard
library, which cannot shadow a standard-library name.
"""

from __future__ import annotations

import configparser
import posixpath
import tomllib
from collections.abc import Iterable

from cairnwork.candidate_shadow import _ALWAYS_SHADOW, _read_source_blobs, _source_batches
from cairnwork.git_read_cap import read_git_bytes
from cairnwork.gitcmd import GitCommandError
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected

# Fixed union of standard-library top-level names (CPython 3.14 plus modules removed in 3.12 and
# 3.13), so the verdict does not depend on the interpreter that runs the gate.
_STDLIB = frozenset(
    (
        "__future__ _abc _aix_support _android_support _apple_support _ast _ast_unparse "
        "_asyncio _bisect _blake2 _bootlocale _bz2 _codecs _codecs_cn _codecs_hk "
        "_codecs_iso2022 _codecs_jp _codecs_kr _codecs_tw _collections _collections_abc "
        "_colorize _compat_pickle _contextvars _csv _ctypes _curses _curses_panel _datetime "
        "_dbm _decimal _dummy_thread _elementtree _frozen_importlib "
        "_frozen_importlib_external _functools _gdbm _hashlib _heapq _hmac _imp "
        "_interpchannels _interpqueues _interpreters _io _ios_support _json _locale _lsprof "
        "_lzma _markupbase _md5 _multibytecodec _multiprocessing _opcode _opcode_metadata "
        "_operator _osx_support _overlapped _pickle _posixshmem _posixsubprocess _py_abc "
        "_py_warnings _pydatetime _pydecimal _pyio _pylong _pyrepl _queue _random "
        "_remote_debugging _scproxy _sha1 _sha2 _sha3 _signal _sitebuiltins _socket _sqlite3 "
        "_sre _ssl _stat _statistics _string _strptime _struct _suggestions _symtable "
        "_sysconfig _thread _threading_local _tkinter _tokenize _tracemalloc _types _typing "
        "_uuid _warnings _weakref _weakrefset _winapi _wmi _zoneinfo _zstd abc aifc "
        "annotationlib antigravity argparse array ast asynchat asyncio asyncore atexit "
        "audioop base64 bdb binascii binhex bisect builtins bz2 cProfile calendar cgi cgitb "
        "chunk cmath cmd code codecs codeop collections colorsys compileall compression "
        "concurrent configparser contextlib contextvars copy copyreg crypt csv ctypes curses "
        "dataclasses datetime dbm decimal difflib dis distutils doctest dummy_threading email "
        "encodings ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch "
        "formatter fractions ftplib functools gc genericpath getopt getpass gettext glob "
        "graphlib grp gzip hashlib heapq hmac html http idlelib imaplib imghdr imp importlib "
        "inspect io ipaddress itertools json keyword lib2to3 linecache locale logging lzma "
        "macpath mailbox mailcap marshal math mimetypes mmap modulefinder msilib msvcrt "
        "multiprocessing netrc nis nntplib nt ntpath nturl2path numbers opcode operator "
        "optparse os ossaudiodev parser pathlib pdb pickle pickletools pipes pkgutil platform "
        "plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile pyclbr "
        "pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource "
        "rlcompleter runpy sched secrets select selectors shelve shlex shutil signal site "
        "smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile sre_constants "
        "sre_parse ssl stat statistics string stringprep struct subprocess sunau symbol "
        "symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile termios test "
        "textwrap this threading time timeit tkinter token tokenize tomllib trace traceback "
        "tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib uu uuid "
        "venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc "
        "zipapp zipfile zipimport zlib zoneinfo "
    ).split()
)
# conftest is not a real module: adding one replaces nothing, so it is judged by the old rules
_NAMES = frozenset(n.casefold() for n in _STDLIB | (_ALWAYS_SHADOW - {"conftest"}))
_MODULE_SUFFIXES = (".py", ".pyc", ".pyo", ".so", ".pyd")
_CONFIGS = frozenset({"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"})
_PATH_KEYS = frozenset({"pythonpath", "python_paths"})
_STARTUP = frozenset({"sitecustomize.py", "usercustomize.py"})


def refuse_stdlib_named_adds(repo, candidate: str, entries: Iterable, own: set[str]) -> None:
    """Raise CANDIDATE_SYS_PATH_MUTATION for a new own file named like a standard-library module
    whose directory may be an import root. ``entries`` are the change-set entries; only paths in
    ``own`` (the allowed files) are judged."""
    named = [
        (e.path, d)
        for e in entries
        if e.path in own and _is_new(e) and (d := _import_dir(e.path)) is not None
    ]
    if not named:
        return
    tree: set[str] = set()
    try:
        tree = _tree_paths(repo, candidate)
        roots = _pythonpath_dirs(repo, candidate, tree)  # None: may name any directory
        loose = roots is None or _startup_or_path_signal(repo, tree)
    except (GitCommandError, OSError, ValueError, UnicodeError, MemoryError, RecursionError):
        roots, loose = None, True
    for path, directory in named:
        if (
            directory == ""
            or loose
            or directory in (roots or ())
            or (posixpath.join(directory, "__init__.py") not in tree)
        ):
            raise CandidateRejected(
                "CANDIDATE_SYS_PATH_MUTATION",
                f"{path!r}: adds a file named like a standard-library module where it can be an "
                "import root, so it could replace the real module at run time",
            )


def _is_new(entry) -> bool:
    """An added path, or a path whose file type changed (regular file, symlink, other)."""
    if entry.change_type == "ADD":
        return True
    return (
        entry.change_type == "MODIFY"
        and (entry.before_mode or "")[:3] != (entry.after_mode or "")[:3]
    )


def _import_dir(path: str) -> str | None:
    """The directory a new file would be a top-level import in, or None when its name is not a
    standard-library module. A ``name/__init__.py`` package is judged in the directory above it."""
    parts = path.split("/")
    leaf = parts[-1]
    if leaf.casefold() == "__init__.py":
        if len(parts) < 2 or parts[-2].casefold() not in _NAMES:
            return None
        return "/".join(parts[:-2])
    if not leaf.casefold().endswith(_MODULE_SUFFIXES):
        return None
    return "/".join(parts[:-1]) if leaf.split(".")[0].casefold() in _NAMES else None


def _tree_paths(repo, commit: str) -> dict[str, str]:
    raw = read_git_bytes(repo, "ls-tree", "-r", "-z", commit)
    tree: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if item:
            header, path = item.split(b"\t", 1)
            tree[path.decode("utf-8", "surrogateescape")] = header.split(b" ")[2].decode("ascii")
    return tree


def _pythonpath_dirs(repo, commit: str, tree: dict[str, str]) -> frozenset[str] | None:
    """Directories named by a ``pythonpath`` setting in any configuration file of the tree, or
    None when one cannot be trusted (unreadable, over the cap, bad encoding, unparsable)."""
    found: set[str] = set()
    for path in sorted(tree):
        leaf = path.rsplit("/", 1)[-1]
        if leaf not in _CONFIGS:
            continue
        try:
            text = read_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}").decode("utf-8")
            values = _toml_values(text) if leaf.endswith(".toml") else _ini_values(text)
        except (
            GitCommandError,
            UnicodeError,
            ValueError,
            configparser.Error,
            tomllib.TOMLDecodeError,
        ):
            return None
        base = path.rpartition("/")[0]
        found.update(posixpath.normpath(posixpath.join(base, v)) if v else base for v in values)
        found.update(v.rstrip("/") for v in values)  # also as written, from any rootdir
    return frozenset(d for d in found if d != ".") | ({""} if "." in found else set())


def _toml_values(text: str) -> list[str]:
    table = tomllib.loads(text).get("tool", {}).get("pytest", {}).get("ini_options", {})
    out: list[str] = []
    for key in _PATH_KEYS:
        value = table.get(key, [])
        out.extend(value.split() if isinstance(value, str) else [str(v) for v in value])
    return out


def _ini_values(text: str) -> list[str]:
    parser = configparser.RawConfigParser()
    parser.read_string(text)
    out: list[str] = []
    for section in ("pytest", "tool:pytest"):
        if parser.has_section(section):
            for key in _PATH_KEYS:
                out.extend(parser.get(section, key, fallback="").split())
    return out


def _startup_or_path_signal(repo, tree: dict[str, str]) -> bool:
    """Whether the tree holds a ``.pth`` / startup file, or Python source that edits the path, so
    no directory with ``__init__.py`` can be proved to be no root. Unreadable source counts."""
    if any(p.endswith(".pth") or p.rsplit("/", 1)[-1] in _STARTUP for p in tree):
        return True
    sources = {p: o for p, o in tree.items() if p.casefold().endswith(".py")}
    first = {}
    for path, oid in sources.items():
        first.setdefault(oid, path)
    for batch in _source_batches(repo, list(first)):
        for blob in _read_source_blobs(repo, batch).values():
            if scan_sys_path_blob(blob) is not False:
                return True
    return False
