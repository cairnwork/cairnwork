"""The bytecode hook under the verifier account, and the host note on ptrace_scope (Issue #276).

Under the separate verifier account the run's pytest process holds the same hook, so bytecode
planted for a protected helper and removed again before the run ends is never loaded there either:
the helper's own source runs and the deferred test fails. These need the account
(pytest.mark.verifier_account; CI's Linux account job runs them on real bwrap).

``host_notes`` reads Yama's ptrace_scope outside the sandbox: below 1 a child of the run could
write the memory of the pytest process holding the hook, which the run then states in its notes.
"""

import sys

import pytest
from test_pycache_redirect import ACCOUNT, account_run_root, redirecting, run_one

from cairnwork import bytecode_hook

assert account_run_root  # a fixture used by name below

REMOVED_AT_EXIT = {
    "prefix_removed": "import atexit, shutil\natexit.register(shutil.rmtree, sys.pycache_prefix)\n",
    "pyc_only_removed": (
        "import atexit\n"
        "atexit.register(lambda: [os.remove(os.path.join(r, f))"
        " for r, _, fs in os.walk(sys.pycache_prefix) for f in fs if f.endswith('.pyc')])\n"
    ),
}


@pytest.mark.verifier_account
@pytest.mark.parametrize("how", sorted(REMOVED_AT_EXIT))
def test_account_bytecode_removed_before_the_run_ends_is_not_loaded(
    repo, tmp_path, how, account_run_root
):
    files = {
        "src/evil.py": redirecting("home", "tests/helper.py", "OK = True\n", False)
        + REMOVED_AT_EXIT[how],
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "from helper import OK\n\ndef test_z():\n    assert OK\n",
    }
    run = run_one(repo, tmp_path, files, "tests/test_a.py", "tests/test_z.py", account=ACCOUNT)
    assert run.returncode == 1, run.log_path.read_text()
    assert not run.observation.passed
    assert run.outcomes.get("tests/test_z.py::test_z") == "failed", run.outcomes


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.mark.parametrize("value", ["1\n", "2\n", "3\n"])
def test_a_restricted_ptrace_scope_adds_no_note(tmp_path, monkeypatch, linux, value):
    (tmp_path / "scope").write_text(value)
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", tmp_path / "scope")
    assert bytecode_hook.host_notes() == ()


@pytest.mark.parametrize(
    ("content", "shown"),
    [("0\n", "0"), ("", "empty"), (None, "unreadable"), ("\xff", "unreadable")],
)
def test_an_open_or_unknown_ptrace_scope_is_noted(tmp_path, monkeypatch, linux, content, shown):
    scope = tmp_path / "scope"
    if content is not None:
        scope.write_text(content, encoding="latin-1")
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", scope)
    assert bytecode_hook.host_notes() == (f"{bytecode_hook.PTRACE_NOTE}{shown}",)


def test_other_platforms_have_no_ptrace_note(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", tmp_path / "missing")
    assert bytecode_hook.host_notes() == ()
