"""#851 step 0: one table of plugin fixture names; attribution is a subset of selection and the
guard's plugin text is rendered from it."""

from cairnwork import plugin_names, report_guard, report_guard_parts
from cairnwork.pytest_config import PLUGINS


def test_attribution_is_a_subset_of_selection():
    selection, attribution = plugin_names.selection_names(), plugin_names.attribution_names()
    assert attribution.items() <= selection.items()
    assert {"db", "client"} <= selection.keys() and not {"db", "client"} & attribution.keys()


def test_table_rows_are_pytest_config_plugin_modules():
    assert set(plugin_names.PLUGIN_NAMES) <= {module for _, module, _ in PLUGINS}


def test_guard_plugin_text_is_rendered_from_the_table():
    assert plugin_names.fixtures_source() in report_guard_parts.PARTS_SOURCE
    namespace: dict = {}
    exec(plugin_names.fixtures_source(), namespace)  # noqa: S102 - our own rendered literal
    assert namespace["_PLUGIN_FIXTURES"] == plugin_names.attribution_names()
    assert plugin_names.fixtures_source() in report_guard._PLUGIN_SOURCE


_PYTEST_MOCK = "mocker class_mocker module_mocker package_mocker session_mocker".split()
_PYTEST_DJANGO = (
    "transactional_db django_db_setup django_db_blocker admin_client admin_user rf async_rf "
    "live_server django_user_model django_assert_num_queries mailoutbox"
).split()


def test_attribution_and_selection_names_are_pinned():
    """The full name -> plugin map as literals: dropping a name or moving one to another plugin
    must turn this red (the table is what the guard attributes setup errors with)."""
    expected_attribution = {
        **dict.fromkeys(_PYTEST_MOCK, "pytest_mock"),
        **dict.fromkeys(_PYTEST_DJANGO, "pytest_django.plugin"),
        "anyio_backend": "anyio.pytest_plugin",
    }
    assert plugin_names.attribution_names() == expected_attribution
    # selection = attribution plus the names a project often defines itself (never attributed)
    assert plugin_names.selection_names() == {
        **expected_attribution,
        "db": "pytest_django.plugin",
        "client": "pytest_django.plugin",
        "settings": "pytest_django.plugin",
    }
    assert sorted(plugin_names.PLUGIN_NAMES) == [
        "anyio.pytest_plugin",
        "pytest_django.plugin",
        "pytest_mock",
    ]


def test_rendered_plugin_text_entries_are_pinned():
    rendered = plugin_names.fixtures_source()
    for name, module in plugin_names.attribution_names().items():
        assert f'"{name}"' in rendered and f'"{module}"' in rendered
    assert rendered.startswith("_PLUGIN_FIXTURES = {\n") and rendered.endswith("\n}\n")
    assert "# pytest-django: only names a project rarely defines itself" in rendered
