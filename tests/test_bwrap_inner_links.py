"""bwrap masks for the outside target of a symlink found inside a credential directory (#377).
The directory's tmpfs hides the link but not its target, which stayed readable at its own path.
The policy tests run on any host; the sandbox-marked test witnesses it on real bwrap (the
test-linux-sandbox CI job)."""

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import commit_change

import cairnwork_sandbox
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError


def _policy(home: Path, reads=(), writes=(), owner=True) -> list[str]:
    return bwrap.BACKEND.policy(reads, writes, home=home, mask_home_credentials=owner).text.split(
        "\n"
    )


def _masked(lines: list[str]) -> list[str]:
    tmpfs = [lines[i + 1] for i in range(len(lines) - 1) if lines[i] == "--tmpfs"]
    null = [lines[i + 2] for i in range(len(lines) - 2) if lines[i : i + 2] == bwrap_inner._NULL]
    return tmpfs + null


def _under(lines: list[str], base: Path) -> list[str]:
    """The masks below ``base``: a Linux host adds its own (/dev/shm, /run/user, sockets), so a
    whole-list comparison only holds on macOS."""
    return [p for p in _masked(lines) if p.startswith(_real(base) + os.sep)]


def _pair(lines: list[str], target: str) -> list[str]:
    i = lines.index(target)
    return lines[i - 1 : i + 1] if lines[i - 1] == "--tmpfs" else lines[i - 2 : i + 1]


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def out(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


def _real(path: Path) -> str:
    return str(path.resolve())


def test_outside_targets_of_inner_links_are_masked_by_type(home, out):
    (out / "cred").write_text("secret\n")
    (out / "gcloud").mkdir()
    (out / "deep").mkdir()
    (out / "deep" / "tok").write_text("secret\n")
    (out / "rel.txt").write_text("secret\n")
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").symlink_to(out / "cred")  # a file
    (home / ".config").mkdir()
    (home / ".config" / "gcloud").symlink_to(out / "gcloud")  # a directory
    (home / ".ssh" / "keys").mkdir(parents=True)
    (home / ".ssh" / "keys" / "id").symlink_to(out / "deep" / "tok")  # deep in the dir
    (home / ".aws" / "rel").symlink_to(os.path.relpath(out / "rel.txt", home / ".aws"))
    lines = _policy(home)
    for name in ("cred", "deep/tok", "rel.txt"):
        assert _pair(lines, _real(out / name)) == ["--ro-bind", "/dev/null", _real(out / name)]
    assert _pair(lines, _real(out / "gcloud")) == ["--tmpfs", _real(out / "gcloud")]


def test_an_in_home_target_outside_the_credential_dir_is_masked(home):
    (home / "notes").mkdir()
    (home / "notes" / "kubecfg").write_text("secret\n")
    (home / ".kube").mkdir()
    (home / ".kube" / "config").symlink_to(home / "notes" / "kubecfg")
    assert _real(home / "notes" / "kubecfg") in _masked(_policy(home))


def test_links_inside_the_dir_or_already_masked_add_no_mask(home, out):
    (home / ".aws" / "real").mkdir(parents=True)
    (home / ".aws" / "inside").symlink_to(home / ".aws" / "real")
    (home / ".config").mkdir()
    (home / ".config" / "SingletonLock").symlink_to("host-123")  # dangling, inside: Chrome
    (home / ".config" / "id").symlink_to(home / ".aws" / "real")  # under another mask
    (home / ".netrc").symlink_to(out / "netrc")
    (out / "netrc").write_text("secret\n")
    (home / ".config" / "netrc").symlink_to(home / ".netrc")  # already masked at its target
    before = _under(_policy(home), home.parent)
    assert sorted(before) == sorted(
        [f"{_real(home)}/.aws", f"{_real(home)}/.config", _real(out / "netrc")]
    )


def test_two_links_to_one_target_mask_it_once(home, out):
    (out / "cred").write_text("secret\n")
    (home / ".aws").mkdir()
    (home / ".gnupg").mkdir()
    (home / ".aws" / "a").symlink_to(out / "cred")
    (home / ".gnupg" / "b").symlink_to(out / "cred")
    assert _masked(_policy(home)).count(_real(out / "cred")) == 1


def test_a_linked_credential_dir_is_scanned_at_its_real_path(home, out):
    (out / "aws").mkdir()
    (out / "cred").write_text("secret\n")
    (out / "aws" / "credentials").symlink_to(out / "cred")
    (home / ".aws").symlink_to(out / "aws")
    assert _real(out / "cred") in _masked(_policy(home))


@pytest.mark.parametrize("target", ["/usr/share", "/usr/bin/env"])
def test_system_package_targets_are_left_alone(home, target):
    # D1: package-managed public content (~/.config/tz -> /usr/share/zoneinfo, systemd user units).
    if not os.path.exists(target):
        pytest.skip(f"{target} is absent on this host")
    (home / ".config" / "systemd").mkdir(parents=True)
    (home / ".config" / "systemd" / "x").symlink_to(target)
    lines = _policy(home)
    assert _under(lines, home.parent) == [f"{_real(home)}/.config"]
    assert _real(Path(target)) not in _masked(lines)  # the target itself is left unmasked


def test_an_etc_directory_target_is_refused_and_an_etc_file_masked(home, tmp_path, monkeypatch):
    # D1: /etc is not exempt. Pointed at a stand-in /etc (macOS resolves /etc to /private/etc).
    etc = tmp_path / "etc"
    (etc / "ssl").mkdir(parents=True)
    (etc / "token").write_text("secret\n")
    monkeypatch.setattr(bwrap_inner, "ETC", _real(etc) + "/")
    (home / ".config").mkdir()
    (home / ".config" / "tok").symlink_to(etc / "token")
    assert _real(etc / "token") in _masked(_policy(home))
    (home / ".config" / "certs").symlink_to(etc / "ssl")
    with pytest.raises(SandboxError, match=r"certs.*/etc directory"):
        _policy(home)


@pytest.mark.parametrize("where", ["home", "home-parent", "/usr", "write", "read"])
def test_an_inner_link_whose_target_cannot_be_masked_is_refused(tmp_path, where):
    home, work = tmp_path / "h" / "home", tmp_path / "w" / "run"
    (home / ".aws").mkdir(parents=True)
    (work / "venv").mkdir(parents=True)
    target = {
        "home": home,
        "home-parent": home.parent,
        "/usr": Path("/usr"),
        "write": work,
        "read": work,
    }[where]
    (home / ".aws" / "x").symlink_to(target)
    writes, reads = ((work,), ()) if where == "write" else ((), (work / "venv",))
    with pytest.raises(SandboxError, match=r"\.aws/x"):
        _policy(home, reads=reads, writes=writes)


@pytest.mark.parametrize("root", ["read", "write"])
@pytest.mark.parametrize("depth", ["missing-leaf", "missing-parent"])
def test_a_dangling_target_under_a_run_root_is_refused(tmp_path, home, root, depth):
    # D3: a target that may appear where the run reads or writes is refused, as at the top level.
    run = tmp_path / "run"
    run.mkdir()
    gone = run / "gone" if depth == "missing-leaf" else run / "no" / "such" / "file"
    (home / ".aws").mkdir()
    (home / ".aws" / "x").symlink_to(gone)
    reads, writes = ((run,), ()) if root == "read" else ((), (run,))
    with pytest.raises(SandboxError, match=r"\.aws/x.*does not exist"):
        _policy(home, reads=reads, writes=writes)


def test_a_dangling_target_outside_every_root_is_skipped_with_a_note(tmp_path, home):
    # D3 (narrowed): e.g. Codex's control socket link into /tmp while its daemon is down. A
    # residual of the #353 kind: the run sees the host root, so a target created mid-run is
    # readable. Recorded in the policy notes (a fixed string and a count, no untrusted path).
    run = tmp_path / "run"
    run.mkdir()
    (home / ".codex" / "ctl").mkdir(parents=True)
    (home / ".codex" / "ctl" / "a.sock").symlink_to(tmp_path / "daemon" / "a.sock")
    (home / ".codex" / "ctl" / "b.sock").symlink_to(tmp_path / "gone.sock")
    policy = bwrap.BACKEND.policy((run,), (), home=home)
    assert _under(policy.text.split("\n"), tmp_path) == [f"{_real(home)}/.codex"]
    assert f"{bwrap_inner.DANGLING_NOTE}2" in policy.notes


def test_an_unreadable_target_is_refused_not_called_dangling(tmp_path, home):
    # A permission error on the target is not "it does not exist": skipping it would leave a
    # target the owner cannot stat unmasked. Only ENOENT and ENOTDIR count as dangling.
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "tok").write_text("secret\n")
    (home / ".aws").mkdir()
    (home / ".aws" / "x").symlink_to(locked / "tok")
    locked.chmod(0)
    try:
        if os.access(locked / "tok", os.F_OK):
            pytest.skip("running as a user that stats through a mode-0 directory (root)")
        with pytest.raises(SandboxError, match=r"\.aws/x.*cannot be checked"):
            _policy(home, reads=(tmp_path / "run",))
    finally:
        locked.chmod(0o700)


def test_a_scan_past_the_limit_is_refused(home, monkeypatch):
    # D2: fail closed rather than leave a link unscanned.
    monkeypatch.setattr(bwrap_inner, "SCAN_LIMIT", 5)
    (home / ".config").mkdir()
    for i in range(6):
        (home / ".config" / f"f{i}").write_text("")
    with pytest.raises(SandboxError, match="more than 5 entries"):
        _policy(home)


def test_links_are_scanned_to_depth_three_and_deeper_levels_noted(home, out):
    # D2 depth: .config/a/b/c is scanned (c at depth 3); entries below it are not, and the
    # policy notes count the unscanned directories (a fixed string, no path).
    (out / "d3").write_text("secret\n")
    (out / "d4").write_text("secret\n")
    (home / ".config" / "a" / "b" / "c").mkdir(parents=True)
    (home / ".config" / "a" / "b" / "link").symlink_to(out / "d3")  # depth 3
    (home / ".config" / "a" / "b" / "c" / "link").symlink_to(out / "d4")  # depth 4
    policy = bwrap.BACKEND.policy((), (), home=home)
    masked = _masked(policy.text.split("\n"))
    assert _real(out / "d3") in masked and _real(out / "d4") not in masked
    assert f"{bwrap_inner.DEPTH_NOTE}1" in policy.notes


def test_a_large_tree_below_the_scan_depth_is_not_refused(home, monkeypatch):
    # The limit counts only what is scanned: bulk state below depth 3 (a .local/state with
    # millions of entries) does not refuse the run.
    monkeypatch.setattr(bwrap_inner, "SCAN_LIMIT", 5)
    deep = home / ".local" / "state" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    for i in range(10):
        (deep / f"f{i}").write_text("")
    assert f"{bwrap_inner.DEPTH_NOTE}1" in bwrap.BACKEND.policy((), (), home=home).notes


def test_an_unreadable_subdir_is_refused(home):
    locked = home / ".config" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as a user that reads a mode-0 directory (root)")
        with pytest.raises(SandboxError, match="cannot scan"):
            _policy(home)
    finally:
        locked.chmod(0o700)


@pytest.fixture
def world():
    """0755 dirs under /tmp (world-traversable ancestors), so the account path can reach them."""
    base = Path(tempfile.mkdtemp(dir="/tmp", prefix="cw-inner-"))  # noqa: S108
    os.chmod(base, 0o755)
    (base / "home").mkdir(mode=0o755)
    (base / "out").mkdir(mode=0o755)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize("home_mode", [0o755, 0o700], ids=["home-0755", "home-0700"])
def test_account_mode_masks_a_reachable_inner_target(world, home_mode):
    home, out = world / "home", world / "out"
    (out / "cred").write_text("secret\n")
    (out / "priv").mkdir(mode=0o700)
    (out / "priv" / "dock").write_text("secret\n")
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").symlink_to(out / "cred")
    (home / ".aws" / "dock").symlink_to(out / "priv" / "dock")  # unreachable: not masked
    (home / ".aws" / "gone").symlink_to(out / "priv" / "gone")  # unreachable dangling: ignored
    home.chmod(home_mode)
    masked = _masked(_policy(home, owner=False))
    assert _real(out / "cred") in masked
    assert not [p for p in masked if p.startswith(_real(out / "priv"))]


def test_account_mode_refuses_a_reachable_dangling_inner_target_under_a_root(world):
    (world / "home" / ".aws").mkdir()
    (world / "home" / ".aws" / "x").symlink_to(world / "out" / "gone")
    world.joinpath("home").chmod(0o700)
    with pytest.raises(SandboxError, match="does not exist"):
        _policy(world / "home", reads=(world / "out",), owner=False)
    assert _policy(world / "home", owner=False)  # outside every root: skipped


def _serve(path: Path) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    return server


@pytest.mark.sandbox
def test_inner_link_targets_stay_masked_on_a_real_run(repo, tmp_path, monkeypatch):
    """On base the run passes but reads the canaries and reaches the socket, so the candidate's
    test fails. Fixed, nothing behind an inner link is readable or reachable, and a /usr target
    is still there."""
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name != "bwrap":
        pytest.skip("bwrap only: Seatbelt deny rules are a separate mechanism (not #377)")
    home, out = tmp_path / "fakehome", tmp_path / "o"
    (out / "deep").mkdir(parents=True)
    (out / "cred").write_text("CANARY-SECRET\n")
    (out / "deep" / "tok").write_text("CANARY-SECRET\n")
    (home / ".aws").mkdir(parents=True)
    (home / ".ssh" / "keys").mkdir(parents=True)
    (home / ".config").mkdir()
    (home / ".aws" / "credentials").symlink_to(out / "cred")
    (home / ".ssh" / "keys" / "id").symlink_to(out / "deep")
    (home / ".ssh" / "agent.sock").symlink_to(out / "agent.sock")
    (home / ".config" / "env").symlink_to("/usr/bin/env")  # D1 control: left alone
    server = _serve(out / "agent.sock")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CW_OUT", str(out))
    import cairnwork.verifiers as verifiers

    monkeypatch.setattr(verifiers, "_ENV_KEEP", (*verifiers._ENV_KEEP, "CW_OUT"))
    code = (
        "import os, socket\n"
        "def test_masked():\n"
        "    out = os.environ['CW_OUT']\n"
        "    assert os.path.exists('/usr/bin/env')\n"
        "    for path in (out + '/cred', out + '/deep/tok'):\n"
        "        try:\n"
        "            data = open(path).read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        assert 'CANARY-SECRET' not in data, path\n"
        "    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    try:\n"
        "        client.connect(out + '/agent.sock')\n"
        "    except OSError:\n"
        "        return\n"
        "    raise AssertionError('agent socket reachable')\n"
    )
    try:
        head = commit_change(repo, {"tests/test_masked.py": code})
        spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_masked.py"])
        runs = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    finally:
        server.close()
    assert runs[0].observation.passed, runs[0].log_path.read_text()
