"""Whether a commit's own Python code may change where Python imports from (#382).

``sealed_packages`` seals a directory only when nothing in the base (or in a test commit already
in the repository) can put it in front of sys.path. The tests, conftests and the code they import
can live anywhere in the checkout: ``python -m pytest`` puts the checkout root on sys.path, a
directory without ``__init__.py`` imports as a namespace package, a conftest in one test root can
import a root-level package, and another test root's conftest runs in the same session. So every
Python file of the commit is read, not only those near a test root.

A commit may change it when one of its Python files (``.py``/``.pyw``, any case)

- holds one of ``PATH_WORDS`` (a plain byte search: comments and strings count too), or
- uses the ``sys`` module under any name (``import sys as s``) for anything but a ``SAFE_SYS``
  attribute (``s.path``, ``from sys import path``, ``f(sys)``), takes a ``sys`` attribute from
  any other object (``os.sys``) or imports ``sys`` from another module, or names a function of
  ``os`` (also ``posix``, ``nt``) that starts a program, under any name
  (``from os import system``), or
- cannot be read: it may name one of those modules (or holds a non-ASCII byte, which an
  identifier may normalize from) and this interpreter cannot parse it (a syntax or encoding
  error), or it is a symlink; a file that names none of them reaches them only through a word;

when the commit tracks compiled code Python may import (``.pyc``, ``.pyo``, ``.pyd``, ``.so``);
or when it has more than ``MAX_FILES`` Python files or ``MAX_BYTES`` of them, or git fails. Every
one of these answers seals nothing, the stricter outcome: each directory counts as an import root,
as before #382.
"""

from __future__ import annotations

import ast
import re
import subprocess
import warnings
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes

MAX_FILES = 20_000
MAX_BYTES = 64 << 20  # 64 MiB of Python source, read at once
_REGULAR = ("100644", "100755")
_SOURCE = (".py", ".pyw")
_COMPILED = (".pyc", ".pyo", ".pyd", ".so")
# Ways Python code can put a directory on sys.path, or start Python with another sys.path.
PATH_WORDS = (
    "sys.path",  # also sys.path_hooks, sys.path_importer_cache
    "syspath",  # pytest's monkeypatch.syspath_prepend, pytester.syspathinsert
    "from sys import",
    "addsitedir",
    ".pth",
    "PYTHONPATH",
    "pytest_plugins",  # a plugin module may live anywhere
    "runpy",
    "sys.executable",
    "subprocess",
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
    "meta_path",
    "path_hooks",
    "importlib",
    "__import__",
)
_WORDS = tuple(word.encode() for word in PATH_WORDS)
# sys attributes that neither read nor change the import system or start a program.
SAFE_SYS = frozenset(
    {
        "abiflags", "api_version", "argv", "base_exec_prefix", "base_prefix", "byteorder",
        "builtin_module_names", "copyright", "displayhook", "dont_write_bytecode", "exc_info",
        "exception", "excepthook", "exec_prefix", "exit", "flags", "float_info",
        "float_repr_style", "get_int_max_str_digits", "getdefaultencoding",
        "getfilesystemencodeerrors", "getfilesystemencoding", "getprofile", "getrecursionlimit",
        "getrefcount", "getsizeof", "getswitchinterval", "gettrace", "getwindowsversion",
        "hash_info", "hexversion", "implementation", "int_info", "intern", "is_finalizing",
        "last_traceback", "last_type", "last_value", "maxsize", "maxunicode", "orig_argv",
        "platform", "platlibdir", "prefix", "set_int_max_str_digits", "setprofile",
        "setrecursionlimit", "setswitchinterval", "settrace", "stderr", "stdin",
        "stdlib_module_names", "stdout", "thread_info", "unraisablehook", "version",
        "version_info", "warnoptions", "__stderr__", "__stdin__", "__stdout__",
    }
)  # fmt: skip
_OS_MODULES = frozenset({"os", "posix", "nt"})
_OS_RUN = frozenset({"system", "popen", "putenv", "startfile"})
_OS_RUN_PREFIXES = ("exec", "spawn", "posix_spawn")
# Files worth parsing: those that may name sys, os, posix or nt (see the module docstring).
_MAY_NAME = re.compile(rb"\b(?:sys|os|posix|nt)\b|[^\x00-\x7f]")


def may_edit_sys_path(repo: Path, commit: str) -> bool:
    """Whether commit's Python code may change sys.path or start Python another way (see the
    module docstring); True whenever the scan cannot tell."""
    oids = _python_blobs(repo, commit)
    if oids is None:
        return True
    blobs = _read_blobs(repo, oids)
    if blobs is None:
        return True
    return any(_mentions(data) for data in blobs)


def _python_blobs(repo: Path, commit: str) -> list[str] | None:
    """The distinct blob ids of commit's Python files; None when one is a symlink, compiled code
    is tracked, there are too many or too large, or git fails."""
    try:
        listing = run_git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", commit)
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return None
    oids: dict[str, None] = {}
    total = 0
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, oid, size = meta.split()
        if kind == "blob" and not size.isdigit():
            return None
        leaf = path.rpartition("/")[2].casefold()
        if leaf.endswith(_COMPILED) and kind == "blob":
            return None
        if not leaf.endswith(_SOURCE) or kind != "blob":
            continue
        if mode not in _REGULAR:  # a symlink: what it points at may not be in the commit
            return None
        if oid not in oids:
            oids[oid] = None
            total += int(size)
    if len(oids) > MAX_FILES or total > MAX_BYTES:
        return None
    return list(oids)


def _read_blobs(repo: Path, oids: list[str]) -> list[bytes] | None:
    """The contents of oids, in order, from one ``git cat-file --batch``; None on any failure."""
    if not oids:
        return []
    try:
        out = run_git_bytes(
            repo, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in oids).encode()
        )
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return None
    blobs, pos = [], 0
    for oid in oids:
        end = out.find(b"\n", pos)
        header = out[pos:end].split() if end >= 0 else []
        if len(header) != 3 or header[:2] != [oid.encode(), b"blob"] or not header[2].isdigit():
            return None
        start = end + 1
        stop = start + int(header[2])
        if out[stop : stop + 1] != b"\n":
            return None
        blobs.append(out[start:stop])
        pos = stop + 1
    return blobs


def _mentions(data: bytes) -> bool:
    """Whether one file's bytes hold a path word or its syntax tree uses the import system."""
    if any(word in data for word in _WORDS):
        return True
    if not _MAY_NAME.search(data):
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # invalid escapes and the like are no answer
            tree = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return True  # unreadable: closed
    return _uses_import_system(tree)


def _uses_import_system(tree: ast.AST) -> bool:
    nodes = list(ast.walk(tree))
    sys_names, os_names = {"sys"}, set(_OS_MODULES)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and alias.name == "sys":
                    sys_names.add(alias.asname)
                elif alias.asname and alias.name in _OS_MODULES:
                    os_names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            if "sys" in names:  # ``from os import sys``, ``from logging import sys``
                return True
            if node.level == 0 and node.module == "sys":
                if any(name not in SAFE_SYS for name in names):
                    return True
            elif node.level == 0 and node.module in _OS_MODULES:
                if any(name == "*" or _starts_programs(name) for name in names):
                    return True
    safe: set[int] = set()
    for node in nodes:
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr == "sys" or (_named(node.value, os_names) and _starts_programs(node.attr)):
            return True
        if _named(node.value, sys_names) and node.attr in SAFE_SYS:
            safe.add(id(node.value))
    return any(_named(node, sys_names) and id(node) not in safe for node in nodes)


def _named(node: ast.AST, names: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in names


def _starts_programs(name: str) -> bool:
    return name in _OS_RUN or name.startswith(_OS_RUN_PREFIXES)
