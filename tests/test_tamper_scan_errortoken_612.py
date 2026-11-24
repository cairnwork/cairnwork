"""#612: on Python 3.11 a space or tab before an identifier character the tokenizer reports as an
error token (not only at line start) must not make the whole file unreadable."""

import sys

import pytest

from cairnwork.tamper_scan_tokens import file_views

CHARS = ["ᢅ", "ᢆ", "℘", "℮"]
SHAPES = [
    "x = {c}\n",
    "f(a, {c})\n",
    "def g():\n    return {c}\n",
    "import {c}\n",
    "x = 1 if True else {c}\n",
    "\t{c} = 1\n",
    "{c} = 1\n",
]


@pytest.mark.parametrize("char", CHARS)
@pytest.mark.parametrize("shape", SHAPES)
def test_an_identifier_character_after_a_space_still_has_views(shape, char):
    source = shape.format(c=char)
    assert file_views(source.encode()) is not None


AFTER_OPERAND = [
    "f(a) {c}\n",
    "x[0] {c}\n",
    "d = {{}} {c}\n",
    "x = 1 {c}\n",
    "a {c}\n",
    "x = 'a' {c}\n",
]


@pytest.mark.parametrize("char", CHARS)
@pytest.mark.parametrize("shape", AFTER_OPERAND)
def test_an_identifier_character_right_after_an_operand_stays_unreadable(shape, char):
    source = shape.format(c=char)
    if sys.version_info < (3, 12):
        assert file_views(source.encode()) is None
    else:
        with pytest.raises(SyntaxError):
            compile(source, "<t>", "exec")
