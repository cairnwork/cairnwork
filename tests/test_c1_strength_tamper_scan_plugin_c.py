"""Second strength pass over tamper_scan_plugin: framing, shape check, git parsing, setattr scan."""

import sys

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan_plugin as plugin
from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan_budget import ScanBudget

HOOKS = sorted(plugin.HOOK_NAMES)[:6]


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


A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
Z40, Z64 = "0" * 40, "0" * 64


# ---- _frame ----
def test_file_rules_refuses_object_names_git_did_not_promise(monkeypatch):
    for bad in ("g" * 40, "abc", "A" * 40):
        raw = _raw((Z40, C, "A", "ok.py"), (bad, D, "M", "bad.py"))
        _scripted(monkeypatch, raw, rows=[])
        with pytest.raises(GitCommandError, match="unexpected object name"):
            _run()
    raw = _raw((Z40, "g" * 40, "A", "bad.py"), (Z40, C, "A", "ok.py"))
    _scripted(monkeypatch, raw, rows=[])
    with pytest.raises(GitCommandError, match="unexpected object name"):
        _run()


def test_file_rules_empty_diff_is_empty(monkeypatch):
    asked = _scripted(monkeypatch, b"", rows=[])
    assert _run() == []
    assert asked["oids"] == []


def test_file_rules_parse_failure_reports_budget_on_first_work_path_after_found(monkeypatch):
    raw = _raw((Z40, C, "A", "lib.so"), (Z40, D, "A", "p.py"), (Z40, A, "A", "q.py"))
    _scripted(monkeypatch, raw, rows=None)
    assert _run() == [("lib.so", "sourceless_binary"), ("p.py", "scan_budget")]


def test_file_rules_parse_failure_with_no_python_work_adds_nothing(monkeypatch):
    raw = _raw((Z40, C, "A", "lib.so"))
    _scripted(monkeypatch, raw, rows=None)
    assert _run() == [("lib.so", "sourceless_binary")]
    budget = ScanBudget()
    budget.over = True
    _scripted(monkeypatch, raw, rows=[])
    assert _run(budget) == [("lib.so", "sourceless_binary")]


def test_file_rules_budget_over_after_parse_is_scan_budget(monkeypatch):
    raw = _raw((Z40, C, "A", "p.py"))
    budget = ScanBudget()

    def rows(data):
        budget.over = True
        return [("ok", {"pytest_configure"})]

    _scripted(monkeypatch, raw, rows=rows)
    assert _run(budget) == [("p.py", "scan_budget")]


def test_file_rules_fail_row_stops_with_scan_budget_for_that_path(monkeypatch):
    raw = _raw((Z40, C, "A", "a.py"), (Z40, D, "A", "b.py"), (Z40, A, "A", "c.py"))
    rows = [("ok", {"pytest_configure"}), ("fail", set()), ("ok", {"pytest_configure"})]
    _scripted(monkeypatch, raw, rows=rows)
    assert _run() == [("a.py", "hook_file_touched"), ("b.py", "scan_budget")]


def test_file_rules_syntax_rows_note_once_per_path_and_flag_nothing(monkeypatch, caplog):
    raw = _raw((C, D, "M", "s.py"), (A, B, "M", "t.py"))
    rows = [("syntax", set()), ("syntax", set()), ("syntax", set()), ("ok", set())]
    _scripted(monkeypatch, raw, rows=rows)
    notes = []
    with caplog.at_level("INFO", logger=plugin.log.name):
        assert _run(notes=notes) == []
    assert notes == ["s.py: unparseable_by_verifier", "t.py: unparseable_by_verifier"]
    assert sum("unparseable_by_verifier" in r.getMessage() for r in caplog.records) == 2
    _scripted(monkeypatch, raw, rows=rows)
    assert _run() == []  # notes=None is fine


def test_file_rules_row_count_mismatch_is_an_error_not_silently_truncated(monkeypatch):
    raw = _raw((Z40, C, "A", "a.py"), (Z40, D, "A", "b.py"))
    _scripted(monkeypatch, raw, rows=[("ok", set())])
    with pytest.raises(ValueError):
        _run()


def test_file_rules_odd_field_count_is_an_error(monkeypatch):
    raw = _raw((Z40, C, "A", "a.py")) + b"stray\0"
    _scripted(monkeypatch, raw, rows=[("ok", set())])
    with pytest.raises(ValueError):
        _run()


def test_file_rules_real_git_end_to_end(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit_change(
        repo,
        {
            "src/hook.py": "def pytest_configure(config):\n    pass\n",
            "src/plain.py": "z = 1\n",
            "src/broken.py": "def (:\n",
            "src/mod.so": "binary-ish",
            "docs/readme.md": "hi\n",
        },
        delete=["src/b.py"],
    )
    head = git(repo, "rev-parse", "HEAD")
    notes = []
    got = plugin.file_rules(repo, base, head, ScanBudget(), sys.executable, notes)
    assert sorted(got) == [
        ("src/hook.py", "hook_file_touched"),
        ("src/mod.so", "sourceless_binary"),
    ]
    assert notes == ["src/broken.py: unparseable_by_verifier"]
    commit_change(repo, {}, delete=["src/mod.so"])
    after = git(repo, "rev-parse", "HEAD")
    assert plugin.file_rules(repo, head, after, ScanBudget(), sys.executable) == []


def test_file_rules_real_git_old_side_hook_is_found_when_file_is_rewritten_clean(repo):
    commit_change(repo, {"src/h.py": "def pytest_configure(c):\n    pass\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/h.py": "def other():\n    pass\n"})
    assert plugin.file_rules(repo, base, head, ScanBudget(), sys.executable) == [
        ("src/h.py", "hook_file_touched")
    ]
    gone = commit_change(repo, {}, delete=["src/h.py"])
    assert plugin.file_rules(repo, head, gone, ScanBudget(), sys.executable) == []
    assert plugin.file_rules(repo, base, gone, ScanBudget(), sys.executable) == [
        ("src/h.py", "hook_file_touched")
    ]


# ---- setattr_forge ----
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("setattr(f, '__code__', g)", True),
        ('setattr(f, "__globals__", g)', True),
        ("setattr(f, '__code__', g); setattr(h, 1, 2)", True),
        ("setattr(f, 1, 2); setattr(g, '__code__', h)", True),
        ("setattr(f, 1, 2); '__code__'", False),
        ("setattr(f, 1, 2) # '__code__'", False),
        ("setattr(f, 1, 2) ;'__globals__'", False),
        ("setattr(f, 1, 2)#'__globals__'", False),
        ("setattr(f, # c\n '__code__')", False),
        ("'__code__' setattr(f)", False),
        ("setattr(f, 'code')", False),
        ("x = '__code__'", False),
        ("setattr(f, 1); setattr(f, '__code__'", True),
        ("", False),
    ],
)
def test_setattr_forge_separators_reset_and_token_order_matters(line, expected):
    assert plugin.setattr_forge(line) is expected


# ---- additions: header start, git flags, path decoding, oid order, tokenize budget ----
def test_batch_refuses_output_that_starts_with_a_blank_line(monkeypatch):
    raw = b"\n" + A.encode() + b" blob 1\nx\n"
    monkeypatch.setattr(plugin, "read_git_bytes", lambda *a, **k: raw)
    with pytest.raises(GitCommandError, match="did not return a blob"):
        plugin._batch(object(), [A])


def test_file_rules_bad_commit_id_message_is_exact(repo):
    with pytest.raises(GitCommandError) as err:
        plugin.file_rules(repo, "bad", "0" * 40, ScanBudget(), sys.executable)
    assert str(err.value).endswith(": base or head is not a full commit id")


def test_file_rules_asks_git_for_a_plain_machine_readable_raw_diff(monkeypatch):
    seen = {}

    def fake(repo, *args, **kwargs):
        seen["args"] = args
        return b""

    monkeypatch.setattr(plugin, "read_git_bytes", fake)
    assert _run() == []
    assert seen["args"] == (
        "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
        "--raw", "--no-abbrev", "-z", A, B,
    )  # fmt: skip


def test_file_rules_paths_decode_as_utf8_with_surrogateescape(monkeypatch):
    raw = _raw((Z40, C, "A", "café.so"))
    raw = raw.replace("café".encode("latin-1"), "café".encode())
    raw += b":100644 100644 " + Z40.encode() + b" " + C.encode() + b" A\0\xff\xfe.pyc\0"
    _scripted(monkeypatch, raw, rows=[])
    assert _run() == [("café.so", "sourceless_binary"), ("\udcff\udcfe.pyc", "sourceless_binary")]


def test_file_rules_fetches_old_oid_before_new_oid_of_a_modified_file(monkeypatch):
    raw = _raw((C, D, "M", "m.py"))
    asked = _scripted(monkeypatch, raw, rows=[("ok", set()), ("ok", set())])
    _run()
    assert asked["oids"] == [C, D]


def test_file_rules_tokenize_budget_exhaustion_is_scan_budget_for_that_path(monkeypatch):
    raw = _raw((Z40, C, "A", "lib.so"), (Z40, D, "A", "p.py"), (Z40, A, "A", "q.py"))
    asked = _scripted(monkeypatch, raw, rows=[])
    assert _run(ScanBudget(tokenized=0)) == [
        ("lib.so", "sourceless_binary"),
        ("p.py", "scan_budget"),
    ]
    assert "data" not in asked
