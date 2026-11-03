"""Text cleaning by Unicode category in five places (#740): zero-width, direction and BOM
characters are replaced, delimiters and plain text (CJK, accents) are kept as before."""

from __future__ import annotations

import pytest

from cairnwork import gitcmd, issue_runner, p2p_probe_log, reasons
from cairnwork.textclean import is_hidden, replace_hidden
from cairnwork_attest.cli import _printable

HIDDEN = ["​", "؜", "‮", "﻿"]
PLAIN = "說明 café ok"


@pytest.mark.parametrize("ch", [*HIDDEN, "\x00", " ", " ", "", "͸", "\ud800"])
def test_helper_hides_c_star_and_line_separators(ch):
    assert is_hidden(ch)
    assert replace_hidden(f"a{ch}b") == "a?b"


def test_helper_keeps_plain_text_and_the_kept_characters():
    assert replace_hidden(PLAIN) == PLAIN
    assert replace_hidden("a\n\tb\x00", keep="\n\t") == "a\n\tb?"
    assert replace_hidden("a​b", "") == "ab"


@pytest.mark.parametrize("ch", [*HIDDEN, "\x1b"])
def test_reasons_names_replace_hidden(ch):
    assert reasons._names([f"a{ch}b"]) == "1: a?b"


def test_reasons_names_keep_delimiter_rule_and_plain_text():
    assert reasons._names(["a[b];c:d"]) == "1: a?b??c?d"
    assert reasons._names([PLAIN]) == f"1: {PLAIN}"


@pytest.mark.parametrize("ch", [*HIDDEN, "\x1b"])
def test_probe_log_tail_replaces_hidden(tmp_path, ch):
    log = tmp_path / "log"
    log.write_text(f"x{ch}y\n", encoding="utf-8")
    assert p2p_probe_log.probe_log_tail(log) == "x?y\n"


def test_probe_log_tail_keeps_newline_tab_and_plain_text(tmp_path):
    log = tmp_path / "log"
    log.write_text(f"{PLAIN}\t1\n2\n", encoding="utf-8")
    assert p2p_probe_log.probe_log_tail(log) == f"{PLAIN}\t1\n2\n"


@pytest.mark.parametrize("ch", [*HIDDEN, "\x1b"])
def test_stderr_tail_drops_hidden(ch):
    assert gitcmd.stderr_tail(f"fa{ch}tal") == "fatal"


def test_stderr_tail_keeps_plain_text_and_joins_lines():
    assert gitcmd.stderr_tail(f"{PLAIN} x\nnext") == f"{PLAIN} x | next"


@pytest.mark.parametrize(
    ("ch", "shown"),
    [
        ("​", "\\u200b"),
        ("؜", "\\u061c"),
        ("‮", "\\u202e"),
        ("﻿", "\\ufeff"),
        ("\x1b", "\\x1b"),
        (" ", "\\u2028"),
    ],
)
def test_attest_printable_escapes_hidden(ch, shown):
    assert _printable(f"a{ch}b") == f"a{shown}b"


def test_attest_printable_keeps_plain_text_and_surrogate_escape():
    assert _printable(PLAIN) == PLAIN
    assert _printable("a\udc80b") == "a\\udc80b"


@pytest.mark.parametrize("ch", [*HIDDEN, "\x00"])
def test_unfence_replaces_hidden_in_single_line_fields(ch):
    # Cf is dropped before the replacement, the other control characters become '?'
    expected = "ab" if ch in HIDDEN else "a?b"
    assert issue_runner._unfence(f"a{ch}b", multiline=False) == expected


def test_unfence_separators_and_newline_rules():
    assert issue_runner._unfence("a b c", multiline=False) == "a?b?c"
    assert issue_runner._unfence("a\nb", multiline=False) == "a?b"
    assert issue_runner._unfence("a\n\tb\x00", multiline=True) == "a\n\tb?"
    assert issue_runner._unfence(PLAIN, multiline=False) == PLAIN
