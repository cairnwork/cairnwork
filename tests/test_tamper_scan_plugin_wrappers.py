"""#848: stdlib wrappers of compile are refused; a huge added file is refused fast, by budget."""

import time

import pytest
from conftest import commit_change, git

from cairnwork.tamper_scan import scan_added_lines, scan_patch


@pytest.mark.parametrize(
    "line",
    [
        "code = Loader.source_to_code(src, 'x.py')",
        "import codeop",
        "from codeop import Compile",
    ],
)
def test_a_stdlib_code_object_wrapper_is_refused(line):
    assert [f.rule for f in scan_added_lines("src/a.py", [(3, line)])] == ["code_object_forge"]


def test_an_honest_line_with_a_similar_name_is_not_refused():
    assert scan_added_lines("src/a.py", [(1, "def source_to_text(x): return x")]) == []


def test_a_huge_added_file_is_refused_by_the_budget_and_fast(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")  # CI has no global identity
    git(repo, "config", "user.name", "test")
    commit_change(repo, {"src/a.py": "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    sha = commit_change(repo, {"src/a.py": "X = [" + "0," * 1_500_000 + "]\n"})
    start = time.perf_counter()
    rules = {f.rule for f in scan_patch(repo, base, sha)}
    assert time.perf_counter() - start < 1
    assert {"long_line", "scan_budget"} <= rules
