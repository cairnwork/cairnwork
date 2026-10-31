"""The audit hook a verifier's pytest process installs before any candidate code runs (#276).

Candidate code runs inside the verifier's pytest process. Bytecode it plants where the import
system or pytest's assertion rewriter looks would run instead of a protected test, conftest or
helper; the after-run scans (pycache_guard.py, bytecode_roots.py) see only what is still there
when the run ends, and Seatbelt's ``.pyc`` rules (#368) exist on macOS only. So the verifier's
pytest process refuses to load bytecode from files at all: ``HOOK_SOURCE`` is written at the top
of the trusted report plugin (report_guard.install_plugin), which pytest imports with ``-p``
before any conftest, test module or candidate code. It installs a PEP 578 audit hook that:

* refuses to open, for reading, a path whose name ends in ``.pyc`` in any letter case
  (``PermissionError``: the import system and pytest treat it as a cache miss and compile the
  source, as an honest run already does, its cache prefix being an empty directory);
* refuses to unmarshal a code object (``marshal.loads`` of data that starts with one) and any
  ``marshal.load``: the import system's bytecode loaders (cached and sourceless imports,
  zipimport) and pytest's assertion-rewrite cache go through one of the two, so bytecode they
  would load from a file is refused whatever the file is called, where it lies and when it is
  removed;
* keeps itself in place against pure-Python code in the same process: its function and defaults
  cannot be replaced, and the object-graph walks (``gc.get_referrers``, ``gc.get_referents``,
  ``gc.get_objects``) and ``sys._current_frames`` that would reach it are refused outright;
* refuses opening a file named ``mem`` (Linux ``/proc/<pid>/mem``) or making a symlink to one,
  and Python 3.14's remote debugger script hook (the run also sets PYTHON_DISABLE_REMOTE_DEBUG).

A path that arrives as a str or bytes subclass is judged by an exact copy of its text, taken by
the base type's own slot, never by a method of the subclass; any other object (``io.FileIO`` given
a ``pathlib.Path``: the event carries the object, not the name the file system sees) is refused,
since its name cannot be read without running its code. Frozen standard-library modules are
imported before the hook, each from the interpreter's own frozen table or the plugin fails (3.13
and later unmarshal them on the audited path). Then the hook probes itself, and the
plugin import (and so the whole run: no verdict record) fails when the hook is not live or the
interpreter is older than 3.9.

How it is written (each rule from a measured way to switch an audit hook off): a plain function,
not an instance; built with ``exec`` under its own file name; it reads no globals, builtins or
closure when called, only its defaults, which hold types, exception classes, strings, frozen sets
of strings and one ``slice``; types are compared with the bound ``type`` only (no ``isinstance``,
no ``__class__``); it keeps no state and reports nothing, refusal is the exception it raises.

Not a boundary. The frozen-origin check trusts the import state it finds: it relies on nothing
untrusted having run before the plugin, which the verifier's command line and environment guarantee
(``-P``, ``PYTHONNOUSERSITE``, a read-only guard directory first on ``PYTHONPATH``) together with
the protected start-up files and root modules (tests/test_root_modules.py and
tests/test_candidate_shadow_refusals.py pin those two). From Python 3.13 on, code in the same
process may still change the hook's arguments at its entry; ctypes or native code can rewrite
memory; code the candidate builds or unmarshals explicitly in its own process is not covered; and
code can change what a test reads without any bytecode file (``sys.modules``, import hooks: #303).
The hook guards the process it is installed in: a forked child keeps it, a child Python a test
starts does not (no child produces the verdict). This raises the cost of one way to make a verdict
disagree with the source, like tamper_scan and report_channel; the boundary is a verdict produced
outside the candidate's process. Honest code it refuses: reading back a ``.pyc`` (as Seatbelt
already does), unmarshalling code objects, the gc walks above (leak and reference-cycle checks;
``hypothesis.register_random`` and ``deterministic_PRNG`` outside ``@given``, typeguard's
``check_argument_types`` and ``check_return_type``), ``sys._current_frames`` (a stack dump, as
pytest-timeout prints on a timeout) and ``io.FileIO`` on a path object. Coverage, debuggers and
``sys.settrace`` are not affected.
"""

from __future__ import annotations

import sys
from pathlib import Path

NAME = "<cairnwork-bytecode-hook>"  # the hook's co_filename: how it recognises itself
PTRACE_SCOPE = Path("/proc/sys/kernel/yama/ptrace_scope")
PTRACE_NOTE = "bytecode hook: ptrace_scope "


def host_notes() -> tuple[str, ...]:
    """What the host leaves open around the hook, read outside the sandbox for the run's notes.

    Linux: with Yama's ``ptrace_scope`` at 0 (or no Yama), any process of the run's user may write
    another's memory, so a child the candidate starts could change the pytest process that holds
    the hook (the hook only guards the process it is in). GitHub's runners and common distributions
    use 1, which limits that to a process's own descendants. A note, not a refusal: the hook is a
    hurdle either way, and the verdict is unchanged."""
    if sys.platform != "linux":
        return ()
    try:
        value = PTRACE_SCOPE.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        value = "unreadable"
    return () if value in ("1", "2", "3") else (f"{PTRACE_NOTE}{value or 'empty'}",)


HOOK_SOURCE = '''\
# --- cairnwork bytecode hook: written by cairnwork.report_guard from cairnwork/bytecode_hook.py
import sys as _cw_sys

_CW_HOOK_BODY = """
def hook(event, args, _t=None, _str=None, _bytes=None, _ba=None, _mv=None, _int=None,
         _perm=None, _stop=None, _type_error=None, _func=None, _name=None, _pyc=None,
         _bpyc=None, _whole=None, _rmsg=None, _nmsg=None):
    if event == "open" or event == "os.symlink":
        path = args[0]
        kind = _t(path)
        if kind is _int and event == "open":
            return
        if kind is not _str and kind is not _bytes:
            try:  # a str or bytes subclass: an exact copy of its own text, no method of its own
                path = _str.__str__(path)
            except _type_error:
                try:
                    path = _bytes.__getitem__(path, _whole)
                except _type_error:
                    raise _perm(13, _nmsg, "") from None
            kind = _t(path)
        if kind is _str:
            tail, last = path[-4:], path.rpartition("/")[2]
            pyc = tail[:1] == "." and tail[1:] in _pyc
        else:
            tail, last = path[-4:], path.rpartition(b"/")[2]
            pyc = tail[:1] == b"." and tail[1:] in _bpyc
            last = "mem" if last == b"mem" else ""
        if last == "mem":
            raise _perm(13, _nmsg, path)
        if event == "os.symlink":
            return
        mode, flags = args[1], args[2]
        if _t(mode) is _str:
            reads = "r" in mode or "+" in mode
        else:
            reads = _t(flags) is not _int or (flags & 3) != 1
        if reads and pyc:
            raise _perm(13, _rmsg, path)
    elif event == "marshal.loads":
        data = args[0]
        kind = _t(data)
        if not (kind is _bytes or kind is _ba or kind is _mv) or (
            data[:1] and (data[0] & 0x7F) == 0x63
        ):
            raise _stop("cairnwork: code is never unmarshalled in a verifier run")
    elif event == "marshal.load":
        raise _stop("cairnwork: code is never unmarshalled in a verifier run")
    elif event == "object.__setattr__" or event == "object.__delattr__":
        target = args[0]
        if _t(target) is _func and target.__code__.co_filename == _name:
            raise _stop("cairnwork: the bytecode hook is fixed")
    elif (
        event == "gc.get_referrers"
        or event == "gc.get_referents"
        or event == "gc.get_objects"
        or event == "sys._current_frames"
        or event == "cpython.remote_debugger_script"
    ):
        raise _stop("cairnwork: " + event + " is not available in a verifier run")
"""


def _cw_install_bytecode_hook():
    """Install the hook, then prove it is live; any failure fails this plugin's import."""
    import marshal
    import types

    if _cw_sys.version_info < (3, 9):
        raise RuntimeError("cairnwork: a verifier run needs Python 3.9 or newer")
    name = "<cairnwork-bytecode-hook>"
    message = "cairnwork: bytecode is never read in a verifier run"
    pyc = frozenset(a + b + c for a in "pP" for b in "yY" for c in "cC")
    space = {}
    exec(compile(_CW_HOOK_BODY, name, "exec"), space)
    hook = space.pop("hook")
    space.clear()
    hook.__defaults__ = (
        type, str, bytes, bytearray, memoryview, int, PermissionError, RuntimeError, TypeError,
        types.FunctionType, name, pyc, frozenset(s.encode() for s in pyc), slice(None), message,
        "cairnwork: not available in a verifier run",
    )  # fmt: skip
    import _frozen_importlib  # 3.13+ unmarshals frozen stdlib modules on the audited path:
    import _imp  # load them first, and only from the interpreter's own frozen table

    for frozen in getattr(_imp, "_frozen_module_names", tuple)():
        if frozen.startswith("__"):  # not the interpreter's own __hello__ test modules
            continue
        module = __import__(frozen, fromlist=["_"])
        spec = getattr(module, "__spec__", None)
        if _frozen_importlib.FrozenImporter.find_spec(frozen) is not None and (
            spec is None or spec.origin != "frozen"
        ):
            raise RuntimeError("cairnwork: " + frozen + " did not load from the interpreter")
    _cw_sys.addaudithook(hook)
    live = False
    try:  # refused by the hook before the path reaches the file system
        open("/nonexistent-cairnwork-probe/probe.pyc", "rb").close()
    except PermissionError as error:
        live = error.strerror == message
    except OSError:
        pass
    try:
        marshal.loads(marshal.dumps(compile("0", name, "eval")))
        live = False
    except RuntimeError:
        pass
    if not live:
        raise RuntimeError("cairnwork: the bytecode hook is not active")


_cw_install_bytecode_hook()
del _cw_install_bytecode_hook, _CW_HOOK_BODY, _cw_sys
# --- end of the cairnwork bytecode hook --------------------------------------------------------
'''
