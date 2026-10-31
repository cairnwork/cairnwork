"""Issue #276: candidate code points ``sys.pycache_prefix`` at another writable directory.

The verifier sets PYTHONPYCACHEPREFIX to its own prefix (test_pycache_prefix.py), but candidate
library code imported by an honest test may reassign ``sys.pycache_prefix`` at runtime to any
directory the run may write (its HOME, its TMPDIR, the work directory next to the checkout, the
checkout's own .git) and plant bytecode there for a protected test or helper. Under bwrap pytest's
assertion rewriter and the import system read it from there, the deferred test that fails as
written passes, and the checkout comparison does not see the planted file; Seatbelt denies the
read (#368, test_seatbelt_pyc.py). The after-run scan (bytecode_roots.py)
must fail each of these runs with FORGED_BYTECODE, while honest runs that write bytecode into
tmp_path, leave symlinks or leave an unreadable directory there still pass.

All of these run the real sandbox through run_verifiers. The account-mode variants at the end need
the verifier account (pytest.mark.verifier_account).
"""

import os
import shutil
import sys

import pytest
from conftest import commit_change
from test_pycache_prefix import CONFTEST, FORGE, PLANTED_RC

from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox

# Where candidate code points sys.pycache_prefix (evaluated inside the run; evil.py is
# <checkout>/src/evil.py, so parents[1] is the checkout and parents[2] the work directory).
PREFIXES = {
    "home": "os.path.join(os.environ['HOME'], 'alt')",
    "tmpdir": "os.path.join(os.environ['TMPDIR'], 'alt')",
    "workdir": "str(Path(__file__).resolve().parents[2] / 'alt')",
    "git": "str(Path(__file__).resolve().parents[1] / '.git' / 'alt')",
}
DEFERRED = "def test_z():\n    assert False, 'the real deferred test'\n"


def redirecting(where, target, code, pytest_pyc):
    return (
        "import os, sys\nfrom pathlib import Path\nfrom forger import forge\n"
        f"sys.pycache_prefix = {PREFIXES[where]}\n"
        f"forge({target!r}, {code!r}, {pytest_pyc})\n"
    )


def run_one(repo, tmp_path, files, *targets, account=None):
    commit_change(repo, {"tests/conftest.py": CONFTEST, "src/forger.py": FORGE})
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", *targets])
    if account is None:
        root, python = tmp_path / "runs", sys.executable
    else:
        root, python = account.run_root, None
    run_id = "r" + os.urandom(6).hex()
    (run,) = run_verifiers(
        VerifyContext(repo, root, python=python, account=account), head, (spec,), run_id
    )
    return run


def forged_test_run(repo, tmp_path, where, account=None):
    files = {
        "src/evil.py": redirecting(where, "tests/test_z.py", "def test_z():\n    pass\n", True),
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": DEFERRED,
    }
    return run_one(repo, tmp_path, files, "tests/test_a.py", "tests/test_z.py", account=account)


def forged_helper_run(repo, tmp_path, where, account=None):
    files = {
        "src/evil.py": redirecting(where, "tests/helper.py", "OK = True\n", False),
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "from helper import OK\n\ndef test_z():\n    assert OK\n",
    }
    return run_one(repo, tmp_path, files, "tests/test_a.py", "tests/test_z.py", account=account)


# Candidate code: the mirror of tests/ under $HOME/alt passes through a symlink, in its middle
# component, to $HOME/stash spelled another way (#289 review): in another letter case, or through
# the /System/Volumes/Data firmlink. The forged helper bytecode then lies physically in stash/.
LINKED = (
    "import os, sys\nfrom pathlib import Path\nfrom forger import forge\n"
    "home = Path(os.environ['HOME']).resolve()\n"
    "parts = str(Path(__file__).resolve().parents[1] / 'tests').strip('/').split('/')\n"
    "head = home / 'alt' / os.path.join(*parts[: len(parts) // 2])\n"
    "head.mkdir(parents=True)\n"
    "(home / 'stash').mkdir()\n"
    "target = {target}\n"
    "os.symlink(target, head / parts[len(parts) // 2])\n"
    "sys.pycache_prefix = str(home / 'alt')\n"
    "forge('tests/helper.py', 'OK = True\\n', False)\n"
)
LINK_TARGETS = {
    "case": "os.path.join(os.path.relpath(home, head), 'STASH')",
    "firmlink": "'/System/Volumes/Data' + str(home / 'stash')",
    "closed": "str(head / 'closed' / 'stash')",
}
# #289 round 3: stash/ lies in a directory next to the link that the candidate closes (mode 000)
# when its Python exits, after the import system has read the forged helper through the link.
CLOSED_LINKED = LINKED.replace("import os, sys", "import atexit, os, sys").replace(
    "(home / 'stash').mkdir()\n",
    "(head / 'closed' / 'stash').mkdir(parents=True)\n"
    "atexit.register(os.chmod, head / 'closed', 0)\n",
)


def case_insensitive(path):
    """Whether the filesystem holding ``path`` gives ``path`` for another letter case of its name:
    the case-variant link resolves only there (APFS by default, not ext4 on Linux CI)."""
    path.stat()  # it must be visible, or the probe would skip the test silently
    other = path.with_name(path.name.swapcase())
    return other.exists() and os.path.samefile(other, path)


def linked_helper_run(repo, tmp_path, how, account=None):
    files = {
        "src/evil.py": (CLOSED_LINKED if how == "closed" else LINKED).format(
            target=LINK_TARGETS[how]
        ),
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "from helper import OK\n\ndef test_z():\n    assert OK\n",
    }
    return run_one(repo, tmp_path, files, "tests/test_a.py", "tests/test_z.py", account=account)


# An honest test that writes bytecode outside the run's prefix in ways real suites do: py_compile
# with an explicit cfile, compileall's legacy layout, a child Python with a scrubbed environment
# importing a tmp_path module (it writes tmp_path/__pycache__), and leaves a symlink and a
# directory it closed to itself (mode 000) behind.
HONEST = (
    "import compileall, os, py_compile, subprocess, sys\n"
    "def test_bytecode_in_tmp_path(tmp_path):\n"
    "    (tmp_path / 'm.py').write_text('X = 1\\n')\n"
    "    py_compile.compile(str(tmp_path / 'm.py'), cfile=str(tmp_path / 'out' / 'm.pyc'),\n"
    "                       doraise=True)\n"
    "    assert compileall.compile_file(str(tmp_path / 'm.py'), legacy=True, quiet=1)\n"
    "    done = subprocess.run([sys.executable, '-c', 'import m'], cwd=tmp_path, env={},\n"
    "                          capture_output=True, text=True)\n"
    "    assert done.returncode == 0, done.stderr\n"
    "    assert (tmp_path / 'out' / 'm.pyc').is_file() and (tmp_path / 'm.pyc').is_file()\n"
    "    assert list((tmp_path / '__pycache__').glob('m.*.pyc'))\n"
    "def test_staged_install(tmp_path):\n"
    "    import colorsys\n"
    "    site = os.path.dirname(os.path.realpath(colorsys.__file__))\n"
    "    staged = tmp_path / 'stage' / site.lstrip('/') / '__pycache__'\n"
    "    staged.mkdir(parents=True)\n"
    "    tag = sys.implementation.cache_tag\n"
    "    (staged / f'colorsys.{tag}.pyc').write_bytes(b'x')\n"
    "def test_symlink_and_closed_dir(tmp_path):\n"
    "    (tmp_path / 'link').symlink_to(tmp_path)\n"
    "    (tmp_path / 'closed').mkdir()\n"
    "    (tmp_path / 'closed' / 'f').write_text('x')\n"
    "    os.chmod(tmp_path / 'closed', 0)\n"
)


@pytest.mark.parametrize("where", sorted(PREFIXES))
def test_a_redirected_pytest_pyc_for_a_deferred_test_fails_the_run(repo, tmp_path, where):
    run = forged_test_run(repo, tmp_path, where)
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed and not run.plain_failure
    assert "FORGED_BYTECODE" in run.why and "tests/test_z" in run.why, run.why


@pytest.mark.parametrize("where", ["home", "tmpdir", "workdir"])
def test_a_redirected_plain_pyc_for_a_protected_helper_fails_the_run(repo, tmp_path, where):
    run = forged_helper_run(repo, tmp_path, where)
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


def test_a_redirect_through_a_case_variant_symlink_fails_the_run(repo, tmp_path):
    if not case_insensitive(tmp_path):  # the run root is tmp_path / "runs"
        pytest.skip("case-sensitive filesystem: no case-variant link resolves")
    run = linked_helper_run(repo, tmp_path, "case")
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


def test_a_redirect_through_a_firmlink_symlink_fails_the_run(repo, tmp_path):
    """Whether Seatbelt lets the run use the /System/Volumes/Data spelling or not, the run must
    not pass; when the forgery ran, the scan is what fails it."""
    if not os.path.isdir("/System/Volumes/Data" + str(tmp_path)):
        pytest.skip("no APFS firmlink to the run directory here")
    run = linked_helper_run(repo, tmp_path, "firmlink")
    assert not run.observation.passed
    if run.returncode == 0:
        assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


def test_a_redirect_through_a_directory_closed_at_exit_fails_the_run(repo, tmp_path):
    run = linked_helper_run(repo, tmp_path, "closed")
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


def test_honest_bytecode_symlinks_and_closed_dirs_in_tmp_path_pass(repo, tmp_path):
    run = run_one(repo, tmp_path, {"tests/test_honest.py": HONEST}, "tests/test_honest.py")
    assert run.observation.passed, (run.why, run.log_path.read_text())


# --- the same under the separate verifier account -------------------------------------------

ACCOUNT = VerifierAccount()


@pytest.fixture
def account_run_root():
    """The account's run root is shared: remove what these tests leave there."""
    root = ACCOUNT.run_root
    before = set(os.listdir(root)) if root.is_dir() else set()
    yield
    for name in (set(os.listdir(root)) if root.is_dir() else set()) - before:
        ACCOUNT.remove(root / name)
        shutil.rmtree(root / name, ignore_errors=True)


@pytest.mark.verifier_account
@pytest.mark.parametrize("where", sorted(PREFIXES))
def test_account_a_redirected_pytest_pyc_fails_the_run(repo, tmp_path, where, account_run_root):
    run = forged_test_run(repo, tmp_path, where, account=ACCOUNT)
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/test_z" in run.why, run.why


@pytest.mark.verifier_account
def test_account_a_redirected_plain_pyc_fails_the_run(repo, tmp_path, account_run_root):
    run = forged_helper_run(repo, tmp_path, "home", account=ACCOUNT)
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


@pytest.mark.verifier_account
def test_account_a_redirect_through_a_case_variant_symlink_fails(repo, tmp_path, account_run_root):
    if not case_insensitive(ACCOUNT.run_root):
        pytest.skip("case-sensitive filesystem: no case-variant link resolves")
    run = linked_helper_run(repo, tmp_path, "case", account=ACCOUNT)
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


@pytest.mark.verifier_account
def test_account_honest_tmp_path_runs_pass(repo, tmp_path, account_run_root):
    """pytest-of-<account> under TMPDIR is 0700 to the account: the owner cannot read it, so the
    scan must run as the account, and an honest run using tmp_path must not fail."""
    files = {"tests/test_honest.py": HONEST}
    run = run_one(repo, tmp_path, files, "tests/test_honest.py", account=ACCOUNT)
    assert run.observation.passed, (run.why, run.log_path.read_text())
