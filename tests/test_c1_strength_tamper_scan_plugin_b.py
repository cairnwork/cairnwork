"""Second strength pass over tamper_scan_plugin: framing, shape check, git parsing, setattr scan."""

import subprocess
import sys

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan_plugin as plugin
from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan_budget import ScanBudget

HOOKS = sorted(plugin.HOOK_NAMES)[:6]
A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
Z40, Z64 = "0" * 40, "0" * 64


# ---- _frame ----
def test_frame_is_count_then_length_prefixed_blobs_big_endian():
    blobs = [b"x" * 258, b"", b"yz"]
    expect = (3).to_bytes(4, "big")
    expect += b"\x00\x00\x01\x02" + b"x" * 258 + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x02yz"
    assert plugin._frame(blobs) == expect
    assert plugin._frame([]) == b"\x00\x00\x00\x00"
    assert plugin._frame([b"q"]) == b"\x00\x00\x00\x01\x00\x00\x00\x01q"


# ---- _rows_ok ----
def test_rows_ok_extra_key_alone_is_refused():
    assert plugin._rows_ok({"rows": [["ok", []]]}, 1)
    assert not plugin._rows_ok({"rows": [["ok", []]], "extra": 1}, 1)


def test_rows_ok_row_count_must_equal_expected_exactly():
    two = {"rows": [["ok", []], ["ok", []]]}
    assert not plugin._rows_ok(two, 1)
    assert plugin._rows_ok(two, 2)
    assert not plugin._rows_ok({"rows": [["ok", []]]}, 2)
    assert not plugin._rows_ok({"rows": [["ok", []]]}, 0)


def test_rows_ok_row_is_exactly_two_long():
    assert not plugin._rows_ok({"rows": [["ok", [], 1]]}, 1)
    assert not plugin._rows_ok({"rows": [["ok", [], []]]}, 1)
    assert not plugin._rows_ok({"rows": [("ok", [])]}, 1)
    assert not plugin._rows_ok({"rows": [[]]}, 1)


def test_rows_ok_non_ok_kinds_need_empty_names_and_every_kind_is_known():
    for kind in ("syntax", "fail"):
        assert plugin._rows_ok({"rows": [[kind, []]]}, 1)
        assert not plugin._rows_ok({"rows": [[kind, HOOKS]]}, 1)
    assert not plugin._rows_ok({"rows": [["OK", []]]}, 1)
    assert not plugin._rows_ok({"rows": [[None, []]]}, 1)
    assert plugin._rows_ok({"rows": [["ok", HOOKS]]}, 1)
    assert not plugin._rows_ok({"rows": [["ok", HOOKS[:5] + [7]]]}, 1)
    assert not plugin._rows_ok({"rows": [["ok", ()]]}, 1)


def test_rows_ok_every_row_is_checked_not_just_the_first():
    good = ["ok", []]
    assert not plugin._rows_ok({"rows": [good, ["bad", []]]}, 2)
    assert not plugin._rows_ok({"rows": [good, ["ok", ["nope"]]]}, 2)


# ---- parse_blobs timeout and budget ----
def _fake_run(seen, out=b'{"rows":[["ok",[]]]}'):
    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, out, b"")

    return run


@pytest.mark.parametrize("left", [0, -5, -0.0004])
def test_parse_blobs_timeout_never_below_one_millisecond(monkeypatch, left):
    seen = {}
    monkeypatch.setattr(plugin.subprocess, "run", _fake_run(seen))
    budget = ScanBudget(seconds=left)
    plugin.parse_blobs([b"x=1"], sys.executable, budget)
    assert seen["timeout"] == 0.001


def test_parse_blobs_timeout_is_the_budget_left_when_larger_than_the_floor(monkeypatch):
    seen = {}
    monkeypatch.setattr(plugin.subprocess, "run", _fake_run(seen))
    plugin.parse_blobs([b"x=1"], sys.executable, ScanBudget(seconds=7.5))
    assert seen["timeout"] == 7.5
    plugin.parse_blobs([b"x=1"], sys.executable, ScanBudget(seconds=0.002))
    assert seen["timeout"] == 0.002


def test_parse_blobs_empty_input_runs_nothing_and_malformed_output_is_none(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no process for no blobs")

    monkeypatch.setattr(plugin.subprocess, "run", boom)
    assert plugin.parse_blobs([], sys.executable, ScanBudget()) == []
    for out in (b"not json", b'{"rows":[]}', b'{"rows":[["ok",[]]],"x":1}'):
        monkeypatch.setattr(plugin.subprocess, "run", _fake_run({}, out))
        assert plugin.parse_blobs([b"x"], sys.executable, ScanBudget()) is None


# ---- _batch ----
def test_batch_reads_several_blobs_with_newlines_and_empty_content(monkeypatch):
    raw = (
        A.encode()
        + b" blob 7\nab\n\ncd\n\n"  # content "ab\n\ncd\n"
        + B.encode()
        + b" blob 0\n\n"
        + C.encode()
        + b" blob 3\nxyz\n"
    )
    seen = {}

    def fake(repo, *args, stdin=b""):
        seen["args"], seen["stdin"] = args, stdin
        return raw

    monkeypatch.setattr(plugin, "read_git_bytes", fake)
    out = plugin._batch(object(), [A, B, C])
    assert out == {A: b"ab\n\ncd\n", B: b"", C: b"xyz"}
    assert seen["args"] == ("cat-file", "--batch")
    assert seen["stdin"] == f"{A}\n{B}\n{C}\n".encode()
    assert plugin._batch(object(), []) == {}


@pytest.mark.parametrize(
    "raw",
    [
        b"%s tree 1\nx\n" % A.encode(),
        b"%s blob\nx\n" % A.encode(),
        b"%s blob 1 extra\nx\n" % A.encode(),
        b"%s missing\n" % A.encode(),
    ],
)
def test_batch_refuses_non_blob_or_malformed_header_with_message(monkeypatch, raw):
    monkeypatch.setattr(plugin, "read_git_bytes", lambda *a, **k: raw)
    with pytest.raises(GitCommandError, match="did not return a blob"):
        plugin._batch(object(), [A])


def test_batch_second_header_is_checked_too(monkeypatch):
    raw = A.encode() + b" blob 1\nx\n" + B.encode() + b" tree 1\ny\n"
    monkeypatch.setattr(plugin, "read_git_bytes", lambda *a, **k: raw)
    with pytest.raises(GitCommandError, match="did not return a blob"):
        plugin._batch(object(), [A, B])


def test_batch_real_git_round_trip_for_binary_and_text(repo):
    data = b"\x00\n\n\xff abc\n"
    (repo / "bin.dat").write_bytes(data)
    (repo / "t.txt").write_text("one\ntwo\n")
    (repo / "e.txt").write_text("")
    commit_change(repo)
    oids = [git(repo, "rev-parse", f"HEAD:{n}") for n in ("bin.dat", "t.txt", "e.txt", "src/a.py")]
    got = plugin._batch(repo, oids)
    assert [got[o] for o in oids] == [data, b"one\ntwo\n", b"", b"x = 1\n"]


# ---- file_rules, with a scripted git ----
def _raw(*entries):
    return "".join(f":100644 100644 {o} {n} {s}\0{p}\0" for o, n, s, p in entries).encode()


def _scripted(monkeypatch, raw, blobs=None, rows=None):
    asked = {}

    def batch(repo, oids):
        asked["oids"] = list(oids)
        return {o: (blobs or {}).get(o, b"x=1\n") for o in oids}

    def parse(data, python, budget):
        asked["data"] = list(data)
        return rows(data) if callable(rows) else rows

    monkeypatch.setattr(plugin, "read_git_bytes", lambda *a, **k: raw)
    monkeypatch.setattr(plugin, "_batch", batch)
    monkeypatch.setattr(plugin, "parse_blobs", parse)
    return asked


def _run(budget=None, notes=None):
    return plugin.file_rules(object(), A, B, budget or ScanBudget(), sys.executable, notes)


def test_file_rules_zero_ids_are_never_fetched_and_ids_deduplicate_in_order(monkeypatch):
    raw = _raw(
        (Z40, C, "A", "added.py"),
        (D, Z64, "D", "gone.py"),
        (C, C, "M", "same.py"),
        (Z64, A, "A", "added64.py"),
    )
    asked = _scripted(monkeypatch, raw, rows=lambda d: [("ok", set())] * len(d))
    assert _run() == []
    assert asked["oids"] == [C, D, A]
    assert len(asked["data"]) == 1 + 1 + 1 + 1 + 1  # new then old per file, zero sides skipped


def test_file_rules_new_side_comes_before_old_side_per_file(monkeypatch):
    raw = _raw((C, D, "M", "m.py"))
    blobs = {C: b"old\n", D: b"new\n"}
    asked = _scripted(monkeypatch, raw, blobs, rows=[("ok", set()), ("ok", set())])
    _run()
    assert asked["data"] == [b"new\n", b"old\n"]


def test_file_rules_hook_found_once_per_path_even_on_both_sides(monkeypatch):
    raw = _raw((C, D, "M", "m.py"), (A, B, "M", "n.py"))
    rows = [
        ("ok", {"pytest_configure"}),
        ("ok", {"pytest_configure"}),
        ("ok", set()),
        ("ok", set()),
    ]
    _scripted(monkeypatch, raw, rows=rows)
    assert _run() == [("m.py", "hook_file_touched")]


def test_file_rules_sourceless_extensions_status_and_case(monkeypatch):
    names = ["a.pyc", "b.PYO", "c.so", "d.pyd", "e.dylib", "f.zip", "g.egg", "h.whl", "i.pyz"]
    raw = _raw(*[(Z40, C, "A", n) for n in names])
    raw += _raw((C, Z40, "D", "deleted.pyc"), (Z40, C, "A", "notes.txt"), (Z40, C, "A", "x.pyc.py"))
    asked = _scripted(monkeypatch, raw, rows=[("ok", set())])
    assert _run() == [(n, "sourceless_binary") for n in names]
    assert asked["oids"] == [C]  # only x.pyc.py is a python file


def test_file_rules_sourceless_binary_is_not_also_parsed_and_py_checked_by_suffix(monkeypatch):
    raw = _raw((Z40, C, "A", "a.py.pyc"), (Z40, D, "A", "b.pyw"), (Z40, A, "A", "c.PY"))
    asked = _scripted(monkeypatch, raw, rows=[])
    assert _run() == [("a.py.pyc", "sourceless_binary")]
    assert asked["oids"] == []
