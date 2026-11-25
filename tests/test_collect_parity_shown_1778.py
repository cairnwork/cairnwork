"""#1778: a base-tree path reaches CI-log reason text only after control characters, line
breaks and the record delimiters are replaced, so no line can start with ``::``."""

import pytest

from cairnwork.collect_parity import _shown

HIDDEN = ["\n", "\r", "\x1b", "\x00", "\x7f", "\x85", " ", " ", "\t"]


@pytest.mark.parametrize("char", HIDDEN)
def test_hidden_character_never_reaches_the_reason(char):
    out = _shown(f"tests/a{char}::error::x.py")
    assert char not in out
    assert "::" not in out and out.isprintable()
    assert out.startswith("tests/a") and out.endswith("x.py")


def test_a_path_is_cut_at_80_characters():
    assert len(_shown("tests/" + "a" * 200 + ".py")) == 80


def test_plain_paths_are_unchanged():
    for path in ("tests/test_a.py", "tests/sub dir/test_b.py"):
        assert _shown(path) == path
