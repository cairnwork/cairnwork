"""Review findings for cairnwork#851 step 2, each red on the first head: magic pathspecs,
symlinked candidate conftest, and a test file whose name merely ends in conftest.py."""

from __future__ import annotations

import pytest

from experimental.plugin_select.scanner import ScanUnknown, safe_rel, scan_run
from tests.test_plugin_select_851 import make_repo


@pytest.mark.parametrize("path", [":(literal)a.py", ":/a.py", ":!a.py", ":(glob)*.py"])
def test_magic_pathspec_is_refused(path):
    with pytest.raises(ScanUnknown):
        safe_rel(path)


@pytest.mark.parametrize("path", ["tests/t*.py", "tests/t?st_a.py", "tests/[t]est_a.py"])
def test_glob_characters_are_matched_literally(tmp_path, path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "x = 1\n"})
    assert not scan_run(repo, base, [path]).classified


def test_symlinked_candidate_conftest_is_unknown(tmp_path):
    repo, base = make_repo(tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    candidate = tmp_path / "candidate"
    (candidate / "tests").mkdir(parents=True)
    real = tmp_path / "real_conftest.py"
    real.write_text("def f(mocker):\n    return mocker\n")
    (candidate / "tests" / "conftest.py").symlink_to(real)
    assert not scan_run(repo, base, ["tests/test_a.py"], candidate).classified


def test_name_ending_in_conftest_is_not_a_conftest(tmp_path):
    files = {"tests/test_conftest.py": "def test_a(mocker):\n    pass\n"}
    repo, base = make_repo(tmp_path, files)
    candidate = tmp_path / "candidate"
    (candidate / "tests").mkdir(parents=True)
    (candidate / "tests" / "test_conftest.py").write_text("def f(mocker):\n    return mocker\n")
    scan = scan_run(repo, base, ["tests/test_conftest.py"], candidate)
    assert scan.classified
    assert "mocker" in scan.names
