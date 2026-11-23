"""#353 on a real bwrap: a credential link the host re-points while a run is going leaves its new
target readable inside the run, so the run must not be counted. Linux CI only: the owner-mode
witness runs in job test-linux-sandbox, the account one in test-linux-verifier-account. Without a
working bwrap (macOS) they skip; on Linux with CAIRNWORK_REQUIRE_SANDBOX=1 a missing bwrap fails.
The policy-level cases (any host) are in tests/test_bwrap_mask_recheck.py."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fixture_tree import world_dir

from cairnwork.mask_recheck import MaskChanged, guard
from cairnwork.verifier_account import VerifierAccount
from cairnwork_sandbox import bwrap

pytestmark = pytest.mark.sandbox

CANARY = "CW-353-CANARY\n"
# The run says it is ready, waits for the host's re-point, then prints what the link now reaches.
SCRIPT = (
    'touch "$1/ready"; i=0; '
    'while [ ! -e "$1/go" ] && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done; '
    'cat "$2/.ssh/id"'
)


@pytest.fixture(autouse=True)
def _real_bwrap():
    if bwrap.available():
        return
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1" and sys.platform == "linux":
        pytest.fail("CAIRNWORK_REQUIRE_SANDBOX=1 but bwrap is unavailable")
    pytest.skip("needs a working bwrap (Linux)")


@pytest.fixture
def tree():
    """home/.ssh -> keys/v1 (empty); keys/v2/id holds the canary; run/ is the write root."""
    with world_dir("cw353-") as base:
        base = base.resolve()
        (base / "keys" / "v1").mkdir(parents=True)
        (base / "keys" / "v2").mkdir()
        (base / "keys" / "v2" / "id").write_text(CANARY)
        (base / "home").mkdir()
        (base / "home" / ".ssh").symlink_to(base / "keys" / "v1")
        (base / "run").mkdir()
        for path in (base / "run", base / "home", base / "keys" / "v1", base / "keys" / "v2"):
            os.chmod(path, 0o777 if path.name == "run" else 0o755)
        os.chmod(base / "keys" / "v2" / "id", 0o644)
        yield base


def _host_repoints(base: Path) -> threading.Thread:
    """After the run signals ready, point .ssh at keys/v2, then let the run go on."""

    def work() -> None:
        deadline = time.monotonic() + 60
        while not (base / "run" / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        link = base / "home" / ".ssh"
        link.unlink()
        link.symlink_to(base / "keys" / "v2")
        (base / "run" / "go").write_text("")

    thread = threading.Thread(target=work)
    thread.start()
    return thread


def _run(base: Path, owner: bool, seen: list[subprocess.CompletedProcess]) -> None:
    """The guarded run: the sandboxed command, with the host re-pointing the link meanwhile."""
    policy = bwrap.BACKEND.policy(
        (), (base / "run",), home=base / "home", mask_home_credentials=owner
    )
    (base / "policy.txt").write_text(policy.text)
    argv = ["/bin/sh", "-c", SCRIPT, "sh", str(base / "run"), str(base / "home")]
    thread = _host_repoints(base)
    try:
        with guard(bwrap.BACKEND, policy):
            if owner:
                wrapped = bwrap.wrap(argv, base / "policy.txt")
                seen.append(subprocess.run(wrapped, capture_output=True, timeout=90, check=False))  # noqa: S603
            else:
                account = VerifierAccount()
                with account.exclusive(60):
                    wrapped = account.wrap(argv, base / "policy.txt")
                    seen.append(
                        subprocess.run(  # noqa: S603
                            wrapped, capture_output=True, timeout=90, check=False, cwd=account.root
                        )
                    )
    finally:
        thread.join()


def test_a_link_repointed_during_an_owner_run_is_not_counted(tree):
    seen: list[subprocess.CompletedProcess] = []
    with pytest.raises(MaskChanged, match=r"\.ssh"):
        _run(tree, True, seen)
    # Witness: the run read the canary through the new link, so the mask had really stopped holding.
    assert seen and seen[0].stdout.decode() == CANARY, seen


@pytest.mark.verifier_account
def test_a_link_repointed_during_an_account_run_is_not_counted(tree):
    seen: list[subprocess.CompletedProcess] = []
    with pytest.raises(MaskChanged, match=r"\.ssh"):
        _run(tree, False, seen)
    assert seen and seen[0].stdout.decode() == CANARY, seen
