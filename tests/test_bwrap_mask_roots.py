"""bwrap credential masks that would hide a read root, or whose mount type does not match the
real target (#378, #358 review V4 and V5). Both used to pass policy building and then fail the run
at mount or with an unrelated-looking error; now the type follows the target, and a mask over a
read root is refused with the root named. The policy tests run on any host; the last test
witnesses the type fix on real bwrap (the test-linux-sandbox CI job)."""

import os
import sys
from pathlib import Path

import pytest
from conftest import commit_change
from fixture_tree import reach_env, world_base, world_dir

import cairnwork_sandbox
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from cairnwork_sandbox import bwrap
from cairnwork_sandbox.backend import SandboxError


def _policy(home: Path, reads=(), writes=(), owner=True) -> list[str]:
    return bwrap.BACKEND.policy(reads, writes, home=home, mask_home_credentials=owner).text.split(
        "\n"
    )


def _pair(lines: list[str], target: str) -> list[str]:
    i = lines.index(target)
    return lines[i - 1 : i + 1] if lines[i - 1] == "--tmpfs" else lines[i - 2 : i + 1]


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_a_plain_credential_path_is_masked_by_its_real_type(home):
    # V5: a .netrc that is a directory got --ro-bind /dev/null (bwrap: "Destination is not a
    # file"), an .ssh that is a file got --tmpfs (a tmpfs cannot mount on a file).
    (home / ".netrc").mkdir()
    (home / ".ssh").write_text("key\n")
    os.mkfifo(home / ".pgpass")  # anything that is not a directory takes /dev/null
    lines, real = _policy(home), str(home.resolve())
    assert _pair(lines, f"{real}/.netrc") == ["--tmpfs", f"{real}/.netrc"]
    assert _pair(lines, f"{real}/.ssh") == ["--ro-bind", "/dev/null", f"{real}/.ssh"]
    assert _pair(lines, f"{real}/.pgpass") == ["--ro-bind", "/dev/null", f"{real}/.pgpass"]


def test_a_linked_credential_path_is_masked_by_its_target_type(tmp_path, home):
    outside = tmp_path / "outside"
    (outside / "pypirc").mkdir(parents=True)
    (outside / "aws").write_text("[default]\n")
    (home / ".pypirc").symlink_to(outside / "pypirc")  # a file name linked to a directory
    (home / ".aws").symlink_to(outside / "aws")  # a directory name linked to a file
    lines = _policy(home)
    pypirc, aws = str((outside / "pypirc").resolve()), str((outside / "aws").resolve())
    assert _pair(lines, pypirc) == ["--tmpfs", pypirc]
    assert _pair(lines, aws) == ["--ro-bind", "/dev/null", aws]


@pytest.mark.parametrize("where", ["under", "exact", "file"])
def test_a_link_whose_target_would_hide_a_read_root_is_refused(tmp_path, home, where):
    # V4: e.g. a venv or interpreter under the linked .local/state target.
    state = tmp_path / "x" / "state"
    (state / "venv").mkdir(parents=True)
    (state / "ini").write_text("[pytest]\n")
    (home / ".local").mkdir()
    if where == "file":
        (home / ".netrc").symlink_to(state / "ini")  # a /dev/null bind would blank the ini
    else:
        (home / ".local" / "state").symlink_to(state)
    read = {"under": state / "venv", "exact": state, "file": state / "ini"}[where]
    with pytest.raises(SandboxError, match=r"links to.*read root") as err:
        _policy(home, reads=(read,))
    assert str(read.resolve()) in str(err.value)


def test_a_plain_credential_dir_over_a_read_root_is_refused(home):
    venv = home / ".config" / "venv"
    venv.mkdir(parents=True)
    with pytest.raises(SandboxError, match=r"\.config.*read root") as err:
        _policy(home, reads=(venv,))
    assert str(venv.resolve()) in str(err.value)


def test_read_roots_beside_or_above_a_mask_are_kept(tmp_path, home):
    # Negative controls: a read root above the target (the target is a part of it, as the whole
    # root is) or one that only shares a name prefix with it does not stop the mask.
    state = tmp_path / "x" / "state"
    state.mkdir(parents=True)
    sibling = tmp_path / "x" / "state2"
    sibling.mkdir()
    (home / ".local").mkdir()
    (home / ".local" / "state").symlink_to(state)
    lines = _policy(home, reads=(tmp_path, sibling))
    assert _pair(lines, str(state.resolve())) == ["--tmpfs", str(state.resolve())]


def test_a_write_root_under_a_plain_credential_dir_is_left_to_the_deployment_check(home, tmp_path):
    # Unchanged by #378: a write root under a plain masked dir is the documented deployment error
    # (docs/SECURITY.md) that cairnwork-verify refuses as STATE_UNDER_SANDBOX_MASK.
    work = home / ".config" / "run"
    work.mkdir(parents=True)
    lines = _policy(home, writes=(work,))
    assert _pair(lines, f"{home.resolve()}/.config") == ["--tmpfs", f"{home.resolve()}/.config"]


@pytest.fixture
def world():
    """0755 dirs off /tmp (world-traversable ancestors), so the account path can reach them."""
    with world_dir("cw-roots-") as base:
        (base / "home").mkdir(mode=0o755)
        (base / "out").mkdir(mode=0o755)
        yield base


def test_account_mode_refuses_a_reachable_target_over_a_read_root(world):
    (world / "out" / "aws" / "venv").mkdir(parents=True)
    (world / "home" / ".aws").symlink_to(world / "out" / "aws")
    read = world / "out" / "aws" / "venv"
    with pytest.raises(SandboxError, match="read root"):
        _policy(world / "home", reads=(read,), owner=False)


def test_account_mode_masks_a_reachable_target_by_its_type(world):
    (world / "out" / "netrc").mkdir()
    (world / "home" / ".netrc").symlink_to(world / "out" / "netrc")
    lines = _policy(world / "home", owner=False)
    real = str((world / "out" / "netrc").resolve())
    assert _pair(lines, real) == ["--tmpfs", real]


@pytest.mark.sandbox
def test_mistyped_credential_paths_run_and_stay_masked(repo, tmp_path, monkeypatch, request):
    """On base this run fails at mount ("Destination is not a file" / tmpfs on a file): the
    verifier does not pass. Fixed, the run passes and no canary is readable, while a plain file
    beside them is (the fixture tree is off /tmp, so the run sees it, #391)."""
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name != "bwrap":
        pytest.skip("bwrap only: Seatbelt deny rules are a separate mechanism (not #378)")
    base = world_base(request, "cw-roots-run-")
    home, outside = base / "fakehome", base / "outside"
    (home / ".netrc").mkdir(parents=True)  # a credential file name that is a directory
    (home / ".netrc" / "token").write_text("CANARY-SECRET\n")
    (home / ".ssh").write_text("CANARY-SECRET\n")  # a credential dir name that is a file
    (outside / "pypirc").mkdir(parents=True)
    (outside / "pypirc" / "token").write_text("CANARY-SECRET\n")
    (home / ".pypirc").symlink_to(outside / "pypirc")  # a file name linked to a directory
    canaries = [home / ".netrc/token", home / ".ssh", outside / "pypirc/token"]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CW_CANARIES", ":".join(str(c) for c in canaries))
    reach, check = reach_env(monkeypatch, backend, home, outside)
    import cairnwork.verifiers as verifiers

    monkeypatch.setattr(verifiers, "_ENV_KEEP", (*verifiers._ENV_KEEP, "CW_CANARIES", *reach))
    code = (
        "import os\n"
        "def test_masked():\n"
        f"{check}"
        "    paths = os.environ['CW_CANARIES'].split(':')\n"
        "    assert len(paths) == 3\n"
        "    for path in paths:\n"
        "        try:\n"
        "            data = open(path).read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        assert 'CANARY-SECRET' not in data, path\n"
    )
    head = commit_change(repo, {"tests/test_masked.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_masked.py"])
    runs = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    assert runs[0].observation.passed, runs[0].log_path.read_text()
