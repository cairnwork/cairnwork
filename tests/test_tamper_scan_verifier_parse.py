"""#848b: the hook-file rule reads a changed ``.py`` with the verifier's own interpreter.

AC1-AC7 of DESIGN-848b (v2.1). One batched subprocess (tamper_scan_walk.py) parses every changed
blob; only a plain ``SyntaxError`` is "unparseable by the verifier" (a note, no finding); every
other failure ends as ``scan_budget``. Fake interpreters are shell scripts that print a chosen
listing, so a failure of the run itself is tested without depending on the host's Pythons.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan_plugin as plugin
from cairnwork.tamper_scan import nfkc_names, scan_added_lines, scan_patch
from cairnwork.tamper_scan_budget import ScanBudget
from cairnwork.tamper_scan_plugin import HOOK_NAMES, file_rules, parse_blobs
from cairnwork.tamper_scan_walk import read_blob
from cairnwork.verify_context import VerifyContext, verifier_interpreter

LIB = "src/myplug.py"
FW_BIND = (
    "pytest_\uff43onfigure = lambda config: None\n"  # fullwidth c: NFKC gives pytest_configure
)
PLAIN = "X = 1\n"
FILE_RULES = {"hook_file_touched", "scan_budget", "source_encoding"}


def _scan(repo, new, old=PLAIN, python=sys.executable, notes=None, path=LIB):
    commit_change(repo, {path: old} if isinstance(old, str) else {})
    base = git(repo, "rev-parse", "HEAD")
    if isinstance(new, bytes):
        (repo / path).write_bytes(new)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "candidate")
        head = git(repo, "rev-parse", "HEAD")
    else:
        head = commit_change(repo, {path: new})
    found = scan_patch(repo, base, head, python=python, notes=notes)
    return {(f.path, f.rule) for f in found if f.rule in FILE_RULES}  # not the line rules


def _fake(tmp_path, body, name="fakepy"):
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _listing(tmp_path, rows, name="fakepy"):
    return _fake(tmp_path, f"cat >/dev/null\nprintf '%s' '{json.dumps({'rows': rows})}'", name)


def _minor(minor):
    exe = Path.home() / ".local" / "bin" / f"python3.{minor}"
    if not exe.exists():
        pytest.skip(f"no python3.{minor}")
    return str(exe)


# ---- AC1: the verifier's parse decides ----
def test_ac1_a_newer_verifier_parses_what_the_scanner_cannot(repo):
    """Round-3 shape: syntax the scanner's own Python lacks, a compatibility-spelled binding."""
    minor = sys.version_info.minor + 1
    newer = _minor(minor)
    src = "try:\n    pass\nexcept A, B:\n    pass\n" + FW_BIND  # PEP 758, 3.14
    if minor != 14:
        pytest.skip("the sample is 3.14 syntax: needs the scanner on 3.13")
    assert _scan(repo, src, python=newer) == {(LIB, "hook_file_touched")}


def test_ac1_an_older_verifier_cannot_import_newer_syntax(repo):
    old = _minor(11)
    src = "type Alias = int\n" + FW_BIND  # 3.12 syntax
    notes: list[str] = []
    assert _scan(repo, src, python=old, notes=notes) == set()  # the scanner's Python would refuse
    assert notes == [f"{LIB}: unparseable_by_verifier"]
    assert _scan(repo, src) == {(LIB, "hook_file_touched")}


def test_ac1_negative_the_same_file_without_the_binding(repo):
    assert _scan(repo, "type Alias = int\nX = 1\n") == set()


# ---- AC2 ----
def test_ac2_a_plain_syntax_error_is_noted_not_refused(repo):
    notes: list[str] = []
    bad = "# pytest_configure sample\nprint 'x'\n"
    assert _scan(repo, bad, notes=notes) == set()
    assert notes == [f"{LIB}: unparseable_by_verifier"]


def test_ac2_negative_a_parseable_file_that_binds_the_name_is_refused(repo):
    assert _scan(repo, FW_BIND) == {(LIB, "hook_file_touched")}


# ---- AC3: anything but a plain SyntaxError fails closed ----
DEEP = "X = (a\n" + "+a\n" * 50_000 + ")\n# pytest_configure\n"  # RecursionError on 3.11-3.14


def test_ac3_a_recursion_error_in_the_verifier_parse_is_scan_budget(repo, monkeypatch):
    seen = []
    real = plugin.parse_blobs
    monkeypatch.setattr(plugin, "parse_blobs", lambda *a: seen.append(real(*a)) or seen[-1])
    assert _scan(repo, DEEP) == {(LIB, "scan_budget")}
    assert seen == [[("fail", set()), ("ok", set())]]  # the walker reported it, not the budget


@pytest.mark.parametrize(
    ("error", "kind"),
    [(RecursionError, "fail"), (MemoryError, "fail"), (OSError, "fail"), (SyntaxError, "syntax")],
)
def test_ac6a_the_walker_classifies_by_exception(monkeypatch, error, kind):
    """Mutant (a): a RecursionError (or any non-SyntaxError) must not read as unparseable."""

    def boom(*_a, **_k):
        raise error("x")

    monkeypatch.setattr("ast.parse", boom)
    assert read_blob(b"X = 1\n", frozenset(HOOK_NAMES)) == [kind, []]


@pytest.mark.parametrize(
    "blob",
    [
        b"# coding: nosuchcodec\nX = 1\n",  # an unknown codec
        b"\xef\xbb\xbf# coding: latin-1\nX = 1\n",  # BOM and a cookie that disagree
        b"X = '\xff'\n",  # not UTF-8, no cookie: fails at the decode step
    ],
)
def test_ac3_encoding_failures_are_not_unparseable(blob):
    assert read_blob(blob, frozenset(HOOK_NAMES)) == ["fail", []]


@pytest.mark.parametrize(
    "body",
    [
        "cat >/dev/null; exit 3",  # crash
        "cat >/dev/null; printf 'not json'",
        "cat >/dev/null; printf '{\"rows\": []}'",  # a missing row (mutant e)
        'cat >/dev/null; printf \'{"rows": [["ok", []], ["ok", []], ["ok", []]]}\'',  # extra (e)
        'cat >/dev/null; printf \'{"rows": [["ok", ["os_exit"]]]}\'',  # not a hook name
        'cat >/dev/null; printf \'{"rows": [["fail", ["pytest_configure"]]]}\'',
        'cat >/dev/null; printf \'{"rows": [["weird", []]]}\'',
        'cat >/dev/null; printf \'{"rows": [["ok", []]], "more": 1}\'',
        "cat >/dev/null; printf '[[\"ok\", []]]'",
    ],
)
def test_ac3_a_bad_run_or_listing_is_scan_budget(repo, tmp_path, body):
    assert _scan(repo, FW_BIND, python=_fake(tmp_path, body)) == {(LIB, "scan_budget")}


def test_ac3_an_oversized_listing_is_scan_budget(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "MAX_OUTPUT", 10)
    py = _listing(tmp_path, [["ok", []]])
    assert _scan(repo, PLAIN + "Y = 2\n", python=py) == {(LIB, "scan_budget")}


def test_ac3_a_reported_failure_row_is_scan_budget(repo, tmp_path):
    assert _scan(repo, FW_BIND, python=_listing(tmp_path, [["fail", []], ["ok", []]])) == {
        (LIB, "scan_budget")
    }


def test_ac3_a_timeout_is_scan_budget_and_spends_the_budget(tmp_path):
    budget = ScanBudget(seconds=0.3)
    assert parse_blobs([b"X = 1\n"], _fake(tmp_path, "sleep 5"), budget) is None
    assert budget.over


def test_ac3_negative_valid_rows_give_the_normal_findings(repo, tmp_path):
    py = _listing(tmp_path, [["ok", ["pytest_configure"]], ["ok", []]])
    assert _scan(repo, FW_BIND, python=py) == {(LIB, "hook_file_touched")}
    py2 = _listing(tmp_path, [["ok", []], ["ok", []]], "fake2")
    assert _scan(repo, "Y = 2\n", python=py2) == set()


def test_no_python_file_changed_starts_no_process(repo, tmp_path):
    marker = tmp_path / "ran"
    py = _fake(tmp_path, f"touch {marker}; exit 9")
    assert _scan(repo, "text\n", old="text0\n", python=py, path="docs/a.txt") == set()
    assert not marker.exists()


# ---- AC4: bytes, not text ----
def test_ac4_a_utf8_bom_file_that_binds_a_hook_is_refused(repo):
    assert _scan(repo, b"\xef\xbb\xbf" + FW_BIND.encode()) == {(LIB, "hook_file_touched")}


def test_ac6b_the_blobs_cross_as_bytes():
    """Mutant (b): sent as text, a BOM file would be a SyntaxError and pass."""
    rows = parse_blobs(
        [b"\xef\xbb\xbfdef pytest_configure(c): pass\n"], sys.executable, ScanBudget()
    )
    assert rows == [("ok", {"pytest_configure"})]


# ---- AC5: one spelling table for the line rules and the file rule ----
SPELLINGS = {
    "fi ligature": ("collection\ufb01nish", "collectionfinish"),
    "long s": ("\u017fession", "session"),
    "fullwidth": ("\uff43onfigure", "configure"),
    "math bold": ("\U0001d41conf\U0001d422gure", "configure"),
    "kelvin": ("\u212a", "K"),
}


@pytest.mark.parametrize("label", list(SPELLINGS))
def test_ac5_the_rules_read_a_compatibility_spelling_as_python_does(label):
    spelled, plain = SPELLINGS[label]
    assert nfkc_names(spelled) == plain
    # the file rule (the verifier's own parser) and the line rules both see the plain name
    assert read_blob(f"{spelled} = 1\n".encode(), frozenset({plain})) == ["ok", [plain]]
    assert read_blob(f"x = '{spelled}'\n".encode(), frozenset({plain})) == ["ok", []]  # no NFKC
    hit = scan_added_lines(LIB, [(1, "\uff50luggy = 1"), (2, "import \uff50luggy")])
    assert {f.rule for f in hit} == {"pytest_internals"}  # the fullwidth p of pluggy


def test_ac5_a_hook_spelled_two_ways_is_one_name(repo):
    for spelled in ("pytest_report_collection\ufb01nish", "pytest_\u017fessionstart"):
        assert _scan(repo, f"{spelled} = None\n") == {(LIB, "hook_file_touched")}


def test_ac5_the_same_spelling_in_a_string_is_not_normalised(repo):
    assert _scan(repo, "x = 'pytest_\uff43onfigure'\n") == set()


def test_import_of_a_dotted_hook_module_is_read():
    row = read_blob(b"import pytest_configure.sub\n", frozenset(HOOK_NAMES))
    assert row == ["ok", ["pytest_configure"]]


# ---- AC6 (c): the verifier's interpreter is the one that runs, with -I -S -B ----
def test_ac6c_the_scan_runs_the_given_interpreter_isolated(repo, tmp_path):
    log = tmp_path / "log"
    wrapper = _fake(
        tmp_path,
        f'printf "%s\\n" "$0" "$1" "$2" "$3" "$4" >> {log}\nexec {sys.executable} "$@"',
    )
    assert _scan(repo, FW_BIND, python=wrapper) == {(LIB, "hook_file_touched")}
    lines = log.read_text().split("\n")
    assert lines[0] == wrapper
    assert lines[1:5] == ["-I", "-S", "-B", "-c"]


def test_ac6c_the_process_is_bare(repo, tmp_path):
    """env={} and cwd=/ (bytecode_roots' precedent): nothing of the owner's state reaches it."""
    out = tmp_path / "env"
    wrapper = _fake(tmp_path, f'pwd > {out}; env >> {out}\nexec {sys.executable} "$@"')
    os.environ["CW_PROBE_SECRET"] = "x"
    try:
        _scan(repo, PLAIN + "Y = 1\n", python=wrapper)
    finally:
        del os.environ["CW_PROBE_SECRET"]
    text = out.read_text()
    assert text.startswith("/\n") and "CW_PROBE_SECRET" not in text


# ---- plumbing: both candidate paths hand the verifier's interpreter to the scan ----
def test_the_interpreter_is_the_verifiers_own_expression(tmp_path):
    class Account:
        python = "/acct/python"

    base = VerifyContext(Path("."), Path("."))
    assert verifier_interpreter(base) == sys.executable
    assert verifier_interpreter(VerifyContext(Path("."), Path("."), python="/p")) == "/p"
    withacct = VerifyContext(Path("."), Path("."), account=Account())  # type: ignore[arg-type]
    assert verifier_interpreter(withacct) == "/acct/python"
    explicit = VerifyContext(Path("."), Path("."), account=Account(), python="/p")  # type: ignore[arg-type]
    assert verifier_interpreter(explicit) == "/p"


def test_ac6d_scan_patch_requires_the_interpreter(repo):
    with pytest.raises(TypeError):
        scan_patch(repo, "0" * 40, "1" * 40)  # type: ignore[call-arg]


def test_ac6d_every_caller_passes_the_verifiers_interpreter():
    """Mutants: the verify path wired and the worker path not, or the reverse."""
    src = Path(__file__).parent.parent / "src" / "cairnwork"
    for name, call in (
        ("verify_candidate.py", "check_candidate_commit("),
        ("pipeline.py", "snapshot_candidate("),
    ):
        text = (src / name).read_text()
        at = text.index(call, text.index("def "))
        assert "python=verifier_interpreter(ctx)" in text[at : at + 300], name


# ---- AC7 / AC6 (f): a delete-only change is read for a foreign cookie ----
def test_ac7_a_delete_only_change_whose_new_blob_has_a_foreign_cookie_is_refused(repo):
    old = "X = 1\n# coding: latin-1\nY = 2\n"
    commit_change(repo, {LIB: old})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: "# coding: latin-1\nY = 2\n"})  # the cookie moves up
    found = {(f.path, f.rule) for f in scan_patch(repo, base, head, python=sys.executable)}
    assert (LIB, "source_encoding") in found


def test_ac7_negative_a_delete_only_change_without_a_cookie_passes(repo):
    commit_change(repo, {LIB: "X = 1\nY = 2\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: "Y = 2\n"})
    assert scan_patch(repo, base, head, python=sys.executable) == []


def test_ac3_the_walker_runs_alone_with_a_closed_stdin():
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", plugin.WALKER.read_text(), "[]"],
        input=b"\x00\x00\x00\x00", capture_output=True, timeout=30, check=False,
    )  # fmt: skip
    assert json.loads(done.stdout) == {"rows": []}


def test_file_rules_still_takes_budget_per_blob(repo):
    commit_change(repo, {LIB: PLAIN})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: PLAIN + "Y = 2\n"})
    got = file_rules(repo, base, head, ScanBudget(tokenized=10), sys.executable)
    assert got == [(LIB, "scan_budget")]
