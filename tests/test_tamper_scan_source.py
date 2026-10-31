"""tamper_scan: the scan reads the source text Python compiles.

Python reads identifiers in NFKC form (PEP 3131) and honours a source encoding declared in a
comment on line 1 or 2 (PEP 263), so the scan matches the normalized text too and refuses a
declared encoding other than UTF-8 on an added line 1-2. The scan's diff runs with ``--text``,
and a changed ``.py`` file that the diff shows without its lines is unreadable.
"""

import pytest
from conftest import commit_change, git

from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan import added_lines, scan_added_lines, scan_patch

TEST = "tests/test_x.py"
LIBRARY = "pkg/module.py"


def _rules(path, text):
    lines = list(enumerate(text.split("\n"), start=1))
    return [(f.line, f.rule) for f in scan_added_lines(path, lines)]


def _found(repo, base, head):
    return [(f.path, f.line, f.rule) for f in scan_patch(repo, base, head)]


@pytest.mark.parametrize(
    ("path", "text", "rule"),
    [
        (LIBRARY, "import os\nos._ｅxit(0)", "os_exit"),
        (LIBRARY, "import gc\nｇc.get_objects()", "gc_walk"),
        (LIBRARY, "import sys\nsys.ｐycache_prefix = '/x'", "pycache_prefix"),
        (LIBRARY, "import sys\nsys.\U0001d5c6eta_path.insert(0, None)", "meta_path"),
        (TEST, "import sys\nsys.ｍodules['pytest'] = None", "sys_modules_write"),
        (LIBRARY, "import os as ｏ\no._exit(0)", "os_exit"),
    ],
)
def test_scan_matches_the_identifiers_python_compiles(path, text, rule):
    assert _rules(path, text) == [(2, rule)]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("# coding: utf-7\nimport gc", 1),
        ("#!/usr/bin/env python\n# -*- coding: latin-1 -*-\nx = 1", 2),
        ("# vim: set fileencoding=raw_unicode_escape :\nx = 1", 1),
        ("#\r# coding=unicode_escape\nx = 1", 1),
    ],
)
def test_a_source_encoding_other_than_utf8_on_line_1_or_2_is_refused(text, line):
    assert _rules(LIBRARY, text) == [(line, "source_encoding")]


@pytest.mark.parametrize("path", [TEST, LIBRARY])
@pytest.mark.parametrize(
    "text",
    [
        "# -*- coding: utf-8 -*-\n# 這是說明：讀取設定檔，再回傳結果。\nx = 1",
        "#!/usr/bin/env python3\n# coding=UTF-8\nimport os",
        "# vim: set fileencoding=utf8 :\nx = 1",
        "# -*- coding: utf_8 -*-\nx = 1",
        "x = 1\ny = 2\n# coding: latin-1 (line 3 is an ordinary comment)",
        'GREETING = "你好，世界（測試）"\nNAME = "Café ｆｕｌｌ ｗｉｄｔｈ"',
        "def 計算(數量):\n    return 數量 * 2  # 中文註解：回傳兩倍",
        "π = 3.14159\nlabel = f'半徑：{π}'",
    ],
)
def test_ordinary_non_ascii_source_passes(path, text):
    assert _rules(path, text) == []


def test_scan_patch_refuses_a_declared_encoding_on_an_added_first_line(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/c.py": "# coding: utf-7\nimport gc\n"})
    assert _found(repo, base, head) == [("src/c.py", 1, "source_encoding")]


@pytest.mark.parametrize(
    "setting",
    ["committed", "info", "big_file"],
)
def test_a_py_file_git_would_show_as_binary_is_still_scanned(repo, setting):
    if setting == "committed":
        commit_change(repo, {".gitattributes": "*.py -diff\n"})
    elif setting == "info":
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("*.py binary\n")
    else:
        git(repo, "config", "core.bigFileThreshold", "10")
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"pkg/mod.py": "import os\nx = 1\nos._exit(0)\n"})
    assert _found(repo, base, head) == [("pkg/mod.py", 3, "os_exit")]


def test_ordinary_binary_and_empty_files_in_a_change_still_pass(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "assets").mkdir()
    (repo / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe" + bytes(range(256)))
    (repo / "data.bin").write_bytes(b"\x00\x01\xff")
    head = commit_change(repo, {"pkg/__init__.py": "", "pkg/mod.py": "x = '中文'\n"})
    assert _found(repo, base, head) == []
    git(repo, "update-index", "--chmod=+x", "src/a.py")
    git(repo, "commit", "-q", "-m", "mode only")
    mode = git(repo, "rev-parse", "HEAD")
    assert _found(repo, head, mode) == []
    assert _found(repo, mode, commit_change(repo, delete=["pkg/mod.py", "pkg/__init__.py"])) == []


def test_the_scan_reads_only_py_files_so_other_text_may_use_any_encoding(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_bytes(b"caf\xe9\n")
    head = commit_change(repo, {"pkg/mod.py": "import os\nos._exit(0)\n"})
    assert _found(repo, base, head) == [("pkg/mod.py", 2, "os_exit")]


INDEX = "index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222"


def test_a_changed_py_file_without_lines_in_the_diff_is_unreadable():
    diff = (
        f"diff --git a/pkg/mod.py b/pkg/mod.py\n{INDEX} 100644\n"
        "Binary files a/pkg/mod.py and b/pkg/mod.py differ\n"
    )
    with pytest.raises(GitCommandError):
        added_lines(diff)


def test_a_new_empty_py_file_needs_no_lines():
    empty = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    diff = (
        "diff --git a/pkg/__init__.py b/pkg/__init__.py\nnew file mode 100644\n"
        f"index {'0' * 40}..{empty}\n"
    )
    assert added_lines(diff) == {}
