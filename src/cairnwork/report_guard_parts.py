"""Parts of the report_guard plugin source kept out of report_guard.py (near the 400-line cap):
the fixture table, _source and _missing (pure moves) and the import-error recorders. Plugin text,
not parent code: the plugin cannot import cairnwork."""

PARTS_SOURCE = """\
import sys
from types import ModuleType

# #804: fixture -> module of the pytest_config.PLUGINS entry that provides it. A setup error that
# says one of them is missing is recorded as "plugin:<module>"; the parent keeps it as "plugin"
# (base PLUGIN_NOT_INSTALLED) only when that module cannot be found in the verifier's Python.
_PLUGIN_FIXTURES = {
    **dict.fromkeys(("mocker", "class_mocker", "module_mocker", "package_mocker",
                     "session_mocker"), "pytest_mock"),
    # pytest-django: only names a project rarely defines itself (not db, client or settings)
    **dict.fromkeys(("transactional_db", "django_db_setup", "django_db_blocker", "admin_client",
                     "admin_user", "rf", "async_rf", "live_server", "django_user_model",
                     "django_assert_num_queries", "mailoutbox"), "pytest_django.plugin"),
    "anyio_backend": "anyio.pytest_plugin",
}


def _innermost(exc):  # the file of the innermost frame that is not the import machinery
    tb, last = exc.__traceback__, ""
    while tb is not None:
        file = tb.tb_frame.f_code.co_filename
        last = last if file.startswith("<frozen importlib") else file
        tb = tb.tb_next
    return last


def _import_error(node, call, report, _real=os.path.realpath, _rel=os.path.relpath,
                  _abs=os.path.isabs, _str=str, _last=_innermost):
    # #796: "cannot import name X from <module>" in a test module's own import statement. A
    # record is a HINT, not proof of a failed import: a test module can raise an ImportError of
    # this shape itself, and repo code can build one; the candidate run is what refuses a wrong
    # fix. The exception is the test's own object: read only values that are exactly str (str(exc)
    # or os.fspath would run its code), before anything else; the os functions and str are bound
    # at import (as _Reporter's helpers) so a test module cannot rebind them; any error records
    # nothing.
    try:
        exc = call.excinfo.value.__cause__
        if not (isinstance(node, pytest.Module) and report.when == "collect"
                and type(exc) is ImportError):
            return None
        args, name, path = exc.args, exc.name, exc.path
        if not (type(name) is str and type(path) is str and type(args) is tuple
                and len(args) == 1 and type(args[0]) is str):
            return None
        text, last = args[0], _last(exc)
        origin = _rel(_real(path), _real(node.config.rootpath))
        if ("cannot import name" in text and "partially initialized" not in text
                and _real(last) == _real(_str(node.path))
                and not origin.startswith("..") and not _abs(origin)):
            return {"node": node.nodeid, "module": name, "origin": origin}
    except BaseException:
        return None
    return None


def _denied_exc(info, explicit=False, _sub=issubclass, _type=type, _base=BaseException,
                _errno=OSError.errno.__get__, _file=OSError.filename.__get__,
                _cause=BaseException.__cause__.__get__, _ctx=BaseException.__context__.__get__,
                _quiet=BaseException.__suppress_context__.__get__):
    # #962: the first EPERM OSError (a Seatbelt denial's errno; EACCES is plain file permissions)
    # with a str filename in the cause and context chain (at most 64 links) of a phase's exception
    # info (None for a strict xpass); None when there is none. #1006 (Codex r1): read through
    # type() and the built-in descriptors, never the error's own attributes, so a test's OSError
    # subclass (properties, __eq__, __bool__) runs no code here. explicit (#1030 r1): follow only
    # __cause__ (raise ... from), never the implicit __context__ of an error raised while handling.
    exc = getattr(info, "value", None)
    for _ in range(64):
        if not _sub(_type(exc), _base):
            return None
        if _sub(_type(exc), OSError) and _type(_errno(exc)) is int and _errno(exc) == 1:
            if _type(_file(exc)) is str:
                return exc
        cause = _cause(exc)
        exc = cause if cause is not None else (None if explicit or _quiet(exc) else _ctx(exc))
    return None


def _denied_path(info, _exc=_denied_exc, _file=OSError.filename.__get__):
    # #962: that error's path, or None. Wording only: the parent trusts nothing about it. Read
    # once and checked again (#1006, Claude): the record must stay JSON.
    exc = _exc(info)
    path = None if exc is None else _file(exc)
    return path if type(path) is str else None


def _checkout_rel(code, top, _real=os.path.realpath, _rel=os.path.relpath, _abs=os.path.isabs):
    # #962 step 2a: the frame's file relative to the checkout top, or None when it is not an
    # absolute file inside it (realpath only to find it).
    if not _abs(code.co_filename) or code.co_filename.startswith("<"):
        return None
    rel = _rel(_real(code.co_filename), top)
    out = rel in (".", "..") or rel.startswith(("../", "..\\\\"))
    return None if out or _abs(rel) else rel


def _checkout_frame(tb, top, _in=_checkout_rel):
    # (origin, line) of a traceback's innermost checkout frame; "" when that frame is a lambda or
    # a comprehension (a <...> name), or none is in the checkout.
    origin, line = "", 0
    while tb is not None:
        code = tb.tb_frame.f_code
        if _in(code, top) is not None:
            origin = "" if code.co_name.startswith("<") else _in(code, top)
            line = tb.tb_lineno or 0
        tb = tb.tb_next
    return origin, line


def _denied_origin(info, root, _exc=_denied_exc, _real=os.path.realpath, _str=str,
                   _file=OSError.filename.__get__, _tb=BaseException.__traceback__.__get__,
                   _cause=BaseException.__cause__.__get__, _sub=issubclass, _type=type,
                   _base=BaseException, _frame=_checkout_frame, _in=_checkout_rel):
    # #962 step 2a: [denied path, origin, line] of a setup-phase denial, or None; the origin is
    # the denied error's own innermost checkout frame (_checkout_frame, realpath only to find
    # it). Data for candidate_env.py, which reads the control tree's copy of that line; any error
    # records nothing. #1030 r1: the error setup raised, or one it names with raise ... from; a
    # denial a fixture caught (only the __context__ of a later error) records nothing. #1031: nor
    # one whose traceback holds a checkout frame twice (caught, then re-raised from code the
    # handler called; pytest's and pluggy's own re-raises are outside the checkout), nor one
    # wrapped (raise ... from) by an error raised outside the origin's file.
    try:
        exc = _exc(info, True)
        if exc is None:
            return None
        top, seen, tb = _real(_str(root)), set(), _tb(exc)
        while tb is not None:
            if _in(tb.tb_frame.f_code, top) is not None:
                if id(tb.tb_frame) in seen:
                    return None
                seen.add(id(tb.tb_frame))
            tb = tb.tb_next
        origin, line = _frame(_tb(exc), top)
        outer = getattr(info, "value", None)
        for _ in range(64):
            if outer is exc:
                break
            if not _sub(_type(outer), _base) or _frame(_tb(outer), top)[0] != origin:
                return None
            outer = _cause(outer)
        else:
            return None
        path = _file(exc)
        return [path, origin, line] if type(path) is str else None
    except BaseException:
        return None


def _submodule_error(node, call, report, _real=os.path.realpath, _rel=os.path.relpath,
                     _abs=os.path.isabs, _str=str, _last=_innermost, _base=os.path.basename,
                     _mods=sys.modules, _get=dict.get, _vars=vars, _mod=ModuleType):
    # #796 S3: "No module named 'pkg.newmod'" from a test module's own import statement, where
    # the parent package pkg is a regular package already loaded from an __init__.py in the
    # checkout. A HINT like _import_error, reachable by base code and tests: read values only
    # after an exact-type check, and the parent only through its module dict (getattr could run
    # a module __getattr__ in repo code); no find_spec, no import; any error records nothing. A
    # dict lookup can still call a colliding key's __eq__ (test code): a hint, not evidence.
    try:
        exc = call.excinfo.value.__cause__
        if not (isinstance(node, pytest.Module) and report.when == "collect"
                and type(exc) is ModuleNotFoundError):
            return None
        args, name, path = exc.args, exc.name, exc.path
        if not (type(name) is str and "." in name and path is None and type(args) is tuple
                and len(args) == 1 and type(args[0]) is str
                and args[0] == "No module named '" + name + "'"):
            return None
        parent = _get(_mods, name.rpartition(".")[0])
        file = _get(_vars(parent), "__file__") if type(parent) is _mod else None
        if not (type(file) is str and _base(file) == "__init__.py"
                and _real(_last(exc)) == _real(_str(node.path))):
            return None
        origin = _rel(_real(file), _real(node.config.rootpath))
        if not origin.startswith("..") and not _abs(origin):
            return {"node": node.nodeid, "module": name, "parent_origin": origin}
    except BaseException:
        return None
    return None


def _collect_hints(node, call, report, hints, _imp=_import_error, _sub=_submodule_error):
    hints["collect_import_errors"] += filter(None, [_imp(node, call, report)])
    hints["collect_missing_submodules"] += filter(None, [_sub(node, call, report)])


def _source(item):
    try:
        return item.path.relative_to(item.config.rootpath).as_posix()
    except (AttributeError, TypeError, ValueError):
        return ""


def _missing(report):  # #813: the module a skip at collection says it could not import
    shown = report.longrepr[-1] if isinstance(report.longrepr, tuple) else ""
    return str(shown).partition("could not import '")[2].partition("'")[0][:100]
"""
