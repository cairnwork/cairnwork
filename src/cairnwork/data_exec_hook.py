"""#850 rollout step 2: a verifier run refuses to execute a declared test data file.

A test commit may declare ``.py`` files that are data, not tests (deferred_data.py). The claim
bound under ``DATA_RULE`` is narrow: a declared file is not run in-process through import,
``runpy``, eval/exec of a code object or ``types.FunctionType``, under its own name. For that,
``install_plugin(directory, data_paths)`` (report_guard.py) appends ``plugin_source(data_paths)``
to the per-run plugin, after the plugin's own text: nothing is appended for an empty set, so the
plugin is then byte for byte what it was before, and the shared ``HOOK_SOURCE`` never changes.

The appended block installs a second PEP 578 audit hook, written like the bytecode hook (a plain
function built with ``exec`` under its own name, reading only its defaults, a ``str`` subclass
judged by an exact copy of its text). It refuses:

* the ``exec`` and ``function.__new__`` events (Python 3.9 to 3.14: ``args[0]`` is the code
  object; imports, ``runpy`` and ``exec``/``eval`` of a code object raise ``exec``) when the code's
  ``co_filename`` names a declared path;
* the ``subprocess.Popen``, ``os.exec`` and ``os.posix_spawn`` events (``args[0]`` the program,
  ``args[1]`` its argv) when the program or an argv entry, or a whitespace-separated word of one,
  names a declared path. An entry that is neither ``str`` nor ``bytes`` (a ``pathlib.Path``) is
  refused too: its text cannot be read without running its code.

"Names" is a match on text, never the file system: the name is normalised (backslashes to ``/``,
empty and ``.`` parts dropped, ``..`` removing the part before it, case folded on every platform)
and refused when its last N parts are a declared path of N parts, so an absolute, relative,
``./`` or zip-form (``x.zip/tests/data/a.py``) name all match. A collision only refuses more.

A refusal raises ``DataExecRefused``, a ``BaseException`` (a plain ``except Exception`` does not
catch it), and first records the name in a latch the hook keeps in its defaults. The reporter asks
the hook for the latch at ``sessionfinish`` (the ``cairnwork.data_latch`` audit event) and writes
it as ``data_exec_refused``; the parent fails any run whose list is not empty (``refused``), and a
base run is then UNCOMPARABLE ``DATA_EXEC_REFUSED``, never teeth. At install the hook proves itself
live: ``eval(compile("0", <a declared path>, "eval"))`` must be refused, else the plugin import
fails and the run has no record.

Known limits (the claim above, no more): the file's text executed under another name, C-level
eval, a child that gets the file without naming it in argv (``python -`` with the text piped in),
and the residuals of the bytecode hook (bytecode_hook.py) and of the reporter (report_guard.py):
code that reaches the reporter instance can still change what it writes, and code that reaches the
latch (through ``gc`` or a caught refusal's traceback frames) can clear it. The latch catches an
accidental or swallowed refusal, not deliberate tampering (docs/SECURITY.md).
"""

from __future__ import annotations

FIELD = "data_exec_refused"


def normalise(path: str) -> str:
    """The pure-text normal form the hook compares (and the declared set is written in)."""
    parts: list[str] = []
    for part in path.replace("\\", "/").casefold().split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def refused(data: dict) -> tuple[str, ...] | None:
    """The record's ``data_exec_refused`` names (``()`` when absent), None when malformed."""
    names = data.get(FIELD, [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return None
    return tuple(names)


def plugin_source(data_paths) -> str:
    """The block appended to the per-run plugin; ``""`` for an empty set."""
    declared = sorted({normalise(p) for p in data_paths} - {""})
    if not declared:
        return ""
    return _BLOCK.replace("_CW_DATA_PATHS = ()", f"_CW_DATA_PATHS = {tuple(declared)!r}", 1)


_BLOCK = '''
# --- cairnwork data-exec hook (#850): written per run by cairnwork.report_guard from
# --- cairnwork/data_exec_hook.py
_CW_DATA_PATHS = ()

_CW_DATA_BODY = """
def hook(event, args, _t=None, _str=None, _bytes=None, _list=None, _tuple=None, _code=None,
         _none=None, _refuse=None, _stop=None, _type_error=None, _func=None, _name=None,
         _declared=None, _lens=None, _latch=None, _whole=None):
    if event == "exec" or event == "function.__new__":
        code = args[0]
        if _t(code) is not _code:
            return
        names = [code.co_filename]
    elif event == "subprocess.Popen" or event == "os.exec" or event == "os.posix_spawn":
        names, argv = [args[0]], args[1]
        kind = _t(argv)
        if kind is _list or kind is _tuple:
            names += argv
        else:
            names.append(argv)
    elif event == "cairnwork.data_latch":
        box = args[0]
        if _t(box) is _list:
            box += _latch
        return
    elif event == "object.__setattr__" or event == "object.__delattr__":
        target = args[0]
        if _t(target) is _func and target.__code__.co_filename == _name:
            raise _stop("cairnwork: the data-exec hook is fixed")
        return
    else:
        return
    for name in names:
        kind = _t(name)
        if kind is _none:
            continue
        if kind is not _str and kind is not _bytes:
            try:  # a str or bytes subclass: an exact copy of its own text, no method of its own
                name = _str.__str__(name)
            except _type_error:
                try:
                    name = _bytes.__getitem__(name, _whole)
                except _type_error:
                    _latch.append("<unreadable argument>")
                    raise _refuse("cairnwork: an argument that is not text is refused") from None
            kind = _t(name)
        if kind is _bytes:
            name = _bytes.decode(name, "utf-8", "surrogateescape")
        for word in [name] + name.split():
            parts = []
            for part in word.replace("\\\\\\\\", "/").casefold().split("/"):
                if part == "" or part == ".":
                    continue
                if part == ".." and parts and parts[-1] != "..":
                    parts.pop()
                    continue
                parts.append(part)
            for n in _lens:
                if "/".join(parts[-n:]) in _declared:
                    _latch.append(word)
                    raise _refuse("cairnwork: declared test data is not executed: " + word)
"""


def _cw_install_data_hook():
    """Install the data-exec hook, prove it refuses a declared name, and make the reporter write
    the latch; any failure fails this plugin's import (no record: the run fails)."""
    import sys
    import types

    class DataExecRefused(BaseException):
        """cairnwork: a declared test data file was executed in a verifier run (#850)."""

    name = "<cairnwork-data-exec-hook>"
    declared = frozenset(_CW_DATA_PATHS)
    latch = []
    space = {}
    exec(compile(_CW_DATA_BODY, name, "exec"), space)
    hook = space.pop("hook")
    space.clear()
    hook.__defaults__ = (
        type, str, bytes, list, tuple, types.CodeType, type(None), DataExecRefused, RuntimeError,
        TypeError, types.FunctionType, name, declared,
        tuple(sorted({p.count("/") + 1 for p in declared})), latch, slice(None),
    )  # fmt: skip
    sys.addaudithook(hook)
    probe = min(declared)
    live = False
    try:
        eval(compile("0", probe, "eval"))
    except DataExecRefused:
        live = latch == [probe]
    latch.clear()
    if not live:
        raise RuntimeError("cairnwork: the data-exec hook is not active")
    finish = _Reporter.pytest_sessionfinish
    io, audit = finish.__defaults__[0], sys.audit

    def pytest_sessionfinish(self, session, exitstatus, _finish=finish, _io=io, _audit=audit):
        box = []
        _audit("cairnwork.data_latch", box)  # the hook's latch: no one else holds it

        def stamp(payload, write):
            payload["data_exec_refused"] = box
            return write

        dumps, dump = _io[0], _io[1]
        return _finish(self, session, exitstatus, (
            lambda payload, *rest: stamp(payload, dumps)(payload, *rest),
            lambda payload, *rest: stamp(payload, dump)(payload, *rest),
        ) + _io[2:])  # fmt: skip

    _Reporter.pytest_sessionfinish = pytest_sessionfinish


_cw_install_data_hook()
del _cw_install_data_hook, _CW_DATA_BODY, _CW_DATA_PATHS
# --- end of the cairnwork data-exec hook ---------------------------------------------------------
'''
