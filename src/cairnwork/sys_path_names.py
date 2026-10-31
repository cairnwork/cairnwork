"""The name tables ``sys_path_scan`` matches (moved out of it unchanged, #579).

``sys_path_scan`` reads these tables; its module docstring says what each one means for a commit.
"""

from __future__ import annotations

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
# Code that reaches attributes, or runs code, by a name built at run time; never followed.
DYNAMIC_NAMES = frozenset(
    {"getattr", "setattr", "delattr", "vars", "globals", "locals", "exec", "eval", "compile",
     "breakpoint", "__import__", "__builtins__"}
)  # fmt: skip
DYNAMIC_ATTRS = frozenset(
    {"__dict__", "__getattribute__", "__globals__", "__builtins__", "__code__", "f_globals",
     "f_locals", "f_builtins", "attrgetter", "methodcaller", "setenv", "unsetenv", "putenv",
     "environb", "ProcessPoolExecutor", "spec_from_file_location", "module_from_spec",
     "exec_module", "load_module", "load_source", "import_path", "pyimport", "resolve_name",
     "import_string", "import_module", "find_class", "entry_points", "load_entry_point",
     "builtins", "__self__", "__closure__", "__subclasses__", "__bases__", "__base__",
     "__mro__", "discover"}
)  # fmt: skip
# Modules that can change the import path or the import system, load or run code given as a
# path or as text, or start a program; a file that imports one (or a submodule) seals nothing.
IMPORT_MODULES = frozenset(
    {"site", "sitecustomize", "usercustomize", "runpy", "pkgutil", "zipimport", "imp", "_imp",
     "importlib", "_frozen_importlib", "_frozen_importlib_external", "sysconfig", "distutils",
     "setuptools", "pkg_resources", "code", "codeop", "types", "builtins", "inspect", "gc",
     "ctypes", "pickle", "_pickle", "marshal", "shelve", "copyreg", "doctest", "timeit", "pdb",
     "bdb", "trace", "profile", "cProfile", "pydoc", "subprocess", "_posixsubprocess", "_winapi",
     "multiprocessing", "concurrent.futures.process", "pty", "webbrowser", "logging.config",
     "unittest.mock", "mock", "pexpect", "sh", "plumbum", "execnet"}
)  # fmt: skip
# Names under which another module may hold sys, os or one of IMPORT_MODULES (``logging.os``,
# ``random._os``, ``tempfile._os``), leading underscores aside: an attribute or a ``from``
# import of one seals nothing. HELD_IF_IMPORTED are too common to refuse on any object, so they
# count only on a chain that starts at an imported module (``unittest.mock``, ``logging.config``).
HELD = frozenset(
    {"sys", "os", "posix", "nt", "runpy", "pkgutil", "zipimport", "importlib", "sysconfig",
     "distutils", "setuptools", "pkg_resources", "sitecustomize", "usercustomize", "codeop",
     "builtins", "bltns", "ctypes", "pickle", "marshal", "shelve", "copyreg", "doctest",
     "timeit", "pdb", "bdb", "cProfile", "pyprofile", "pydoc", "subprocess", "posixsubprocess",
     "winapi", "multiprocessing", "pty", "webbrowser", "frozen_importlib",
     "frozen_importlib_external", "pexpect", "plumbum", "execnet"}
)  # fmt: skip
HELD_IF_IMPORTED = frozenset(
    {"site", "code", "types", "imp", "inspect", "gc", "trace", "profile", "util", "machinery",
     "bootstrap", "bootstrap_external", "process", "context", "reduction", "reducer", "config",
     "mock", "sh", "macholib", "dyld", "dylib", "framework"}
)  # fmt: skip
