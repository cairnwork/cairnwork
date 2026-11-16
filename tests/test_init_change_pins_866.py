"""#866: two #790 variants stay refused after #840, pinned so a later change cannot widen them.

Both refusals come from one fact: a candidate that touches ``pkg/__init__.py`` (a package directly
above a test root) unseals ``pkg`` (``sealed_packages``): the changed ``__init__`` can edit
``sys.path`` at import and put ``pkg`` in front, so its sibling names count again.

A: with the ``__init__`` change, a sibling named like a standard-library module is refused.
B: with the ``__init__`` change, deletions allowed: the ``__init__`` is refused (it could be
   deleted); deletions off: the deletion itself is refused (``DELETION_FORBIDDEN``).
These pin the current verdicts, not an endorsement of the false refusals (see
docs/PRESERVED_BEHAVIORS.md, #866). Loosening either must be a deliberate, reviewed change.
"""

import sys

import pytest
from conftest import commit_change, git

from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.workspace import (
    Attempt,
    CandidateRejected,
    _guarded_refs,
    collect_candidate,
    create_attempt_clone,
)

BASE = {
    "pkg/__init__.py": "",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_calc.py": "def test_a():\n    assert True\n",
    "pkg/old.py": "x = 1\n",
    "pkg/json.py": "x = 1\n",
    "pkg/util.py": "x = 1\n",
}
WORKER = ("-c", "user.email=w@example.invalid", "-c", "user.name=w")
VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "pkg/tests/test_calc.py")),)


def _task(repo, paths, allow_deletions):
    head = git(repo, "rev-parse", "HEAD")
    return TaskSpec("key", "card", "t", "fix", repo, head, paths, VERIFY,
                    test_roots=("pkg/tests",), allow_deletions=allow_deletions)  # fmt: skip


@pytest.mark.parametrize("allow_deletions", [False, True])
@pytest.mark.parametrize("sibling", ["pkg/json.py", "pkg/os.py"])
def test_a_stdlib_named_sibling_changed_with_the_init_is_refused(repo, sibling, allow_deletions):
    commit_change(repo, BASE | {"pkg/os.py": "x = 1\n"})
    with pytest.raises(TaskRejected, match=f"PROTECTED_VERIFIER_INPUT: .*{sibling}"):
        check_task(_task(repo, ("pkg/__init__.py", sibling), allow_deletions))


def test_the_same_init_change_with_a_plainly_named_sibling_passes(repo):
    commit_change(repo, BASE)  # the control: only the stdlib-like name is refused
    assert check_task(_task(repo, ("pkg/__init__.py", "pkg/util.py"), False))


def test_b_with_deletions_allowed_the_init_change_is_refused(repo):
    commit_change(repo, BASE)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/__init__.py$"):
        check_task(_task(repo, ("pkg/__init__.py", "pkg/old.py"), True))


def test_b_with_deletions_off_the_deletion_is_refused_at_collection(repo, tmp_path):
    commit_change(repo, BASE)
    base = git(repo, "rev-parse", "HEAD")
    clone = create_attempt_clone(repo, base, tmp_path / "attempts", "a1").path
    (clone / "pkg" / "__init__.py").write_text("# fixed\n")
    (clone / "pkg" / "old.py").unlink()
    git(clone, "add", "-A")
    git(clone, *WORKER, "commit", "-q", "-m", "candidate")
    made = Attempt("a1", base, clone, _guarded_refs(repo, "a1"))
    allowed = ("pkg/__init__.py", "pkg/old.py")
    with pytest.raises(CandidateRejected) as refused:
        collect_candidate(repo, made, allowed, allow_deletions=False, python=sys.executable)
    assert refused.value.code == "DELETION_FORBIDDEN"
