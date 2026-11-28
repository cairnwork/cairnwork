"""Focused lexical-view pins for survivor mutations in tamper_scan_tokens."""

import ast

import pytest

from cairnwork.tamper_scan_tokens import _mask, added_file, file_views


def test_views_distinguish_comment_from_string_hash_and_semicolon():
    source = 'import os\nx = "#;"; os._exit(0) # tail\n'
    views = file_views(source.encode())

    assert views is not None
    assert views[(2, 0)].code == 'x = "xx"; os._exit(0) # tail'
    assert views[(2, 0)].bound == "x = xxxx; os._exit(0) "
    assert views[(2, 0)].skip is False
    assert views[(1, 0)].bound == "import os"


def test_multiline_literal_masks_only_its_span_and_bound_omits_literal_text():
    source = 'value = """alpha #;\ninside #; text\nend"""\nresult = 1\n'
    views = file_views(source.encode())

    assert views is not None
    assert views[(1, 0)].code == 'value = """alpha xx'
    assert views[(2, 0)].code == "inside xx text"
    assert views[(3, 0)].code == 'end"""'
    assert views[(1, 0)].bound == "value = " + "x" * len('"""alpha #;')
    assert views[(2, 0)].bound == ""
    assert views[(3, 0)].bound == ""
    assert views[(4, 0)].bound == "result = 1"


@pytest.mark.parametrize("source", ["℘ = 1\n", "ᢅ = 1\n", "x℘ = 1\n", "x = ℘\n"])
def test_python311_identifier_error_tokens_are_repaired(source):
    ast.parse(source)
    views = file_views(source.encode())

    assert views is not None
    assert views[(1, 0)].code == source.rstrip("\n")
    assert views[(1, 0)].bound == source.rstrip("\n")


def test_prefix_and_standin_file_preserve_their_real_inputs():
    views = file_views(b"f'{1 + 2}'\nr'value'\n")

    assert views is not None
    assert [views[(line, 0)].skip for line in (1, 2)] == [False, True]
    assert added_file([(1, "alpha"), (3, "omega")]) == b"alpha\n\nomega"


def test_mask_respects_multiline_token_start_and_end_columns():
    chars = [list("keep 'ab#"), list("#;cd' tail")]

    _mask(chars, (1, 5), (2, 5), frozenset("#;"))

    assert ["".join(row) for row in chars] == ["keep 'abx", "xxcd' tail"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("℘ = 1\n", "℘ = 1"),
        ("x℘ = 1\n", "x℘ = 1"),
        ("x = ℘\n", "x = ℘"),
        ("x = \t℘\n", "x = \t℘"),
        ("def f():\n    ℘ = 1\n", "℘ = 1"),
    ],
)
def test_python311_repairs_unicode_identifier_error_tokens(source, expected):
    import sys

    if sys.version_info[:2] != (3, 11):
        pytest.skip("ERRORTOKEN identifier repair is specific to Python 3.11")
    views = file_views(source.encode())
    assert views is not None
    line = 2 if source.startswith("def ") else 1
    expected_row = "    " + expected if line == 2 else expected
    assert views[(line, 0)].code == expected_row
    assert views[(line, 0)].bound == expected_row


def test_python311_repairs_identifier_following_blank_before_operand():
    import sys

    if sys.version_info[:2] != (3, 11):
        pytest.skip("ERRORTOKEN whitespace repair is specific to Python 3.11")
    source = "x = ℘\n"
    views = file_views(source.encode())
    assert views is not None
    assert views[(1, 0)].code == "x = ℘"
    assert views[(1, 0)].bound == "x = ℘"


def test_views_trusted_uses_isolated_bounded_probe(monkeypatch):
    import subprocess

    from cairnwork import tamper_scan_tokens as scanner

    captured = {}

    class Result:
        returncode = 0
        stdout = "3 14\n"

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(scanner.subprocess, "run", fake_run)
    monkeypatch.setattr(scanner.sys, "version_info", (3, 15, 0))
    assert scanner.views_trusted("python-target") is True
    assert captured["argv"] == [
        "python-target",
        "-I",
        "-S",
        "-B",
        "-c",
        "import sys; print(*sys.version_info[:2])",
    ]
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == scanner._PROBE_SECONDS
    assert captured["check"] is False
    assert captured["env"] == {}
    assert captured["cwd"] == "/"
    assert captured["stdin"] is subprocess.DEVNULL


@pytest.mark.parametrize("stdout", ["", "3.14\n", "314 0\n", "3 14 extra\n"])
def test_views_trusted_rejects_malformed_or_untrusted_probe(stdout, monkeypatch):
    from cairnwork import tamper_scan_tokens as scanner

    class Result:
        returncode = 0

        def __init__(self, text: str) -> None:
            self.stdout = text

    def fake_run(*args, **kwargs):
        return Result(stdout)

    monkeypatch.setattr(scanner.subprocess, "run", fake_run)
    assert scanner.views_trusted("python-target") is False


def test_views_trusted_rejects_failed_probe_and_newer_target(monkeypatch):
    from cairnwork import tamper_scan_tokens as scanner

    class Failed:
        returncode = 1
        stdout = "3 11\n"

    monkeypatch.setattr(scanner.subprocess, "run", lambda *a, **k: Failed())
    assert scanner.views_trusted("python-target") is False

    class Newer:
        returncode = 0
        stdout = "99 99\n"

    monkeypatch.setattr(scanner.subprocess, "run", lambda *a, **k: Newer())
    assert scanner.views_trusted("python-target") is False


def test_views_trusted_fails_closed_on_probe_errors(monkeypatch):
    from cairnwork import tamper_scan_tokens as scanner

    def fail(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(scanner.subprocess, "run", fail)
    assert scanner.views_trusted("python-target") is False


def test_added_file_rejects_empty_and_invalid_line_numbers():
    assert added_file([]) is None
    assert added_file([(0, "bad")]) is None


def test_token_repair_rejects_non_identifier_error_tokens():
    import tokenize

    from cairnwork.tamper_scan_tokens import _repair_identifier_errors

    error = tokenize.TokenInfo(tokenize.ERRORTOKEN, "`", (1, 0), (1, 1), "`bad`\n")
    assert _repair_identifier_errors([error]) is None
    assert _repair_identifier_errors([]) == []
