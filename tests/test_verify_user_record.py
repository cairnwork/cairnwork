"""#527: a target test module that looks up the user record at import (pwd.getpwuid) must not
make a correct fix UNCOMPARABLE in the sandbox. Seen on a real repository on 2026-11-06."""

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from verify_helpers import FIX, candidate, verify

USER_RECORD = (
    "import os, pwd\nHOME = pwd.getpwuid(os.getuid()).pw_dir\ndef test_home():\n    assert HOME\n"
)


@pytest.mark.sandbox
def test_a_test_module_that_reads_the_user_record_keeps_a_fix_comparable(state, project):
    files = {f"tests/test_m{i}.py": "def test_ok():\n    pass\n" for i in range(26)}
    commit_change(project, {"tests/test_user.py": USER_RECORD, **files})  # over the 25-file cap
    new = "from calc import add\ndef test_new():\n    assert add(1, 1) == 2\n"
    base, tc = make_test_commit(project, {"tests/test_new.py": new})
    fix = candidate(project, base, {"src/calc.py": FIX})
    _code, out = verify(
        state,
        project,
        base,
        fix,
        "--test-commit",
        tc,
        tests=("python3 -m pytest -q tests/test_new.py",),
    )
    assert out.get("status") == "VERIFIED", out
