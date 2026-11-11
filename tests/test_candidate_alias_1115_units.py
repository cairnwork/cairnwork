"""Direct unit tests for candidate_alias pure functions (#1115)."""

from __future__ import annotations

import pytest

from cairnwork.candidate_alias import alias_machinery_changed, runtime_shadow_added


def test_alias_machinery_changed_added_swap():
    assert alias_machinery_changed(None, b"import sys as s\ns.modules['a'] = 1\n") is True


def test_alias_machinery_changed_unchanged_file():
    assert alias_machinery_changed(b"x = 1\n", b"x = 1\n") is False


def test_runtime_shadow_added_open_write_and_path_insert():
    src = b"import sys\nopen('f', 'w').write('x')\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is True


def test_runtime_shadow_added_open_default_read_mode_is_false():
    src = b"import sys\nopen('f')\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is False


def test_runtime_shadow_added_open_non_literal_mode_is_true():
    src = b"import sys\nopen('f', mode)\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is True


def test_unparseable_new_raises_value_error():
    with pytest.raises(ValueError):
        alias_machinery_changed(None, b"def (syntax error")
    with pytest.raises(ValueError):
        runtime_shadow_added(None, b"def (syntax error")
