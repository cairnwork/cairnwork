"""The xdist group list stays true to the files (#406)."""

import re
from pathlib import Path

from xdist_groups import XDIST_GROUP_OF, XDIST_GROUPS

TESTS = Path(__file__).parent


def test_every_listed_file_exists_and_is_in_one_group():
    names = [name for group in XDIST_GROUPS.values() for name in group]
    assert len(names) == len(set(names)) == len(XDIST_GROUP_OF)
    assert [name for name in names if not (TESTS / name).is_file()] == []


def test_every_file_on_a_shared_run_tag_is_in_its_group():
    """A new test file that runs the wrapper on a make_runs() root must join the group."""
    uses = re.compile(r"\b(make_runs|run_wrapper)\b")
    users = {
        path.name
        for path in TESTS.glob("test_*.py")
        if path.name != Path(__file__).name and uses.search(path.read_text())
    }
    assert users and users == set(XDIST_GROUPS["local_worker_run_tag"])


def test_conftest_marks_the_listed_files_only(request):
    """The conftest hook adds xdist_group(<group>) to a listed file's tests, nothing elsewhere."""
    (conftest,) = [
        plugin
        for plugin in request.config.pluginmanager.get_plugins()
        if getattr(plugin, "__file__", None) == str(TESTS / "conftest.py")
    ]

    class Item:
        def __init__(self, name):
            self.path = TESTS / name
            self.marks = []

        def add_marker(self, mark):
            self.marks.append(mark)

    listed, other = Item("test_local_worker_wrapper.py"), Item(Path(__file__).name)
    conftest.pytest_itemcollected(listed)
    conftest.pytest_itemcollected(other)
    assert [(m.name, m.args) for m in listed.marks] == [("xdist_group", ("local_worker_run_tag",))]
    assert other.marks == []
