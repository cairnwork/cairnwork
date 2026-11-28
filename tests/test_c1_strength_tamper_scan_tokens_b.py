"""Second batch of strength pins for tamper_scan_tokens (survivors the first batch left).

Token-level helpers are driven with hand-built tokens so the pins hold on every Python version
(the 3.11 tokenizer emits ERRORTOKEN for some identifier characters; later ones do not).
"""

import subprocess
import sys
import tokenize

import pytest

from cairnwork import tamper_scan_tokens as scanner
from cairnwork.tamper_scan_tokens import (
    _blank_before_identifier,
    _prefix,
    _repair_identifier_errors,
    added_file,
    file_views,
)

CONT = "·"  # MIDDLE DOT: an identifier continuation character, never a start


def tok(kind, text, start, end=None):
    end = end or (start[0], start[1] + len(text))
    return tokenize.TokenInfo(kind, text, start, end, "")


def _probe(monkeypatch, *, returncode=0, stdout="3 11\n", version=(3, 11, 5)):
    class Result:
        def __init__(self, code: int, text: str) -> None:
            self.returncode = code
            self.stdout = text

    result = Result(returncode, stdout)
    monkeypatch.setattr(scanner.subprocess, "run", lambda *a, **k: result)
    monkeypatch.setattr(scanner.sys, "version_info", version)


# ---- views_trusted (L84 fullmatch, rc, L85 >=) --------------------------------------------


@pytest.mark.parametrize(
    "stdout", ["3 11 extra\n", "3 11\nx", "3 11\n\n", "3 11x", " 3 11\n", "3 11\r\n", "x"]
)
def test_views_trusted_needs_the_whole_probe_output_to_be_a_version(stdout, monkeypatch):
    # A target as new as sys.version_info makes any prefix match "trusted", so only a full
    # match of the output may let it through (pinned far above any real interpreter).
    _probe(monkeypatch, stdout=stdout, version=(99, 99, 0))
    assert scanner.views_trusted("py") is False


@pytest.mark.parametrize("stdout", ["3 11\n", "3 11", "3 1\n", "03 011\n"])
def test_views_trusted_accepts_exact_version_output(stdout, monkeypatch):
    _probe(monkeypatch, stdout=stdout, version=(99, 99, 0))
    assert scanner.views_trusted("py") is True


@pytest.mark.parametrize("returncode", [1, 2, -1, 127])
def test_views_trusted_rejects_any_nonzero_exit(returncode, monkeypatch):
    _probe(monkeypatch, returncode=returncode, stdout="3 11\n", version=(99, 99, 0))
    assert scanner.views_trusted("py") is False


@pytest.mark.parametrize(
    ("running", "target", "expected"),
    [
        ((3, 11, 0), "3 11\n", True),  # equal version is trusted (>=, not >)
        ((3, 11, 9), "3 11\n", True),
        ((3, 12, 0), "3 11\n", True),
        ((4, 0, 0), "3 99\n", True),
        ((3, 11, 9), "3 12\n", False),
        ((3, 10, 9), "3 11\n", False),
        ((2, 99, 0), "3 0\n", False),
    ],
)
def test_views_trusted_compares_major_minor_only(running, target, expected, monkeypatch):
    _probe(monkeypatch, stdout=target, version=running)
    assert scanner.views_trusted("py") is expected


def test_views_trusted_probe_call_shape(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "3 11\n"

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(scanner.subprocess, "run", fake_run)
    monkeypatch.setattr(scanner.sys, "version_info", (3, 11, 0))
    assert scanner.views_trusted("PY") is True
    assert seen["argv"][0] == "PY"
    assert seen["argv"][1:5] == ["-I", "-S", "-B", "-c"]
    assert seen["argv"][5] == "import sys; print(*sys.version_info[:2])"
    assert len(seen["argv"]) == 6
    assert seen["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": scanner._PROBE_SECONDS,
        "check": False,
        "env": {},
        "cwd": "/",
        "stdin": subprocess.DEVNULL,
    }


@pytest.mark.parametrize(
    "error",
    [
        OSError("x"),
        subprocess.TimeoutExpired("py", 1),
        subprocess.SubprocessError("x"),
        ValueError("x"),
    ],
)
def test_views_trusted_probe_errors_are_untrusted(error, monkeypatch):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(scanner.subprocess, "run", boom)
    monkeypatch.setattr(scanner.sys, "version_info", (99, 99, 0))
    assert scanner.views_trusted("py") is False


# ---- _repair_identifier_errors: next_name / start / continuation --------------------------


def _kinds(tokens):
    return [t.type for t in tokens]


def test_repair_next_name_makes_a_continuation_only_char_a_name():
    err = tok(tokenize.ERRORTOKEN, CONT, (1, 0))
    name = tok(tokenize.NAME, "b", (1, 1))
    end = tok(tokenize.NEWLINE, "\n", (1, 2))
    out = _repair_identifier_errors([err, name, end])
    assert out is not None
    assert _kinds(out) == [tokenize.NAME, tokenize.NAME, tokenize.NEWLINE]
    # same, with the error token in the middle of the stream
    paren = tok(tokenize.OP, "(", (1, 0))
    err2 = tok(tokenize.ERRORTOKEN, CONT, (1, 1))
    name2 = tok(tokenize.NAME, "b", (1, 2))
    end2 = tok(tokenize.NEWLINE, "\n", (1, 3))
    out2 = _repair_identifier_errors([paren, err2, name2, end2])
    assert out2 is not None
    assert _kinds(out2) == [tokenize.OP, tokenize.NAME, tokenize.NAME, tokenize.NEWLINE]


def test_repair_next_name_two_tokens_only():
    out = _repair_identifier_errors(
        [tok(tokenize.ERRORTOKEN, CONT, (1, 0)), tok(tokenize.NAME, "b", (1, 1))]
    )
    assert out is not None
    assert _kinds(out) == [tokenize.NAME, tokenize.NAME]


def test_repair_next_name_needs_a_following_token():
    assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, CONT, (1, 0))]) is None
    assert (
        _repair_identifier_errors(
            [tok(tokenize.OP, "(", (1, 0)), tok(tokenize.ERRORTOKEN, CONT, (1, 1))]
        )
        is None
    )


def test_repair_next_name_needs_a_name_directly_after():
    err = tok(tokenize.ERRORTOKEN, CONT, (1, 0))
    for follow in (
        tok(tokenize.OP, "+", (1, 1)),  # not a NAME
        tok(tokenize.NUMBER, "1", (1, 1)),
        tok(tokenize.NAME, "b", (1, 2)),  # a NAME, but not touching
    ):
        assert _repair_identifier_errors([err, follow, tok(tokenize.NEWLINE, "\n", (1, 3))]) is None
    # the token before or two after must not stand in for the next one
    name_before = tok(tokenize.NAME, "z", (1, 5))
    assert (
        _repair_identifier_errors(
            [
                name_before,
                tok(tokenize.OP, "+", (1, 6)),
                err._replace(start=(1, 7), end=(1, 8)),
                tok(tokenize.OP, "-", (1, 8)),
                tok(tokenize.NAME, "q", (1, 9)),
            ]
        )
        is None
    )


def test_repair_prior_name_needs_a_touching_name():
    prior = tok(tokenize.NAME, "a", (1, 0))
    err = tok(tokenize.ERRORTOKEN, CONT, (1, 1))
    out = _repair_identifier_errors([prior, err])
    assert out is not None
    assert _kinds(out) == [tokenize.NAME, tokenize.NAME]
    gap = tok(tokenize.ERRORTOKEN, CONT, (1, 2))
    assert _repair_identifier_errors([prior, gap]) is None
    assert _repair_identifier_errors([tok(tokenize.OP, "+", (1, 0)), err]) is None


def test_repair_start_char_alone_and_multichar_texts():
    assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, "x", (1, 0))]) is not None
    # a multi-character error text: start char plus continuation characters
    out = _repair_identifier_errors([tok(tokenize.ERRORTOKEN, "x" + CONT, (1, 0))])
    assert out is not None
    assert _kinds(out) == [tokenize.NAME]
    # a continuation-only first char alone is neither start, prior nor next name
    assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, CONT + "x", (1, 0))]) is None
    # any non-identifier character anywhere in the text refuses
    for text in ("x$", "$x", "x$y", "$", "`"):
        assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, text, (1, 0))]) is None
    for text in ("xy" + CONT, "xyz"):
        assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, text, (1, 0))]) is not None


def test_repair_start_needs_an_identifier_start_char():
    # a digit continues an identifier but cannot start one; nothing touches it
    assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, "1", (1, 0))]) is None
    assert _repair_identifier_errors([tok(tokenize.ERRORTOKEN, "", (1, 0))]) is None
    # an empty text is not a start (no IndexError, no repair): needs a touching name to pass
    touching = [tok(tokenize.NAME, "a", (1, 0)), tok(tokenize.ERRORTOKEN, "", (1, 1))]
    out = _repair_identifier_errors(touching)
    assert out is not None
    assert _kinds(out) == [tokenize.NAME, tokenize.NAME]


def test_repair_blank_before_identifier_is_dropped_and_other_blank_refused():
    tokens = [
        tok(tokenize.NAME, "x", (1, 0)),
        tok(tokenize.OP, "=", (1, 2)),
        tok(tokenize.ERRORTOKEN, " ", (1, 3)),
        tok(tokenize.ERRORTOKEN, "\t", (1, 4)),
        tok(tokenize.ERRORTOKEN, "℘", (1, 5)),
        tok(tokenize.NEWLINE, "\n", (1, 6)),
    ]
    out = _repair_identifier_errors(tokens)
    assert out is not None
    assert [t.string for t in out] == ["x", "=", "℘", "\n"]
    assert out[2].type == tokenize.NAME
    # a lone blank error token (nothing identifier-like after it) is refused
    lone = [tok(tokenize.NAME, "x", (1, 0)), tok(tokenize.ERRORTOKEN, " ", (1, 1))]
    assert _repair_identifier_errors(lone) is None


# ---- _blank_before_identifier -------------------------------------------------------------


def _blank_case(before, *, follow_kind: int | None = tokenize.ERRORTOKEN, gap=0, trailing=0):
    blank = tok(tokenize.ERRORTOKEN, " ", (1, 5))
    tokens = [blank]
    if follow_kind is not None:
        tokens.append(tok(follow_kind, "℘", (1, 6 + gap)))
    tokens.extend(tok(tokenize.NEWLINE, "\n", (1, 20 + i)) for i in range(trailing))
    return _blank_before_identifier(tokens, before, 0)


def test_blank_needs_a_following_token():
    assert _blank_case([], follow_kind=None) is False
    assert _blank_case([tok(tokenize.OP, "=", (1, 0))], follow_kind=None) is False


def test_blank_followed_by_error_token_when_not_the_last_token():
    # follow is the second-to-last token: a bound of index + 2 would lose it
    assert _blank_case([], trailing=1) is True
    assert _blank_case([], trailing=3) is True


def test_blank_follow_must_be_an_adjacent_error_token():
    assert _blank_case([], follow_kind=tokenize.NAME) is False  # touching, not an error token
    assert _blank_case([], follow_kind=tokenize.OP) is False
    assert _blank_case([], gap=1) is False  # an error token, but not touching
    assert _blank_case([], follow_kind=tokenize.NAME, gap=1) is False
    assert _blank_case([], follow_kind=tokenize.ERRORTOKEN) is True


@pytest.mark.parametrize("kind", [tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT])
def test_blank_after_line_structure_token_starts_an_identifier(kind):
    assert _blank_case([tok(kind, "", (1, 0))]) is True


def test_blank_with_nothing_before_it_starts_an_identifier():
    assert _blank_case([]) is True


def test_blank_after_name_depends_on_keyword():
    assert _blank_case([tok(tokenize.NAME, "return", (1, 0))]) is True
    assert _blank_case([tok(tokenize.NAME, "import", (1, 0))]) is True
    assert _blank_case([tok(tokenize.NAME, "value", (1, 0))]) is False


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        (tokenize.OP, "=", True),
        (tokenize.OP, "(", True),
        (tokenize.OP, ",", True),
        (tokenize.OP, ")", False),
        (tokenize.OP, "]", False),
        (tokenize.OP, "}", False),
        (tokenize.NUMBER, "1", False),
        (tokenize.STRING, "'s'", False),
    ],
)
def test_blank_after_operand_or_operator(kind, text, expected):
    assert _blank_case([tok(kind, text, (1, 0))]) is expected


# ---- _prefix (L191), added_file, file_views -----------------------------------------------


def test_prefix_is_empty_for_non_strings_and_the_literal_prefix_for_strings():
    assert _prefix(tok(tokenize.NAME, "rb", (1, 0))) == ""
    assert _prefix(tok(tokenize.OP, "=", (1, 0))) == ""
    assert _prefix(tok(tokenize.NUMBER, "12", (1, 0))) == ""
    assert _prefix(tok(tokenize.COMMENT, "# f'x'", (1, 0))) == ""
    assert _prefix(tok(tokenize.STRING, "'x'", (1, 0))) == ""
    assert _prefix(tok(tokenize.STRING, 'rb"x"', (1, 0))) == "rb"
    assert _prefix(tok(tokenize.STRING, "f'{1}'", (1, 0))) == "f"
    assert _prefix(tok(tokenize.STRING, "Rb'''x'''", (1, 0))) == "Rb"
    assert _prefix(tok(tokenize.STRING, "f'it\"s'", (1, 0))) == "f"
    assert _prefix(tok(tokenize.STRING, 'f"it\'s"', (1, 0))) == "f"


def test_added_file_validates_and_pads_line_numbers():
    assert added_file([]) is None
    assert added_file([(0, "bad")]) is None
    assert added_file([(-3, "bad"), (2, "ok")]) is None
    assert added_file([(2, "b"), (0, "bad")]) is None
    assert added_file([(1, "a")]) == b"a"
    assert added_file([(3, "c"), (1, "a")]) == b"a\n\nc"
    assert added_file([(2, "é℘")]) == "\né℘".encode()
    assert added_file([(1, "\ud800")]) is None  # lone surrogate: not encodable


def test_file_views_decodes_utf8_only_and_strips_a_bom():
    assert file_views(b"x = 1\n\xff\n") is None
    assert file_views("﻿x = 1\n".encode()) is not None
    assert file_views("﻿x = 1\n".encode())[(1, 0)].code == "x = 1"  # type: ignore[index]
    assert file_views("s = 'é;'\n".encode())[(1, 0)].code == "s = 'éx'"  # type: ignore[index]


def test_file_views_semicolon_and_hash_cases():
    src = "a = 1; b = '#;'; c = 2  # note; more #\n"
    views = file_views(src.encode())
    assert views is not None
    assert views[(1, 0)].code == "a = 1; b = 'xx'; c = 2  # note; more #"
    assert views[(1, 0)].bound == "a = 1; b = xxxx; c = 2  "
    assert views[(1, 0)].skip is False


def test_file_views_skip_only_when_trusted_and_no_code():
    src = "# only a comment\n'''doc\n# text; here\n'''\nx = 1\n"
    trusted = file_views(src.encode(), trusted=True)
    untrusted = file_views(src.encode(), trusted=False)
    assert trusted is not None and untrusted is not None
    assert [trusted[(n, 0)].skip for n in range(1, 6)] == [True, True, True, True, False]
    assert [untrusted[(n, 0)].skip for n in range(1, 6)] == [False] * 5
    assert trusted[(3, 0)].code == "x textx here"  # # and ; inside the literal are masked


def test_file_views_carriage_return_pieces_are_separate_lines():
    views = file_views(b"a = 1\rb = '#'\r\nc = 2;\n")
    assert views is not None
    assert views[(1, 0)].code == "a = 1"
    assert views[(1, 1)].code == "b = 'x'"
    assert views[(2, 0)].code == "c = 2;"
    assert set(views) == {(1, 0), (1, 1), (2, 0), (3, 0)}


def test_file_views_python311_repair_end_to_end():
    if sys.version_info[:2] != (3, 11):
        pytest.skip("ERRORTOKEN identifier repair is specific to Python 3.11")
    for source in ("℘ = 1\n", "x ℘ = 1\n"[:0] + "x = ℘\n", "f(a, ℘)\n"):
        views = file_views(source.encode())
        assert views is not None
        assert views[(1, 0)].code == source.rstrip("\n")
    assert file_views(b"x = $\n") is None
    assert file_views("1 ℘\n".encode()) is None
