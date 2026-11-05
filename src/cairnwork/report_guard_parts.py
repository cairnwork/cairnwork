"""Parts of the report_guard plugin source kept out of report_guard.py (near the 400-line cap):
the fixture table (a pure move). Plugin text, not parent code: the
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
"""
