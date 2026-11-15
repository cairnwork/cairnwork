"""Lock each detection rule of candidate_alias with a parametrised case (#1115 follow-up)."""

from __future__ import annotations

import pytest

from cairnwork.candidate_alias import alias_machinery_changed, runtime_shadow_added

_PATH_EDIT = b"sys.path.insert(0, 'd')\n"
_SYS = b"import sys\n"


@pytest.mark.parametrize("table", ["modules", "meta_path", "path_hooks", "path_importer_cache"])
def test_every_import_table_counts_as_machinery(table):
    source = f"import sys\nsys.{table}.append(x)\n".encode()
    assert alias_machinery_changed(None, source) is True


@pytest.mark.parametrize(
    "call",
    [
        "write_text('a')",
        "write_bytes(b'a')",
        "copyfile(a, b)",
        "copy(a, b)",
        "copy2(a, b)",
        "copytree(a, b)",
        "move(a, b)",
        "rename(a, b)",
        "replace(a, b)",
        "symlink(a, b)",
        "link(a, b)",
        "extract(a)",
        "extractall(a)",
    ],
)
def test_every_write_call_then_path_edit_is_a_shadow(call):
    assert runtime_shadow_added(None, _SYS + f"x.{call}\n".encode() + _PATH_EDIT) is True


@pytest.mark.parametrize(
    "edit",
    [
        "import os\nos.environ['PYTHONPATH'] = 'd'\n",
        "import os\nos.environ['pythonpath'] = 'd'\n",
        "import site\nsite.addsitedir('d')\n",
    ],
)
def test_path_words_without_sys_path_count_as_path_edit(edit):
    assert runtime_shadow_added(None, ("open('f', 'w')\n" + edit).encode()) is True


@pytest.mark.parametrize(
    "source",
    [
        "from os import sys as s\ns.modules['x'] = 1\n",
        "import sys\nb = a\na = sys\nb.modules['x'] = 1\n",
        "import sys\n(m := sys)\nm.modules['x'] = 1\n",
        "import sys\ndef f(m=sys, /):\n    m.modules['x'] = 1\n",
        "import sys\ndef f(*, m=sys):\n    m.modules['x'] = 1\n",
    ],
)
def test_sys_alias_forms_reach_the_tables(source):
    assert alias_machinery_changed(None, source.encode()) is True


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("import sys\n(t := sys.modules)\n", "import sys\n(t := sys.modules)\nt['x'] = 1\n"),
        ("import sys\nt = sys.modules\n", "import sys\nt = sys.modules\nt['x'] = 1\n"),
        (
            "import sys\nt = sys.modules\nu = t\n",
            "import sys\nt = sys.modules\nu = t\nu['x'] = 1\n",
        ),
        (
            "from sys import modules as m\n",
            "from sys import modules as m\nm['x'] = 1\n",
        ),
    ],
)
def test_table_alias_added_line_is_machinery_change(old, new):
    assert alias_machinery_changed(old.encode(), new.encode()) is True


@pytest.mark.parametrize(
    "opener",
    [
        "g.open('rb.bin', 'w')",
        "open('f', mode='w')",
        "open('f', 'x')",
        "open('f', 'r+')",
    ],
)
def test_write_modes_are_read_in_every_position(opener):
    assert runtime_shadow_added(None, _SYS + f"{opener}\n".encode() + _PATH_EDIT) is True


@pytest.mark.parametrize("opener", ["open('f', mode='r')", "open('f', 'rb')"])
def test_read_modes_stay_allowed(opener):
    assert runtime_shadow_added(None, _SYS + f"{opener}\n".encode() + _PATH_EDIT) is False


def test_aliased_sys_path_edit_after_write_is_a_shadow():
    source = b"import sys as s\nopen('f', 'w')\ns.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, source) is True
