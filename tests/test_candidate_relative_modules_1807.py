"""Issue #1807: a relative from-import names the package's own module, not an import-machinery one.

Every case commits a base package and a candidate that edits it, then calls
``refuse_new_sys_path_mutation`` on the real change set. MUST_PASS shapes were refused on main and
must pass; MUST_BLOCK shapes were refused on main and must stay refused (nothing here may be
judged more loosely than on main).
"""

import pytest
from conftest import git

from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.workspace import CandidateRejected

IMPORTER = "pkg/validator.py"


def _verdict(tmp_path, importer, own_module, added_module=False):
    """Base: a package with ``own_module`` (unless added later) and a plain importer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    (repo / "pkg").mkdir()
    (repo / "pkg/__init__.py").write_text("")
    (repo / IMPORTER).write_text("X = 1\n")
    if not added_module:
        (repo / own_module).write_text("def ok():\n    return 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / IMPORTER).write_text(importer)
    (repo / own_module).write_text("def ok():\n    return 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    cand = git(repo, "rev-parse", "HEAD")
    try:
        refuse_new_sys_path_mutation(repo, base, cand, [IMPORTER, own_module])
    except CandidateRejected as err:
        return err.code
    return None


@pytest.mark.parametrize("name", ["types", "code", "inspect", "site", "trace", "gc"])
def test_relative_import_of_an_own_module_named_like_machinery_passes(tmp_path, name):
    importer = f"from .{name} import ok\n\ndef f():\n    return ok()\n"
    assert _verdict(tmp_path, importer, f"pkg/{name}.py") is None


@pytest.mark.parametrize(
    ("importer", "own_module", "added"),
    [
        # a newly added package file with a stdlib name, next to a signal file
        ("from .types import ok\nimport sys\nsys.path.insert(0, '.')\n", "pkg/types.py", True),
        # absolute imports of machinery modules, and sys.path mutation
        ("from types import ok\n", "pkg/types.py", False),
        ("from site import ok\n", "pkg/site.py", False),
        ("import types\n", "pkg/types.py", False),
        ("import sys\nsys.path.insert(0, '.')\nfrom .types import ok\n", "pkg/types.py", False),
        # a relative import that brings in a held name
        ("from . import importlib\n", "pkg/types.py", False),
        ("from .x import importlib\n", "pkg/types.py", False),
        ("from .x import site\n", "pkg/types.py", False),
        ("from .x import runpy\n", "pkg/types.py", False),
        ("from .x import logging_config\nfrom .x import config\n", "pkg/types.py", False),
        # empty module part: ``from . import <same-named module>`` is decided as refused, as on main
        ("from . import types\n", "pkg/types.py", False),
        ("from . import site\n", "pkg/site.py", False),
        # a relative import followed by a dynamic import call
        ("from .types import ok\n__import__('os')\n", "pkg/types.py", False),
        ("from .types import ok\nimport importlib\n", "pkg/types.py", False),
        ("from .types import ok, getattr\n", "pkg/types.py", False),
    ],
)
def test_shapes_that_main_refused_stay_refused(tmp_path, importer, own_module, added):
    code = _verdict(tmp_path, importer, own_module, added_module=added)
    assert code == "CANDIDATE_SYS_PATH_MUTATION"
