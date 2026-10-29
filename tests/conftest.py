import json
import sqlite3
import subprocess
import sys

import pytest
from xdist_groups import XDIST_GROUP_OF

pytest_plugins = ["verify_helpers"]


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def tamper_stored_request(db, mutate):
    """Rewrite the stored request of the only notary row (the append-only trigger is dropped)."""
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER core_verifications_no_update")
    request = json.loads(conn.execute("SELECT request_json FROM core_verifications").fetchone()[0])
    mutate(request)
    conn.execute("UPDATE core_verifications SET request_json = ?", (json.dumps(request),))
    conn.commit()
    conn.close()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main", "--template=")  # no hook samples: ~20k fewer files (#417)
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("x = 1\n")
    (path / "src" / "b.py").write_text("y = 1\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


def commit_change(repo, files=None, delete=(), rename=None, message="candidate"):
    for name, text in (files or {}).items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for name in delete:
        git(repo, "rm", "-q", name)
    if rename:
        git(repo, "mv", *rename)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# How long collection waits for another run's account lock before it skips the account tests.
ACCOUNT_PROBE_LOCK_SECONDS = 300.0


def _account_state(items) -> str | None:
    """'usable', 'unusable' or 'busy'; None when no collected test needs the account.

    available() starts a process as the shared account, so it runs under the account lock like
    every live run (Issue #247): started outside it, another session's locked run counted it as
    a leftover ("1 process(es) outlived the run") and killed it."""
    import pwd

    from cairnwork import verifier_account as va

    if not any("verifier_account" in item.keywords for item in items):
        return None
    account = va.VerifierAccount()
    try:
        pwd.getpwnam(account.user)
    except KeyError:
        return "unusable"  # no such user: nothing to lock or probe
    try:
        with account.exclusive(ACCOUNT_PROBE_LOCK_SECONDS):
            return "usable" if account.available() else "unusable"
    except va.AccountBusy:
        return "busy"
    except (OSError, va.VerifierAccountError):
        return "unusable"


@pytest.hookimpl(trylast=True)  # after -m/-k deselection: unselected account tests probe nothing
def pytest_collection_modifyitems(config, items):
    """Tests marked 'sandbox' need real macOS Seatbelt. CAIRNWORK_REQUIRE_SANDBOX=1 (the
    macOS CI job) makes a missing sandbox an error; elsewhere those tests are skipped."""
    import os

    import cairnwork_sandbox

    state = _account_state(items)
    if state in ("unusable", "busy"):
        if os.environ.get("CAIRNWORK_REQUIRE_VERIFIER_ACCOUNT") == "1":
            raise pytest.UsageError(
                f"CAIRNWORK_REQUIRE_VERIFIER_ACCOUNT=1 but the account is {state}"
            )
        reason = (
            "the cairnwork-verifier account is busy (another run holds its lock)"
            if state == "busy"
            else "needs the cairnwork-verifier account"
        )
        skip_account = pytest.mark.skip(reason=reason)
        for item in items:
            if "verifier_account" in item.keywords:
                item.add_marker(skip_account)
    if cairnwork_sandbox.select() is not None:
        return
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1":
        raise pytest.UsageError("CAIRNWORK_REQUIRE_SANDBOX=1 but sandbox-exec is unavailable")
    skip = pytest.mark.skip(reason="needs macOS sandbox-exec")
    for item in items:
        if "sandbox" in item.keywords:
            item.add_marker(skip)


def pytest_itemcollected(item):
    """Mark the files of xdist_groups.XDIST_GROUPS (#406); xdist reads the mark later, in its own
    pytest_collection_modifyitems."""
    group = XDIST_GROUP_OF.get(item.path.name)
    if group is not None:
        item.add_marker(pytest.mark.xdist_group(group))


@pytest.fixture(autouse=True)
def _a_verifier_python_the_gate_can_probe(request, monkeypatch):
    """A task without verifier_python uses the verifier account's (intake). On a host without
    that venv (a CI runner with no account) the shadow gate's probe cannot run and refuses every
    deep path, fail closed (#384); such tests get this interpreter instead. Hosts that have the
    venv, tests of the real account, and accounts with another root keep theirs
    (test_namespace_shadow_e2e pins that a missing venv does fail closed)."""
    from pathlib import Path

    from cairnwork.verifier_account import DEFAULT_ROOT, VerifierAccount

    if request.node.get_closest_marker("verifier_account"):
        return  # the real account and its own venv
    if not Path(VerifierAccount().python).exists():
        here = sys.executable

        def python(self):
            return here if self.root == DEFAULT_ROOT else str(self.root / "venv" / "bin" / "python")

        monkeypatch.setattr(VerifierAccount, "python", property(python))


@pytest.fixture(autouse=True)
def _no_origin_in_test_repos(request, monkeypatch):
    """Test repositories have no origin; tests of the lineage check set the key to true.
    A test marked no_origin_patch sees the real default."""
    if request.node.get_closest_marker("no_origin_patch"):
        return
    from cairnwork import intake

    monkeypatch.setitem(intake.DEFAULT_CONFIG, "require_base_on_main", False)
