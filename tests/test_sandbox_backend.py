"""The sandbox backend interface (cairnwork_sandbox) and the Seatbelt profile bytes.

The profile hashes below are pinned as of the POSIX-semaphore change (#116),
with the same fixed inputs: building a profile does not need sandbox-exec. Council action 3
(C1) added exactly the two /usr/bin/shasum read literals; the test removes those two lines
and checks the rest is still byte-for-byte the #116 profile.
"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

import cairnwork_sandbox
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError
from cairnwork_sandbox import seatbelt

HOME = Path("/Users/fixture")
READS = (
    Path("/opt/fixture/python3.12"),
    Path("/Users/fixture/run/private/pytest.ini"),
    Path("/Users/fixture/run/private/guard"),
)
WRITES = (Path("/Users/fixture/run/attempt"), Path("/Users/fixture/run/private/run"))
PINNED = {
    (): ("db6d79c085c4073a1fcc8173b5bf5de888a0d6cd8c1a15aa077dba50f0b6e08b", 1435),
    ("/Library/Developer/CommandLineTools",): (
        "9938948b6dbbb4ec7e891d3ed9ef0468577f3971f7ac61ba23dc6f078d568faf",
        1485,
    ),
    ("/Applications/Xcode.app",): (
        "6a1daab4ca02eaa34986ca53d92cd32f1dc9edf14c8c7a91c25378fac5190f2f",
        1533,
    ),
}
PINNED_C1 = {
    (): ("c26b26dcdbfe665c6c626972e6f4bc6cda94fbb3d177064c8403eeaca2c0e50a", 1499),
    ("/Library/Developer/CommandLineTools",): (
        "0452cca6c512d7d8cd1e443b2d1beb7c86c026cf63169ec9d820e22b47cd2503",
        1549,
    ),
    ("/Applications/Xcode.app",): (
        "c0d7759d7ce157148963734b80ef0f2aeec38ef0b662d62bb7e75daeaa2d9bdb",
        1597,
    ),
}
PINNED_368 = {  # a deny for reading *.pyc and creating a .pyc symlink under each write root
    (): ("93fd16f87d345ef3c80db6631e4856cf983926e610de2c0bb4e6f155efa7005a", 1863),
    ("/Library/Developer/CommandLineTools",): (
        "b8c12ab2e5a7dc3e4e35d73ea76ef2fded01e94b902041abad6befe238bb9350",
        1913,
    ),
    ("/Applications/Xcode.app",): (
        "d93451a8c0c3b6f7167fe8485864d364cf08844930d0de60a22e28b3bf5d055f",
        1961,
    ),
}


@pytest.mark.parametrize("dirs", list(PINNED))
def test_seatbelt_profile_is_pinned_as_of_the_posix_semaphore_change_116(monkeypatch, dirs):
    """Profile is pinned as of the POSIX-semaphore change (#116)."""
    monkeypatch.setattr(seatbelt, "developer_dirs", lambda: dirs)
    for profile in (
        seatbelt.build_profile(READS, WRITES, home=HOME),
        seatbelt.BACKEND.policy(READS, WRITES, home=HOME),
    ):
        pycs = [line for line in profile.text.splitlines(True) if '\\.pyc$")' in line]
        assert len(pycs) == 2 * len(WRITES)  # #368: a read and a symlink rule per write root
        before_368 = "".join(line for line in profile.text.splitlines(True) if line not in pycs)
        c1_lines = ('  (literal "/usr/bin/shasum")\n', '  (literal "/usr/bin/shasum5.34")\n')
        for line in c1_lines:
            assert before_368.count(line) == 1
        before_c1 = before_368
        for line in c1_lines:
            before_c1 = before_c1.replace(line, "")
        assert (hashlib.sha256(before_c1.encode()).hexdigest(), len(before_c1)) == PINNED[dirs]
        assert (hashlib.sha256(before_368.encode()).hexdigest(), len(before_368)) == PINNED_C1[dirs]
        assert (profile.sha256, len(profile.text)) == PINNED_368[dirs]


def test_seatbelt_argv_prefixes_are_unchanged(tmp_path):
    policy = tmp_path / "profile.sb"
    assert seatbelt.BACKEND.wrap(["/bin/true"], policy) == [
        "/usr/bin/sandbox-exec", "-f", str(policy), "/bin/true",
    ]  # fmt: skip
    assert seatbelt.BACKEND.unconfined(["/bin/true"]) == [
        "/usr/bin/sandbox-exec", "-p", "(version 1) (allow default)", "/bin/true",
    ]  # fmt: skip


def test_verifier_account_argv_is_unchanged(tmp_path, monkeypatch):
    import cairnwork.verifier_account as module

    monkeypatch.setattr(module, "platform_backend", lambda: seatbelt.BACKEND)
    account = VerifierAccount(user="u")
    head = ["/usr/bin/sudo", "-n", "-u", "u", "--"]
    env = ["/usr/bin/env", "-i", "A=1"]
    assert account.wrap(["x"], tmp_path / "p.sb", {"A": "1"}) == [
        *head, "/usr/bin/sandbox-exec", "-f", str(tmp_path / "p.sb"), *env, "x",
    ]  # fmt: skip
    assert account.wrap(["x"], None, {"A": "1"}) == [
        *head, "/usr/bin/sandbox-exec", "-p", "(version 1) (allow default)", *env, "x",
    ]  # fmt: skip


def test_no_backend_fails_closed(monkeypatch):
    monkeypatch.setattr(cairnwork_sandbox.sys, "platform", "win32")
    assert cairnwork_sandbox.platform_backend() is None
    assert cairnwork_sandbox.select() is None
    import cairnwork.verifier_account as module

    monkeypatch.setattr(module, "platform_backend", lambda: None)
    with pytest.raises(VerifierAccountError):
        VerifierAccount(user="u").wrap(["x"], None)


def test_select_refuses_a_backend_whose_probe_fails(monkeypatch):
    monkeypatch.setattr(cairnwork_sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(seatbelt, "available", lambda: False)
    assert cairnwork_sandbox.platform_backend() is seatbelt.BACKEND
    assert cairnwork_sandbox.select() is None


def test_sandbox_package_has_its_own_size_cap(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("check_size", root / "scripts/check_size.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = tmp_path / "src" / "cairnwork_sandbox"
    package.mkdir(parents=True)
    for i in range(4):
        (package / f"m{i}.py").write_text("x = 1\n" * 301, encoding="utf-8")
    monkeypatch.setattr(module, "SANDBOX", package)
    monkeypatch.setattr(module, "SCANNED", [tmp_path / "src"])
    assert any("cairnwork_sandbox total 1204" in p for p in module.check(tmp_path))
