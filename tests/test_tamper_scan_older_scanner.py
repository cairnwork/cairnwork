"""#919: a line is skipped as a comment only when the scanner's Python is at least the verifier's.

String syntax only grows (PEP 701 f-strings in 3.12, t-strings in 3.14), so an older tokenizer can
read as a comment (no ERRORTOKEN) a line that the verifier's Python runs as code. When the
scanner is older, or the verifier's version cannot be read, no line is skipped: every rule reads
comment-only lines too (fail closed; honest comments that mention a rule's text are then refused).
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_change, git

from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_tokens import file_views, views_trusted

LIB = "src/lib.py"
# 3.12+: line 3 is string text, then code; 3.11's tokenizer reads all of line 3 as a comment.
PEP701 = (
    "import pytest\nx = (f\"{'''\"\n#'''}\"); pytest.TestReport.runtest = None; y = (\n)\n"
)  # fmt: skip
COMMENT = "import pytest\n# pytest.TestReport.runtest = None\nY = 1\n"  # an honest comment
PLAIN = "import pytest\n# a comment\nY = 1\n"


def _fake(tmp_path, version, name="fakepy"):
    """An interpreter that prints ``version`` when asked and, to the walker, two empty rows (the
    old and new blob of the one changed file)."""
    script = tmp_path / name
    listing = '{"rows": [["ok", []], ["ok", []]]}'
    script.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        f"  *'print(*sys.version_info[:2])'*) cat; printf '%s' '{version}' ;;\n"
        f"  *) cat >/dev/null; printf '%s' '{listing}' ;;\nesac\n"
    )  # fmt: skip
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _rules(repo, text, python):
    commit_change(repo, {LIB: "import pytest\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: text})
    return {f.rule for f in scan_patch(repo, base, head, python=python)}


def _now(delta):
    major, minor = sys.version_info[:2]
    return f"{major} {minor + delta}\n"


def test_the_scanners_own_python_is_trusted():
    assert views_trusted(sys.executable)


@pytest.mark.parametrize(
    ("version", "trusted"),
    [(_now(0), True), (_now(-1), True), (_now(1), False), ("3\n", False), ("3 x\n", False),
     ("4 0\n3 0\n", False), (f"٣ {sys.version_info[1]}\n", False), ("", False)],
    ids=["same", "older", "newer", "no-minor", "not-a-number", "two-lines", "arabic-digit",
         "empty"],
)  # fmt: skip
def test_the_verifiers_version_decides(tmp_path, version, trusted):
    assert views_trusted(_fake(tmp_path, version)) is trusted


def test_an_interpreter_that_fails_or_is_missing_is_not_trusted(tmp_path):
    failing = tmp_path / "failing"
    failing.write_text(f"#!/bin/sh\nprintf '{_now(0)}'\nexit 1\n")
    failing.chmod(failing.stat().st_mode | stat.S_IXUSR)
    assert not views_trusted(str(failing))
    assert not views_trusted(str(tmp_path / "missing"))


def test_untrusted_views_skip_nothing_and_keep_the_rest():
    data = (COMMENT + '"""doc\n# text\n"""\n').encode()
    trusted, untrusted = file_views(data), file_views(data, trusted=False)
    assert trusted is not None and untrusted is not None and trusted.keys() == untrusted.keys()
    assert trusted[(2, 0)].skip and trusted[(5, 0)].skip  # a comment and string text
    assert not any(view.skip for view in untrusted.values())
    assert [(v.code, v.bound) for v in trusted.values()] == [
        (v.code, v.bound) for v in untrusted.values()
    ]


def test_an_untrusted_scan_reads_a_comment_only_line():
    lines = [(2, "# pytest.TestReport.runtest = None")]
    data = COMMENT.encode()
    assert scan_added_lines(LIB, lines, data) == []
    assert [f.rule for f in scan_added_lines(LIB, lines, data, trusted=False)] == [
        "pytest_public_patch"
    ]


def test_scan_patch_gates_the_comment_skip_on_the_verifiers_version(repo, tmp_path):
    assert _rules(repo, COMMENT, _fake(tmp_path, _now(0))) == set()
    newer = _fake(tmp_path, _now(1), "newer")
    assert _rules(repo, COMMENT, newer) == {"pytest_public_patch"}  # accepted cost (fail closed)
    assert _rules(repo, PLAIN, newer) == set()  # a comment with no rule text still passes


def _minor(minor):
    exe = Path.home() / ".local" / "bin" / f"python3.{minor}"
    if not exe.exists():
        pytest.skip(f"no python3.{minor}")
    return str(exe)


def _scan_with(scanner, repo, base, head, verifier):
    """scan_patch run by ``scanner`` (another Python) on this checkout's source."""
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = (
        "import sys; from pathlib import Path; from cairnwork.tamper_scan import scan_patch\n"
        "print(sorted({f.rule for f in scan_patch(Path(sys.argv[1]), sys.argv[2], sys.argv[3],"
        " python=sys.argv[4])}))"
    )  # fmt: skip
    done = subprocess.run(
        [scanner, "-c", code, str(repo), base, head, verifier], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": src}, check=True, timeout=120,
    )  # fmt: skip
    return done.stdout.strip()


def test_a_311_scanner_with_a_312_verifier_reads_the_pep701_line(repo):
    old, new = _minor(11), _minor(12)
    commit_change(repo, {LIB: "import pytest\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: PEP701})
    assert file_views(PEP701.encode())  # this Python reads the file (no ERRORTOKEN)
    # before #919 the 3.11 scanner skipped line 3 as a comment and found nothing
    assert _scan_with(old, repo, base, head, new) == "['pytest_public_patch']"
    assert _scan_with(new, repo, base, head, new) == "['pytest_public_patch']"
