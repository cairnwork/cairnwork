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
