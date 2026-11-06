"""Parts of the report_guard plugin source kept out of report_guard.py (near the 400-line cap):
the fixture table, _source and _missing (pure moves) and the import-error recorder. Plugin text,
not parent code: the plugin cannot import cairnwork."""

PARTS_SOURCE = """\
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


def _import_error(node, call, report, _real=os.path.realpath, _rel=os.path.relpath,
                  _abs=os.path.isabs, _str=str):
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
        text, tb, last = args[0], exc.__traceback__, ""
        while tb is not None:  # the innermost frame that is not the import machinery
            file = tb.tb_frame.f_code.co_filename
            last = last if file.startswith("<frozen importlib") else file
            tb = tb.tb_next
        origin = _rel(_real(path), _real(node.config.rootpath))
        if ("cannot import name" in text and "partially initialized" not in text
                and _real(last) == _real(_str(node.path))
                and not origin.startswith("..") and not _abs(origin)):
            return {"node": node.nodeid, "module": name, "origin": origin}
    except BaseException:
        return None
    return None


def _source(item):
    try:
        return item.path.relative_to(item.config.rootpath).as_posix()
    except (AttributeError, TypeError, ValueError):
        return ""


def _missing(report):  # #813: the module a skip at collection says it could not import
    shown = report.longrepr[-1] if isinstance(report.longrepr, tuple) else ""
    return str(shown).partition("could not import '")[2].partition("'")[0][:100]
"""
