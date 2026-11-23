"""#482 on a real bwrap: a daemon-socket link that resolves into a home the account cannot enter
must not make the run fail at mount. Linux CI only: the control runs in job test-linux-sandbox
(same-uid bwrap), the verifier-account witnesses in test-linux-verifier-account (a different uid
that cannot enter the owner's home). Without a working bwrap (macOS) they skip, but on Linux with
CAIRNWORK_REQUIRE_SANDBOX=1 a missing bwrap fails. test_bwrap_482.py has the rule as text."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fixture_tree import world_dir

from cairnwork.mask_recheck import MaskChanged, guard
from cairnwork.verifier_account import VerifierAccount
from cairnwork_sandbox import bwrap

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _real_bwrap():
    """Skip without bwrap; a Linux job that requires the sandbox fails instead."""
    if bwrap.available():
        return
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1" and sys.platform == "linux":
        pytest.fail("CAIRNWORK_REQUIRE_SANDBOX=1 but bwrap is unavailable")
    pytest.skip("needs a working bwrap (Linux)")


@pytest.fixture
def fixture_tree(monkeypatch):
    """home/.rd/docker.sock (0700, not enterable by the account) and run/docker.sock linking to it.
    Built under the user's home, not /tmp: a run mounts a private /tmp over the real one, so a mask
    path under /tmp would fail for that reason alone."""
    base = Path(tempfile.mkdtemp(prefix="cw482-", dir=Path.home())).resolve()
    rd = base / "home" / ".rd"
    (base / "run").mkdir()
    rd.mkdir(parents=True)
    (rd / "docker.sock").write_text("")
    rd.chmod(0o700)
    link = base / "run" / "docker.sock"
    link.symlink_to(rd / "docker.sock")
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(link),))
    yield base, rd
    rd.chmod(0o700)
    shutil.rmtree(base, ignore_errors=True)


def _run(base: Path, owner: bool) -> subprocess.CompletedProcess:
    """Build the policy for this fixture and run a trivial command under it."""
    policy = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=owner)
    path = base / "policy.txt"
    path.write_text(policy.text)
    argv = bwrap.wrap(["/bin/true"], path)
    return subprocess.run(argv, capture_output=True, timeout=30, check=False)  # noqa: S603


def _mask_flags(base: Path, rd: Path, owner: bool) -> list[str]:
    policy = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=owner)
    return [ln for ln in policy.text.splitlines() if ln == str(rd / "docker.sock")]


def test_owner_path_still_masks_the_socket(fixture_tree):
    """Owner path (same uid, mask_home_credentials=True): the mask flag is still emitted."""
    base, rd = fixture_tree
    assert _mask_flags(base, rd, owner=True) == [str(rd / "docker.sock")]
    assert _run(base, owner=True).returncode == 0


@pytest.fixture
def account_tree(monkeypatch):
    """Owner home (0700, owned by this process) holding .rd/docker.sock and a 0600 fake
    credential; the verifier account is another uid and cannot enter it."""
    base = Path(tempfile.mkdtemp(prefix="cw482a-", dir=Path.home())).resolve()
    base.chmod(0o700)
    rd = base / "home" / ".rd"
    (base / "run").mkdir()
    rd.mkdir(parents=True)
    (rd / "docker.sock").write_text("")
    rd.chmod(0o700)
    cred = base / "home" / ".aws" / "credentials"
    cred.parent.mkdir()
    cred.write_text("aws_secret_access_key=owner-secret\n")
    cred.chmod(0o600)
    link = base / "run" / "docker.sock"
    link.symlink_to(rd / "docker.sock")
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(link),))
    yield base, rd, cred
    shutil.rmtree(base, ignore_errors=True)


def _account_run(base: Path, argv: list[str], text: str = "") -> subprocess.CompletedProcess:
    """Run argv as the verifier account on real bwrap, with ``text`` or else the policy the account
    path builds (mask_home_credentials=False); VerifierAccount.wrap adds sudo -n -u <account>."""
    path = base / "policy.txt"
    path.write_text(
        text or bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=False).text
    )
    account = VerifierAccount()
    with account.exclusive(60):
        return subprocess.run(  # noqa: S603
            account.wrap(argv, path), capture_output=True, timeout=60, check=False, cwd=account.root
        )


@pytest.mark.verifier_account
def test_account_run_starts_without_the_mask_and_cannot_read_owner_credentials(account_tree):
    """Verifier account (other uid, cannot enter the owner's 0700 home): no mask flag for the
    unreachable socket, the run starts, and the owner's 0600 credential stays unreadable."""
    base, rd, cred = account_tree
    policy = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=False)
    assert str(rd / "docker.sock") not in policy.text.splitlines()
    started = _account_run(base, ["/bin/true"])
    assert started.returncode == 0, started.stderr.decode(errors="replace")
    read = _account_run(base, ["/bin/cat", str(cred)])
    assert read.returncode != 0
    assert b"owner-secret" not in read.stdout + read.stderr


@pytest.mark.verifier_account
def test_account_run_fails_with_the_unreachable_mask(account_tree, monkeypatch):
    """Control for the test above: forcing the reach rule to True for the socket's directory only
    makes the account path emit that one mask (nothing else changes), and then the run fails at
    mount on the socket."""
    base, rd, _cred = account_tree
    honest = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=False)
    socket_dir, real_reach = os.path.realpath(rd), bwrap.account_may_traverse
    monkeypatch.setattr(
        bwrap, "account_may_traverse", lambda path: path == socket_dir or real_reach(path)
    )
    policy = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=False)
    lines = policy.text.splitlines()
    mask = ["--ro-bind", "/dev/null", str(rd / "docker.sock")]
    at = next((i for i in range(len(lines)) if lines[i : i + 3] == mask), -1)
    assert at >= 0, policy.text
    assert lines[:at] + lines[at + 3 :] == honest.text.splitlines()  # only the socket mask added
    failed = _account_run(base, ["/bin/true"], policy.text)
    assert failed.returncode != 0
    assert b"docker.sock" in failed.stderr, failed.stderr.decode(errors="replace")


def _socket_report(base: Path, owner: bool) -> str:
    """Run the measurement script with the fixture home and script readable."""
    script = Path(__file__).parents[1] / "scripts" / "socket_walk_471.py"
    policy = bwrap.BACKEND.policy((script,), (), home=base / "home", mask_home_credentials=owner)
    policy_path = base / "walk-policy.txt"
    policy_path.write_text(policy.text)
    argv = bwrap.wrap([sys.executable, str(script)], policy_path)
    result = subprocess.run(argv, capture_output=True, timeout=100, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    text = result.stdout.decode()
    assert base.name not in text
    return text


def _publish_walk(label: str, report_text: str) -> None:
    """Append the categories-only report to the CI job summary, from outside the sandbox (#471)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"\n#471 socket walk ({label}): `{report_text.strip()}`\n")
    except OSError:
        pass


def _require_priority_trees_walked(report: dict, report_text: str) -> None:
    """Fail unless every tree except the deferred /opt was walked (#1603).

    The walk visits /opt last because it holds ~240k toolchain files on CI runners and used to
    use the whole time budget before home and /var/tmp. A stop inside /opt is not a failure of
    these witnesses; a stop anywhere else leaves no `priority-complete` mark and fails here.
    """
    if "priority-complete|-|-" not in report:
        print(report_text)
        pytest.fail("socket walk stopped before the priority trees were walked; no category count")


def test_owner_socket_walk_reports_categories_only(fixture_tree):
    import json
    import socket
    import warnings

    base, _rd = fixture_tree
    home = base / "home"
    socket_path = home / "private-walk-token.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(socket_path))
    listener.listen()
    try:
        report_text = _socket_report(base, True)
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)
    report = json.loads(report_text)
    assert isinstance(report, dict)
    _publish_walk("owner", report_text)
    _require_priority_trees_walked(report, report_text)
    assert sum(count for key, count in report.items() if key.startswith("home|")) >= 1
    print(report_text)
    warnings.warn(report_text, stacklevel=2)


@pytest.mark.verifier_account
def test_verifier_socket_walk_reports_categories_only(account_tree):
    import json
    import socket
    import warnings

    base, _rd, _cred = account_tree
    socket_dir = Path(tempfile.mkdtemp(prefix="cw471-", dir="/var/tmp"))
    socket_path = socket_dir / "probe.sock"
    listener = None
    try:
        listener = socket.socket(socket.AF_UNIX)
        socket_dir.chmod(0o755)
        listener.bind(str(socket_path))
        socket_path.chmod(0o666)
        listener.listen()
        script = Path(__file__).parents[1] / "scripts" / "socket_walk_471.py"
        # the account cannot exec the venv or read the checkout: system python, source via -c
        policy = bwrap.BACKEND.policy((), (), home=base / "home", mask_home_credentials=False)
        policy_path = base / "walk-policy.txt"
        policy_path.write_text(policy.text)
        account = VerifierAccount()
        with account.exclusive(120):
            result = subprocess.run(
                account.wrap(["/usr/bin/python3", "-I", "-c", script.read_text()], policy_path),
                capture_output=True,
                timeout=130,
                check=False,
                cwd=account.root,
            )  # noqa: S603
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        report_text = result.stdout.decode()
        assert socket_dir.name not in report_text
        report = json.loads(report_text)
        _publish_walk("verifier account", report_text)
        _require_priority_trees_walked(report, report_text)
        # /var/tmp is a private tree since #471 step 2b: the walk must not see the socket
        assert sum(count for key, count in report.items() if key.startswith("var-tmp|")) == 0
        print(report_text)
        warnings.warn(report_text, stacklevel=2)
    finally:
        if listener is not None:
            listener.close()
        socket_path.unlink(missing_ok=True)
        socket_dir.rmdir()


# --- #353: a credential link re-pointed while a run is going (the guard must refuse the run) ---
CANARY_353 = "CW-353-CANARY\n"
# The run says it is ready, waits for the host's re-point, then prints what the link now reaches.
SCRIPT_353 = (
    'touch "$1/ready"; i=0; '
    'while [ ! -e "$1/go" ] && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done; '
    'cat "$2/.ssh/id"'
)


@pytest.fixture
def repoint_tree():
    """home/.ssh -> keys/v1 (empty); keys/v2/id holds the canary; run/ is the write root."""
    with world_dir("cw353-") as base:
        base = base.resolve()
        (base / "keys" / "v1").mkdir(parents=True)
        (base / "keys" / "v2").mkdir()
        (base / "keys" / "v2" / "id").write_text(CANARY_353)
        (base / "home").mkdir()
        (base / "home" / ".ssh").symlink_to(base / "keys" / "v1")
        (base / "run").mkdir()
        for path in (base / "run", base / "home", base / "keys" / "v1", base / "keys" / "v2"):
            os.chmod(path, 0o777 if path.name == "run" else 0o755)
        os.chmod(base / "keys" / "v2" / "id", 0o644)
        yield base


def _repoint_during_run(base: Path) -> threading.Thread:
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


def _guarded_run(base: Path, owner: bool, seen: list[subprocess.CompletedProcess]) -> None:
    """The guarded run: the sandboxed command, with the host re-pointing the link meanwhile."""
    policy = bwrap.BACKEND.policy(
        (), (base / "run",), home=base / "home", mask_home_credentials=owner
    )
    (base / "policy.txt").write_text(policy.text)
    argv = ["/bin/sh", "-c", SCRIPT_353, "sh", str(base / "run"), str(base / "home")]
    thread = _repoint_during_run(base)
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


def test_a_link_repointed_during_an_owner_run_is_not_counted(repoint_tree):
    seen: list[subprocess.CompletedProcess] = []
    with pytest.raises(MaskChanged, match=r"\.ssh"):
        _guarded_run(repoint_tree, True, seen)
    # Witness: the run read the canary through the new link, so the mask had really stopped holding.
    assert seen and seen[0].stdout.decode() == CANARY_353, seen


@pytest.mark.verifier_account
def test_a_link_repointed_during_an_account_run_is_not_counted(repoint_tree):
    seen: list[subprocess.CompletedProcess] = []
    with pytest.raises(MaskChanged, match=r"\.ssh"):
        _guarded_run(repoint_tree, False, seen)
    assert seen and seen[0].stdout.decode() == CANARY_353, seen
