"""bwrap credential masks on symlinked paths (#341). bwrap resolves an absolute symlink against
its temporary root, where the target is missing, so a mask on the literal link failed every run
at mount. A path reached through any link is now masked at its real target. The policy tests run
on any host; the last test witnesses it on real bwrap (the test-linux-sandbox CI job)."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import commit_change

import cairnwork_sandbox
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork_sandbox import bwrap
from cairnwork_sandbox.backend import SandboxError


def _policy(home: Path, writes=(), owner=True) -> list[str]:
    return bwrap.BACKEND.policy((), writes, home=home, mask_home_credentials=owner).text.split("\n")


def _masked(lines: list[str]) -> list[str]:
    """Every path a credential-style mask mounts on (tmpfs dirs and /dev/null file binds)."""
    tmpfs = [lines[i + 1] for i in range(len(lines) - 1) if lines[i] == "--tmpfs"]
    null = [
        lines[i + 2]
        for i in range(len(lines) - 2)
        if lines[i : i + 2] == ["--ro-bind", "/dev/null"]
    ]
    return tmpfs + null


def _pair(lines: list[str], target: str) -> list[str]:
    i = lines.index(target)
    return lines[i - 1 : i + 1] if lines[i - 1] == "--tmpfs" else lines[i - 2 : i + 1]


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_absolute_link_out_of_home_is_masked_at_its_target(tmp_path, home):
    outside = tmp_path / "store" / "config"
    outside.mkdir(parents=True)
    (home / ".config").symlink_to(outside)  # absolute, like home-manager's /nix/store link
    (home / ".ssh").mkdir()  # a plain dir next to it keeps its literal mask
    lines = _policy(home)
    real_home = str(home.resolve())
    assert _pair(lines, str(outside.resolve())) == ["--tmpfs", str(outside.resolve())]
    assert f"{real_home}/.config" not in lines  # the literal link would fail the mount
    assert _pair(lines, f"{real_home}/.ssh") == ["--tmpfs", f"{real_home}/.ssh"]


def test_absolute_link_inside_home_is_masked_at_its_target(home):
    dotfiles = home / "dotfiles" / "ssh"
    dotfiles.mkdir(parents=True)
    (home / ".ssh").symlink_to(dotfiles.resolve())
    lines = _policy(home)
    assert _pair(lines, str(dotfiles.resolve())) == ["--tmpfs", str(dotfiles.resolve())]
    assert f"{home.resolve()}/.ssh" not in lines


def test_relative_link_is_masked_at_its_target(home):
    (home / "dotfiles" / "aws").mkdir(parents=True)
    (home / ".aws").symlink_to("dotfiles/aws")
    lines = _policy(home)
    real = f"{home.resolve()}/dotfiles/aws"
    assert _pair(lines, real) == ["--tmpfs", real]
    assert f"{home.resolve()}/.aws" not in lines


def test_a_symlinked_parent_masks_the_child_at_its_real_path(tmp_path, home):
    local = tmp_path / "elsewhere" / "local"
    (local / "state").mkdir(parents=True)
    (local / "share" / "keyrings").mkdir(parents=True)
    (home / ".local").symlink_to(local)  # .local/state runs through an absolute link
    lines = _policy(home)
    for rel in ("state", "share/keyrings"):
        real = f"{local.resolve()}/{rel}"
        assert _pair(lines, real) == ["--tmpfs", real]
        assert f"{home.resolve()}/.local/{rel}" not in lines


def test_a_linked_credential_file_is_replaced_at_its_target(tmp_path, home):
    secret = tmp_path / "secrets" / "netrc"
    secret.parent.mkdir()
    secret.write_text("machine x password y\n")
    (home / ".netrc").symlink_to(secret)
    (home / ".pypirc").write_text("token\n")  # a plain file keeps its literal mask
    lines = _policy(home)
    real = str(secret.resolve())
    assert _pair(lines, real) == ["--ro-bind", "/dev/null", real]
    assert f"{home.resolve()}/.netrc" not in lines
    plain = f"{home.resolve()}/.pypirc"
    assert _pair(lines, plain) == ["--ro-bind", "/dev/null", plain]


@pytest.mark.parametrize("kind", ["absolute", "relative", "loop"])
def test_a_dangling_link_is_refused(tmp_path, home, kind):
    target = {"absolute": tmp_path / "gone", "relative": "missing", "loop": ".config"}[kind]
    (home / ".config").symlink_to(target)
    with pytest.raises(SandboxError, match=r"\.config.*does not exist.*dangling"):
        _policy(home)


def test_a_dangling_file_link_is_refused(home):
    (home / ".netrc").symlink_to("missing-netrc")
    with pytest.raises(SandboxError, match=r"\.netrc.*does not exist"):
        _policy(home)


@pytest.mark.parametrize(
    "where", ["/", "/usr", "home", "home-parent", "write-root", "write-parent"]
)
def test_a_link_whose_target_would_hide_the_run_is_refused(tmp_path, where):
    home, work = tmp_path / "h" / "home", tmp_path / "w" / "run"
    home.mkdir(parents=True)
    work.mkdir(parents=True)
    target = {
        "home": home,
        "home-parent": home.parent,  # covers the home only, not the write root
        "write-root": work,
        "write-parent": work.parent,  # covers the write root only, not the home
    }.get(where, Path(where))
    (home / ".config").symlink_to(target)
    with pytest.raises(SandboxError, match=r"\.config.*links to") as err:
        _policy(home, (work,))
    hidden = str(err.value).split("without hiding ", 1)[1]
    real_home, real_work = str(home.resolve()), str(work.resolve())
    if where in ("home", "home-parent"):
        assert real_home in hidden and real_work not in hidden
    elif where in ("write-root", "write-parent"):
        assert real_work in hidden and real_home not in hidden


@pytest.mark.parametrize("sibling_of", ["write-root", "home"])
def test_a_target_whose_name_only_prefixes_a_root_is_masked(tmp_path, sibling_of):
    """``/x/run`` does not cover ``/x/run2``: coverage is by path component, not string prefix."""
    home, work = tmp_path / "home2", tmp_path / "run2"
    home.mkdir()
    work.mkdir()
    target = tmp_path / ("run" if sibling_of == "write-root" else "home")
    target.mkdir()
    (home / ".config").symlink_to(target)
    lines = _policy(home, (work,))
    assert _pair(lines, str(target.resolve())) == ["--tmpfs", str(target.resolve())]


def test_a_link_target_cannot_add_a_policy_line(tmp_path, home):
    evil = tmp_path / "x\n--bind"
    evil.mkdir()
    (home / ".config").symlink_to(evil)
    with pytest.raises(SandboxError, match="not allowed"):
        _policy(home)


def _world_dir():
    """A 0755 dir under /tmp (world-traversable ancestors), so the account path can reach it."""
    base = Path(tempfile.mkdtemp(dir="/tmp", prefix="cw-link-"))  # noqa: S108
    os.chmod(base, 0o755)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


world_home = pytest.fixture(_world_dir, name="world_home")
world_outside = pytest.fixture(_world_dir, name="world_outside")


def test_account_mode_masks_an_in_home_absolute_link_at_its_target(world_home):
    (world_home / "dotfiles").mkdir()
    (world_home / "dotfiles" / "ssh").mkdir()
    (world_home / ".ssh").symlink_to(world_home.resolve() / "dotfiles" / "ssh")
    lines = _policy(world_home, owner=False)
    real = f"{world_home.resolve()}/dotfiles/ssh"
    assert _pair(lines, real) == ["--tmpfs", real]
    assert f"{world_home.resolve()}/.ssh" not in lines


def test_account_mode_refuses_a_dangling_link_in_an_enterable_home(world_home):
    (world_home / ".ssh").symlink_to(world_home.resolve() / "gone")
    with pytest.raises(SandboxError, match="dangling"):
        _policy(world_home, owner=False)


def test_account_mode_ignores_a_dangling_link_in_a_private_home(world_home):
    (world_home / ".ssh").symlink_to(world_home.resolve() / "gone")
    world_home.chmod(0o700)  # the account cannot enter: nothing there can leak or be mounted
    lines = _policy(world_home, owner=False)
    assert not [p for p in _masked(lines) if p.startswith(str(world_home.resolve()))]


@pytest.mark.parametrize("home_mode", [0o755, 0o700], ids=["home-0755", "home-0700"])
def test_account_mode_masks_a_reachable_target_outside_home(world_home, world_outside, home_mode):
    # #358: the account reads an outside target through its own path (0755 under /tmp), whether
    # or not it can enter the home that holds the link, so the target is masked there.
    (world_outside / "ssh").mkdir()
    (world_outside / "netrc").write_text("machine x password y\n")
    (world_home / ".ssh").symlink_to(world_outside / "ssh")
    (world_home / ".netrc").symlink_to(world_outside / "netrc")
    world_home.chmod(home_mode)
    lines = _policy(world_home, owner=False)
    ssh, netrc = str((world_outside / "ssh").resolve()), str((world_outside / "netrc").resolve())
    assert _pair(lines, ssh) == ["--tmpfs", ssh]
    assert _pair(lines, netrc) == ["--ro-bind", "/dev/null", netrc]
    assert f"{world_home.resolve()}/.ssh" not in lines


def test_account_mode_leaves_an_unreachable_outside_target_unmasked(world_home, world_outside):
    # Negative control: behind a directory the account cannot enter, the target cannot leak and
    # a mount over it would EPERM, so it is not masked and the policy is not refused.
    (world_outside / "priv" / "ssh").mkdir(parents=True)
    (world_outside / "priv").chmod(0o700)
    (world_home / ".ssh").symlink_to(world_outside / "priv" / "ssh")
    (world_home / ".netrc").write_text("x\n")  # an in-home control is still masked
    lines = _policy(world_home, owner=False)
    assert not [p for p in _masked(lines) if p.startswith(str(world_outside.resolve()))]
    assert f"{world_home.resolve()}/.netrc" in _masked(lines)


@pytest.mark.parametrize("where", ["/usr", "write-root", "dangling"])
def test_account_mode_refuses_an_outside_target_it_cannot_mask(world_home, world_outside, where):
    # Selected like an in-home target, an outside one the mask cannot cover (a system root, a
    # write root) or that does not exist yet is refused as on the owner path, not left readable.
    work = world_outside / "run"
    work.mkdir()
    target = {"/usr": Path("/usr"), "write-root": work, "dangling": world_outside / "gone"}[where]
    (world_home / ".config").symlink_to(target)
    with pytest.raises(SandboxError, match=r"\.config"):
        _policy(world_home, (work,), owner=False)


def test_account_mode_ignores_a_dangling_outside_link_it_cannot_reach(world_home, world_outside):
    (world_outside / "priv").mkdir()
    (world_outside / "priv").chmod(0o700)
    (world_home / ".ssh").symlink_to(world_outside / "priv" / "gone")
    lines = _policy(world_home, owner=False)
    assert not [p for p in _masked(lines) if p.startswith(str(world_outside.resolve()))]


def test_account_mode_refuses_a_link_to_an_in_home_ancestor_of_a_write_root(world_home):
    work = world_home / "w" / "run"
    work.mkdir(parents=True)
    (world_home / ".config").symlink_to(world_home.resolve() / "w")
    with pytest.raises(SandboxError, match=r"\.config.*links to") as err:
        _policy(world_home, (work,), owner=False)
    assert str(work.resolve()) in str(err.value)


@pytest.mark.sandbox
def test_absolute_symlinked_credentials_run_and_stay_masked(repo, tmp_path, monkeypatch):
    """On base this run fails at mount ('Can't mount tmpfs on /newroot/...'): the verifier does
    not pass. Fixed, the run passes and no canary behind a link is readable."""
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name != "bwrap":
        pytest.skip("bwrap only: Seatbelt deny rules are a separate mechanism (not #341)")
    home, outside = tmp_path / "fakehome", tmp_path / "outside"
    (outside / "config" / "gh").mkdir(parents=True)
    (home / "dotfiles" / "ssh").mkdir(parents=True)
    (outside / "config" / "gh" / "hosts.yml").write_text("CANARY-SECRET\n")
    (home / "dotfiles" / "ssh" / "id_test").write_text("CANARY-SECRET\n")
    (outside / "netrc").write_text("CANARY-SECRET\n")
    (home / ".config").symlink_to(outside / "config")  # absolute, out of home
    (home / ".ssh").symlink_to(home / "dotfiles" / "ssh")  # absolute, inside home
    (home / ".netrc").symlink_to(outside / "netrc")  # absolute file link
    canaries = [
        home / ".config/gh/hosts.yml",
        outside / "config/gh/hosts.yml",
        home / ".ssh/id_test",
        home / "dotfiles/ssh/id_test",
        home / ".netrc",
        outside / "netrc",
    ]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CW_CANARIES", ":".join(str(c) for c in canaries))
    import cairnwork.verifiers as verifiers

    monkeypatch.setattr(verifiers, "_ENV_KEEP", (*verifiers._ENV_KEEP, "CW_CANARIES"))
    code = (
        "import os\n"
        "def test_masked():\n"
        "    paths = os.environ['CW_CANARIES'].split(':')\n"
        "    assert len(paths) == 6\n"
        "    for path in paths:\n"
        "        try:\n"
        "            data = open(path).read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        assert 'CANARY-SECRET' not in data, path\n"
    )
    head = commit_change(repo, {"tests/test_masked.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_masked.py"])
    runs = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert runs[0].observation.passed, runs[0].log_path.read_text()
