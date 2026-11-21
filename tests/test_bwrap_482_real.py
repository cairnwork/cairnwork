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
from pathlib import Path

import pytest

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
    from cairnwork.verifier_account import VerifierAccount

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
    result = subprocess.run(argv, capture_output=True, timeout=75, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    text = result.stdout.decode()
    assert base.name not in text
    return text


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
    if "truncated|-|-" in report:
        pytest.skip("socket walk stopped early (time or entry cap); no category count")
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
        policy = bwrap.BACKEND.policy(
            (script,), (), home=base / "home", mask_home_credentials=False
        )
        policy_path = base / "walk-policy.txt"
        policy_path.write_text(policy.text)
        from cairnwork.verifier_account import VerifierAccount

        account = VerifierAccount()
        with account.exclusive(90):
            result = subprocess.run(
                account.wrap([sys.executable, str(script)], policy_path),
                capture_output=True,
                timeout=100,
                check=False,
                cwd=account.root,
            )  # noqa: S603
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        report_text = result.stdout.decode()
        assert socket_dir.name not in report_text
        report = json.loads(report_text)
        if "truncated|-|-" in report:
            pytest.skip("socket walk stopped early (time or entry cap); no category count")
        assert sum(count for key, count in report.items() if key.startswith("var-tmp|")) >= 1
        print(report_text)
        warnings.warn(report_text, stacklevel=2)
    finally:
        if listener is not None:
            listener.close()
        socket_path.unlink(missing_ok=True)
        socket_dir.rmdir()
