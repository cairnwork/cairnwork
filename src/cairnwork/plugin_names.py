"""The one table of pytest plugins' fixture names (#851 step 0).

Rows are keyed by the pytest_config.PLUGINS module. ``selection_names`` is the conservative
superset (it includes names a project often defines itself, such as db and client); no code
reads it yet. ``attribution_names`` is exact: a setup error naming one of them is recorded as
``plugin:<module>`` (#804, report_guard_parts renders it into the plugin text). Every
attribution name is also a selection name.
"""

from __future__ import annotations

import textwrap

_MOCKER = ("mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker")
_DJANGO = (
    "transactional_db", "django_db_setup", "django_db_blocker", "admin_client", "admin_user",
    "rf", "async_rf", "live_server", "django_user_model", "django_assert_num_queries",
    "mailoutbox",
)  # fmt: skip

# module -> (selection_names, attribution_names, comment shown above its plugin-text entry)
PLUGIN_NAMES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "pytest_mock": (_MOCKER, _MOCKER, ""),
    "pytest_django.plugin": (
        (*_DJANGO, "db", "client", "settings"),
        _DJANGO,
        "pytest-django: only names a project rarely defines itself (not db, client or settings)",
    ),
    "anyio.pytest_plugin": (("anyio_backend",), ("anyio_backend",), ""),
}


def selection_names() -> dict[str, str]:
    return {name: mod for mod, (names, _, _) in PLUGIN_NAMES.items() for name in names}


def attribution_names() -> dict[str, str]:
    return {name: mod for mod, (_, names, _) in PLUGIN_NAMES.items() for name in names}


def fixtures_source() -> str:
    """The ``_PLUGIN_FIXTURES = {...}`` literal for the plugin text (attribution names)."""
    lines = ["_PLUGIN_FIXTURES = {"]
    for mod, (_, names, comment) in PLUGIN_NAMES.items():
        if comment:
            lines.append(f"    # {comment}")
        if len(names) == 1:
            lines.append(f'    "{names[0]}": "{mod}",')
            continue
        body = ", ".join(f'"{n}"' for n in names) + f'),\0"{mod}"),'  # \0: never split here
        head = "    **dict.fromkeys(("
        wrapped = textwrap.wrap(
            body, width=100, initial_indent=head, subsequent_indent=" " * 21,
            break_long_words=False, break_on_hyphens=False,
        )  # fmt: skip
        lines.extend(line.replace("\0", " ") for line in wrapped)
    return "\n".join(lines) + "\n}\n"
