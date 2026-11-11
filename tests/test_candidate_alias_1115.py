"""Tests for Issue #1115: aliased module swaps and run-time written shadows."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected


def _run(repo, base, cand, allowed=None):
    if allowed is None:
        allowed = tuple(git(repo, "diff", "--name-only", base, cand).split())
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, allowed)


@pytest.mark.parametrize(
    "src",
    [
        "import sys as s\ns.modules['calc'] = object()\n",
        "import sys\nm = sys\nm.modules['calc'] = object()\n",
        "from sys import modules\nmodules['calc'] = object()\n",
    ],
)
def test_sys_alias_module_swap_is_machinery(repo, src):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\n" + src})
    with pytest.raises(CandidateRejected) as got:
        _run(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "in-process" in str(got.value)


def test_table_alias_added_beside_an_unchanged_import_is_machinery(repo):
    commit_change(repo, {"src/a.py": "from sys import modules\nx = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "from sys import modules\nx = 1\nmodules['calc'] = object()\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        _run(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "in-process" in str(got.value)


@pytest.mark.parametrize(
    "src",
    [
        (
            "import sys, tempfile, pathlib\n"
            "d = tempfile.mkdtemp()\n"
            "pathlib.Path(d, 'calc.py').write_text('X = 2\\n')\n"
            "sys.path.insert(0, d)\n"
        ),
        (
            "import sys, tempfile, os\n"
            "d = tempfile.mkdtemp()\n"
            "with open(os.path.join(d, 'calc.py'), 'w') as f:\n"
            "    f.write('X = 2\\n')\n"
            "sys.path.insert(0, d)\n"
        ),
    ],
)
def test_runtime_written_shadow_on_the_path_is_refused(repo, src):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\n" + src})
    with pytest.raises(CandidateRejected) as got:
        _run(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "writes a file" in str(got.value)


def test_alias_swap_in_an_added_file_is_machinery(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"new_module.py": "import sys as s\ns.modules['calc'] = object()\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        _run(repo, base, cand, ("new_module.py",))
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "in-process" in str(got.value)


def test_removing_a_base_alias_swap_is_machinery(repo):
    commit_change(
        repo,
        {"src/a.py": "import sys as s\ns.modules.pop('calc', None)\nx = 1\n"},
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "import sys as s\nx = 1\n"})
    with pytest.raises(CandidateRejected) as got:
        _run(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "in-process" in str(got.value)


def test_safe_sys_alias_attribute_is_not_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\nimport sys as s\nV = s.version\n"})
    _run(repo, base, cand)


def test_write_without_a_path_edit_is_not_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "x = 1\nimport pathlib\npathlib.Path('out.txt').write_text('x')\n"},
    )
    _run(repo, base, cand)


def test_path_edit_without_a_write_is_not_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "x = 1\nimport sys\nsys.path.insert(0, 'src')\n"},
    )
    _run(repo, base, cand)


def test_unchanged_alias_use_beside_an_honest_one_line_fix_is_not_refused(repo):
    base_src = (
        "import sys as s\nSEEN = s.modules.get('json')\ndef add(a, b):\n    return a + b + 1\n"
    )
    commit_change(repo, {"src/a.py": base_src})
    base = git(repo, "rev-parse", "HEAD")
    cand_src = "import sys as s\nSEEN = s.modules.get('json')\ndef add(a, b):\n    return a + b\n"
    cand = commit_change(repo, {"src/a.py": cand_src})
    _run(repo, base, cand)


def test_os_path_helper_beside_a_write_is_not_refused(repo):
    src = (
        "import os, pathlib\n"
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "pathlib.Path(HERE, 'out.txt').write_text('x')\n"
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\n" + src})
    _run(repo, base, cand)


def test_write_in_one_file_and_path_edit_in_another_is_not_combined(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "new_a.py": "import pathlib\npathlib.Path('o.txt').write_text('x')\n",
            "new_b.py": "import sys\nsys.path.insert(0, 'src')\n",
        },
    )
    _run(repo, base, cand, ("new_a.py", "new_b.py"))


def test_plain_import_sys_without_machinery_attributes_is_not_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/a.py": "x = 1\nimport sys as s\nprint(s.argv)\n"})
    _run(repo, base, cand)
