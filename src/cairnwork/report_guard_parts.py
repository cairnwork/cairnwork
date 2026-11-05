"""Parts of the report_guard plugin source kept out of report_guard.py (near the 400-line cap):
the fixture table (a pure move) and the import-error recorder. Plugin text, not parent code: the
plugin cannot import cairnwork."""

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


def _import_error(node, call, report):
    # #796: "cannot import name X from <module>" in a test module's own import statement. Reads
    # only the exception (no import: that would run repo code); any error records nothing.
    try:
        exc = call.excinfo.value.__cause__
        if not (isinstance(node, pytest.Module) and report.when == "collect"
                and type(exc) is ImportError):
            return None
        text, tb, last = str(exc), exc.__traceback__, ""
        while tb is not None:  # the innermost frame that is not the import machinery
            name = tb.tb_frame.f_code.co_filename
            last = last if name.startswith("<frozen importlib") else name
            tb = tb.tb_next
        real = os.path.realpath
        origin = os.path.relpath(real(exc.path), real(node.config.rootpath))
        if ("cannot import name" in text and "partially initialized" not in text
                and isinstance(exc.name, str) and real(last) == real(str(node.path))
                and not origin.startswith("..") and not os.path.isabs(origin)):
            return {"node": node.nodeid, "module": exc.name, "origin": origin}
    except Exception:
        return None
    return None
"""
