import json
import os
import sqlite3
import subprocess
import sys

import pytest
from xdist_groups import XDIST_GROUP_OF

pytest_plugins = ["verify_helpers"]


# Fewer files per test git repo (#417): the tests' own git init/clone (subprocesses included)
# copy no template directory (hook samples, info/exclude, description) and keep no reflogs.
# Code under test must see the production git environment. Git calls that inherit it:
# - cairnwork.gitcmd run_git/run_git_bytes (every core git call) and
#   experimental/bm25_hints/commit_source.py: drop every GIT_* variable themselves.
# - cairnwork_attest.publish._git ({**os.environ, ...}): its tests (test_publish*.py,
#   test_on_verified.py, test_check_run_gh.py) use the production_git_env fixture below.
# - experimental/local_worker's git queries run under ``env -i``.
# - experimental/bm25_hints/eval_hit_at_k.py (clone --shared, checkout; run by test_bm25_hints)
#   only reads the checked-out files afterwards: no template, hook or reflog is involved.
# - scripts/measure_p2p.py (clone, commit, push) and the scripts/drill_*.py demos (init, commit)
#   are not run by any test; scripts/env_smoke.py (run by test_env_smoke) only reads refs/config.
# No other src code runs git with the inherited environment.
TEST_GIT_ENV = {
    "GIT_TEMPLATE_DIR": "",  # empty: no template, as with --template=
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.logAllRefUpdates",
    "GIT_CONFIG_VALUE_0": "false",
}
# Which of them conftest itself set, recorded in the environment: this module may be imported
# twice (as pytest's conftest and by ``from conftest import``) and xdist workers inherit the
# controller's environment, so only the first import in the process tree decides.
INJECTED_VAR = "CW_TEST_GIT_ENV_INJECTED"


def inject_test_git_env(environ) -> None:
    """Set each test-only variable the environment does not already define, once."""
    if INJECTED_VAR in environ:
        return
    keys = [] if "GIT_TEMPLATE_DIR" in environ else ["GIT_TEMPLATE_DIR"]
    config = [k for k in TEST_GIT_ENV if k.startswith("GIT_CONFIG_")]
    if not any(k in environ for k in config):  # never mix with an existing GIT_CONFIG_* set
        keys += config
    environ.update({k: TEST_GIT_ENV[k] for k in keys})
    environ[INJECTED_VAR] = " ".join(keys)


def injected_test_git_env(environ) -> list[str]:
    """The variables conftest set that still hold its value."""
    keys = environ.get(INJECTED_VAR, "").split()
    return [k for k in keys if k in TEST_GIT_ENV and environ.get(k) == TEST_GIT_ENV[k]]


inject_test_git_env(os.environ)


@pytest.fixture
def production_git_env(monkeypatch):
    """Remove the variables conftest set, and only those."""
    for key in injected_test_git_env(os.environ):
        monkeypatch.delenv(key)


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
