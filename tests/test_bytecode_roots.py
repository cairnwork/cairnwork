"""bytecode_roots.redirected_bytecode on hand-built run trees (owner mode, no sandbox needed).

The listing helper really runs (``python -I -S``) as it does after a verifier run. The
behavioural witnesses, which redirect ``sys.pycache_prefix`` from candidate code inside the real
sandbox, are in test_pycache_redirect.py.
"""

import os
import subprocess
import sys

import pytest

from cairnwork import bytecode_roots
from cairnwork.bytecode_roots import bytecode_findings, redirected_bytecode

TAG = "cpython-312"
PYTEST_TAG = f"{TAG}-pytest-8.3.4"


@pytest.fixture
def run(tmp_path):
    work = tmp_path / "r1-v0.work"
    checkout = work / "r1-v0"
    run_dir = tmp_path / "r1-v0.abc" / "run"
    for directory in (checkout / "tests", checkout / "src", checkout / ".git", run_dir / "home"):
        directory.mkdir(parents=True)
    (run_dir / "tmp" / "pycache").mkdir(parents=True)
    (checkout / "tests" / "helper.py").write_text("OK = False\n")
    (checkout / "tests" / "test_z.py").write_text("def test_z():\n    assert False\n")
    (checkout / "src" / "a.py").write_text("x = 1\n")
    return checkout, run_dir


def scan(run, **kwargs):
    checkout, run_dir = run
    return redirected_bytecode(checkout, (checkout.parent, run_dir), **kwargs)


def mirror(prefix, source, tag=TAG):
    """A file under ``prefix`` where Python would look for the bytecode of ``source``."""
    source = str(source)
    target = (
        prefix / os.path.dirname(source).lstrip("/") / f"{os.path.basename(source)[:-3]}.{tag}.pyc"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    return target


def test_a_run_without_bytecode_is_clean(run):
    checkout, run_dir = run
    (run_dir / "tmp" / "pytest-of-x" / "pytest-0").mkdir(parents=True)
    (run_dir / "tmp" / "pytest-of-x" / "pytest-current").symlink_to(
        run_dir / "tmp" / "pytest-of-x" / "pytest-0"
    )
    assert scan(run) == ()


@pytest.mark.parametrize("home", ["home/alt", "tmp/alt", "tmp/pycache/deep/er", "x"])
def test_a_mirror_of_a_protected_test_or_helper_anywhere_in_the_run_dir_fails(run, home):
    checkout, run_dir = run
    mirror(run_dir / home, checkout / "tests" / "helper.py")
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_a_mirror_in_the_work_dir_or_the_checkouts_git_fails(run):
    checkout, _ = run
    mirror(checkout.parent / "alt", checkout / "tests" / "helper.py")
    mirror(checkout / ".git" / "alt", checkout / "tests" / "test_z.py")
    assert set(scan(run)) == {
        "tests/helper.py (protected input)",
        "tests/test_z.py (protected input)",
    }


def test_a_pytest_pyc_fails_anywhere_outside_the_checkout_but_not_when_committed(run):
    checkout, run_dir = run
    (run_dir / "home" / f"x.{PYTEST_TAG}.pyc").write_bytes(b"x")
    assert scan(run) == (f"run/home/x.{PYTEST_TAG}.pyc (pytest-rewritten bytecode)",)
    (run_dir / "home" / f"x.{PYTEST_TAG}.pyc").unlink()
    (checkout / "src" / f"m.{PYTEST_TAG}.pyc").write_bytes(b"x")  # committed, mirrors nothing
    assert scan(run) == ()
    (checkout / ".git" / f"m.{PYTEST_TAG}.pyc").write_bytes(b"x")  # .git is not compared
    assert scan(run) == (f".git/m.{PYTEST_TAG}.pyc (pytest-rewritten bytecode)",)


def test_a_mirror_of_an_existing_module_outside_the_run_fails(run):
    import colorsys

    _, run_dir = run
    mirror(run_dir / "home" / "alt", colorsys.__file__)
    (reason,) = scan(run)
    assert reason.endswith("colorsys.py (outside the run)"), reason


def test_honest_bytecode_outside_the_prefix_passes(run):
    """py_compile with a cfile, compileall's legacy layout, a scrubbed child Python's
    __pycache__, the run's own prefix mirroring tmp_path and a non-protected checkout file, and
    bytecode for a source outside the run that does not exist."""
    checkout, run_dir = run
    project = run_dir / "tmp" / "pytest-of-x" / "pytest-0" / "test_a0"
    (project / "out").mkdir(parents=True)
    (project / "m.py").write_text("X = 1\n")
    (project / "out" / "m.pyc").write_bytes(b"x")
    (project / "m.pyc").write_bytes(b"x")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / f"m.{TAG}.pyc").write_bytes(b"x")
    mirror(run_dir / "tmp" / "pycache", project / "m.py")
    mirror(run_dir / "tmp" / "pycache", checkout / "src" / "a.py")
    mirror(run_dir / "home" / "alt", "/no/such/dir/mod.py")
    (run_dir / "tmp" / "pytest-of-x" / "pytest-current").symlink_to(project.parent)
    assert scan(run) == ()


def test_committed_legacy_bytecode_next_to_a_protected_input_fails(run):
    checkout, _ = run
    (checkout / "src" / "__pycache__").mkdir()
    (checkout / "src" / "__pycache__" / f"a.{TAG}.pyc").write_bytes(b"x")
    assert scan(run) == ()
    (checkout / "tests" / "__pycache__").mkdir()
    (checkout / "tests" / "__pycache__" / f"helper.{TAG}.pyc").write_bytes(b"x")
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_a_symlink_into_the_mirror_is_followed(run):
    """<P>/<head of the mirror>/<middle component> is a symlink to stash/, which holds the rest:
    the physical path stash/<rest> spells no existing directory (its first name is not one at
    /), only the spelling through the link mirrors tests/helper.py."""
    checkout, run_dir = run
    parts = str(checkout / "tests").strip("/").split("/")
    middle = len(parts) // 2
    assert not os.path.isdir("/" + parts[middle + 1])
    stash = run_dir / "home" / "stash"
    stash.joinpath(*parts[middle + 1 :]).mkdir(parents=True)
    (stash.joinpath(*parts[middle + 1 :]) / f"helper.{TAG}.pyc").write_bytes(b"x")
    head = run_dir / "home" / "alt" / os.path.join(*parts[:middle])
    head.mkdir(parents=True)
    (head / parts[middle]).symlink_to(stash)
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_a_symlink_loop_spelling_the_whole_mirror_is_followed(run):
    """One directory holding the pyc and, for every component of the mirror, a symlink to
    itself: <loop>/private/var/.../tests/helper.<tag>.pyc is that one file."""
    checkout, run_dir = run
    loop = run_dir / "tmp" / "loop"
    loop.mkdir()
    (loop / f"helper.{TAG}.pyc").write_bytes(b"x")
    for name in set(str(checkout / "tests").strip("/").split("/")):
        (loop / name).symlink_to(loop)
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_too_many_walk_steps_fail_closed(run, monkeypatch):
    checkout, run_dir = run
    monkeypatch.setattr(bytecode_roots, "MAX_STATES", 3)
    loop = run_dir / "tmp" / "loop"
    loop.mkdir()
    (loop / f"helper.{TAG}.pyc").write_bytes(b"x")
    for name in set(str(checkout / "tests").strip("/").split("/")):
        (loop / name).symlink_to(loop)
    assert scan(run)[-1] == "more than 3 steps to judge the run's bytecode"


def test_a_non_ascii_pyc_name_fails(run):
    _, run_dir = run
    (run_dir / "home" / f"x.{TAG}.p​yc").write_bytes(b"x")  # folds to .pyc once dropped
    (run_dir / "home" / "über.txt").write_text("an honest non-ASCII name")
    (reason,) = scan(run)
    assert reason.endswith("(non-ASCII name)"), reason


def test_a_long_s_directory_on_a_mirror_fails(run):
    """APFS folds U+017F onto "s", so <P>/.../te\u017fts/helper.<tag>.pyc is found for
    tests/helper.py; names are matched folded (NFD, case), which on a case-sensitive
    filesystem only adds findings."""
    checkout, run_dir = run
    folded = str(checkout / "tests").replace("/tests", "/te\u017fts")
    target = run_dir / "home" / "alt" / folded.lstrip("/") / f"helper.{TAG}.pyc"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert scan(run) == ("tests/helper.py (protected input)",)


def link_in_the_mirror(checkout, run_dir, target_of):
    """<P>/<head of the mirror>/<middle component> -> target_of(stash), with the rest of the
    mirror and helper.<tag>.pyc physically under stash/ (whose path mirrors nothing)."""
    parts = str(checkout / "tests").strip("/").split("/")
    middle = len(parts) // 2
    stash = run_dir / "home" / "stash"
    stash.joinpath(*parts[middle + 1 :]).mkdir(parents=True)
    (stash.joinpath(*parts[middle + 1 :]) / f"helper.{TAG}.pyc").write_bytes(b"x")
    head = run_dir / "home" / "alt" / os.path.join(*parts[:middle])
    head.mkdir(parents=True)
    (head / parts[middle]).symlink_to(target_of(stash, head))


def test_a_link_whose_target_differs_in_case_is_followed(run):
    """#289 review: realpath keeps ../STASH as spelled, the kernel resolves it to stash/."""
    checkout, run_dir = run
    home = run_dir / "home"
    if not (run_dir / "HOME").exists():
        pytest.skip("case-sensitive filesystem: STASH is not stash")
    upper = lambda stash, head: os.path.join(os.path.relpath(home, head), "STASH")  # noqa: E731
    link_in_the_mirror(checkout, run_dir, upper)
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_a_link_through_a_firmlink_is_followed(run):
    """/System/Volumes/Data/private/... is the same directory as /private/... (APFS firmlink),
    which no string comparison of the target sees."""
    checkout, run_dir = run
    stash = run_dir / "home" / "stash"
    firm = "/System/Volumes/Data" + os.path.realpath(run_dir / "home") + "/stash"
    if not os.path.isdir(os.path.dirname(firm)):
        pytest.skip("no APFS firmlink to the run directory here")
    link_in_the_mirror(checkout, run_dir, lambda stash, head: firm)
    assert os.path.samefile(firm, stash)
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_dotdot_after_a_link_in_the_mirror_is_followed(run):
    """<P>/<checkout>/tests is a link to q/r, so <P>/<checkout>/tests/../tests is q/tests: read
    for a source dir spelled <checkout>/tests/../tests (the same directory as tests/)."""
    checkout, run_dir = run
    mirror_tests = run_dir / "home" / "alt" / str(checkout / "tests").lstrip("/")
    mirror_tests.parent.mkdir(parents=True)
    (run_dir / "home" / "q" / "r").mkdir(parents=True)
    mirror_tests.symlink_to(run_dir / "home" / "q" / "r")
    (run_dir / "home" / "q" / "tests").mkdir()
    (run_dir / "home" / "q" / "tests" / f"helper.{TAG}.pyc").write_bytes(b"x")
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_committed_bytecode_read_with_the_root_as_prefix_fails(run):
    """sys.pycache_prefix = "/" makes Python read <checkout>/tests/helper.<tag>.pyc itself."""
    checkout, _ = run
    (checkout / "tests" / f"helper.{TAG}.pyc").write_bytes(b"x")
    (checkout / "tests" / "data.pyc").write_bytes(b"x")  # no tag: no source's cache, allowed
    assert scan(run) == ("tests/helper.py (protected input)",)


def test_a_closed_directory_is_opened_and_scanned(run):
    _, run_dir = run
    closed = run_dir / "tmp" / "closed"
    closed.mkdir()
    (closed / f"x.{PYTEST_TAG}.pyc").write_bytes(b"x")
    closed.chmod(0)
    assert scan(run) == (f"run/tmp/closed/x.{PYTEST_TAG}.pyc (pytest-rewritten bytecode)",)


@pytest.mark.skipif(sys.platform != "darwin", reason="a macOS ACL denies listing despite the mode")
def test_an_unreadable_directory_fails_closed(run):
    _, run_dir = run
    denied = run_dir / "tmp" / "denied"
    denied.mkdir()
    subprocess.run(["/bin/chmod", "+a", "everyone deny list,search", str(denied)], check=True)
    try:
        (reason,) = scan(run)
    finally:
        subprocess.run(["/bin/chmod", "-N", str(denied)], check=True)
    assert reason.startswith("run/tmp/denied unreadable"), reason


def test_too_many_entries_fail_closed(run, monkeypatch):
    _, run_dir = run
    monkeypatch.setattr(bytecode_roots, "MAX_ENTRIES", 5)
    for i in range(10):
        (run_dir / "home" / f"f{i}").write_text("x")
    assert scan(run)[-1] == "more than 5 entries where the run may write"


@pytest.mark.parametrize("python", ["/nonexistent/python3", "/usr/bin/false"])
def test_a_failing_scan_fails_closed(run, python):
    (reason,) = scan(run, python=python)
    assert reason.startswith("bytecode scan failed"), reason


def test_bytecode_findings_joins_the_prefix_check_and_the_rest(run):
    checkout, run_dir = run
    mirror(run_dir / "tmp" / "pycache", checkout / "tests" / "test_z.py", PYTEST_TAG)
    mirror(run_dir / "home" / "alt", checkout / "tests" / "helper.py")
    found = bytecode_findings(run_dir / "tmp", checkout, run_dir, (), None, sys.executable)
    assert f"tests/test_z.{PYTEST_TAG}.pyc (pytest-rewritten bytecode)" in found  # pycache_guard
    assert "tests/helper.py (protected input)" in found  # elsewhere in the run
    assert len(found) == len(set(found))


def test_a_link_that_does_not_resolve_by_name_length_is_reported(run):
    """#289 round 2: a symlink whose target cannot be resolved for a reason other than a missing,
    closed, looping or file-blocked target is an error row, not a dropped edge."""
    _, run_dir = run
    (run_dir / "home" / "long").symlink_to("x" * 300)  # a component over NAME_MAX
    (reason,) = scan(run)
    assert reason.startswith("run/home/long unreadable"), reason


def test_dangling_and_looping_links_are_no_finding(run):
    """Honest tests leave these behind; no lookup through them resolves, for Python either."""
    _, run_dir = run
    home = run_dir / "home"
    (home / "dangling").symlink_to(home / "missing")
    (home / "a").symlink_to(home / "b")
    (home / "b").symlink_to(home / "a")
    (home / "through-a-file").symlink_to(home / "f" / "x")
    (home / "f").write_text("x")
    assert scan(run) == ()


def test_a_staged_install_in_tmp_path_passes(run):
    """pip install --root / DESTDIR: <stage>/<real site dir>/__pycache__/<mod>.<tag>.pyc. Under
    the prefix <stage> Python would read <stage>/<site dir>/<mod>.<tag>.pyc, never the
    __pycache__ one: the legacy layout is read only without a prefix."""
    import colorsys

    _, run_dir = run
    site = os.path.dirname(os.path.realpath(colorsys.__file__))
    staged = run_dir / "tmp" / "stage" / site.lstrip("/") / "__pycache__"
    staged.mkdir(parents=True)
    (staged / f"colorsys.{TAG}.pyc").write_bytes(b"x")
    assert scan(run) == ()


def test_an_ancestor_that_cannot_be_listed_still_leads_to_the_run(run, tmp_path):
    """tmp_path, above both roots, is searchable but not listable: the walk from / must still
    reach committed bytecode in the checkout read with the root as prefix."""
    checkout, _ = run
    (checkout / "tests" / f"helper.{TAG}.pyc").write_bytes(b"x")
    mode = tmp_path.stat().st_mode
    tmp_path.chmod(0o311)
    try:
        found = scan(run)
    finally:
        tmp_path.chmod(mode)
    assert found == ("tests/helper.py (protected input)",)


class _Account:
    """A stand-in account (no sudo): only kill_all is reached before the helper would run."""

    def __init__(self, left):
        self.left = left

    def kill_all(self):
        if isinstance(self.left, Exception):
            raise self.left
        return self.left


@pytest.mark.parametrize(
    "left, reason",
    [
        (1, "the account's processes could not be confirmed ended"),
        (-1, "the account's processes could not be confirmed ended"),
        (OSError("ps"), "bytecode scan failed (OSError)"),
    ],
)
def test_account_mode_fails_closed_when_its_processes_are_not_ended(run, left, reason):
    assert scan(run, account=_Account(left)) == (reason,)
