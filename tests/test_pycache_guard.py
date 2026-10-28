"""pycache_guard.forged_bytecode on hand-built prefixes (no sandbox needed).

The behavioural witnesses, which plant real forgeries from candidate code inside the real
sandbox, are in test_pycache_prefix.py.
"""

import os

import pytest

from cairnwork import pycache_guard
from cairnwork.pycache_guard import forged_bytecode
from cairnwork.reasons import why_not_passed

TAG = "cpython-312"


@pytest.fixture
def run(tmp_path):
    work = tmp_path / "work"
    checkout = work / "checkout"
    run_dir = tmp_path / "private" / "run"
    prefix = run_dir / "tmp" / "pycache"
    for directory in (checkout, prefix):
        directory.mkdir(parents=True)
    return checkout, (work, run_dir), prefix


def plant(prefix, source, name=None):
    """A file in the prefix where Python would look for the bytecode of ``source``."""
    source = str(source)
    name = name or os.path.basename(source)[:-3] + f".{TAG}.pyc"
    target = prefix / os.path.dirname(source).lstrip("/") / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    return target


def check(run):
    checkout, roots, prefix = run
    return forged_bytecode(prefix, checkout, roots)


def test_no_prefix_and_an_empty_prefix_are_clean(run, tmp_path):
    checkout, roots, prefix = run
    assert forged_bytecode(tmp_path / "missing", checkout, roots) == ()
    assert check(run) == ()


@pytest.mark.parametrize(
    "rel", ["src/a.py", "src/pkg/mod.py", "scripts/ops/tool.py", "src/.hidden/m.py"]
)
def test_plain_bytecode_of_a_changeable_checkout_file_is_allowed(run, rel):
    plant(run[2], run[0] / rel)
    assert check(run) == ()


def test_bytecode_of_files_the_run_wrote_outside_the_checkout_is_allowed(run):
    checkout, (work, run_dir), prefix = run
    plant(prefix, run_dir / "tmp" / "pytest-of-x" / "gen.py")
    plant(prefix, work / "sibling" / "gen.py")
    assert check(run) == ()


@pytest.mark.parametrize(
    "rel",
    [
        "tests/test_z.py",  # a deferred test
        "tests/helper.py",  # anything under the test root
        "tests/conftest.py",
        "src/pkg/conftest.py",  # conftest anywhere
        "src/test_inline.py",  # test-named files anywhere
        "src/pkg/thing_test.py",
        "src/testing/fixtures.py",
        "setup.py",
        "app.py",  # a root module: protected without proof from the base commit
        "sitecustomize.py",
    ],
)
def test_bytecode_of_a_protected_input_is_forged(run, rel):
    plant(run[2], run[0] / rel)
    assert check(run) == (f"{rel} (protected input)",)


@pytest.mark.parametrize(
    ("rel", "shown"),
    [
        ("Tests/helper.py", "Tests/helper.py"),
        ("TESTS/test_z.py", "TESTS/test_z.py"),
        ("src/CONFTEST.py", "src/CONFTEST.py"),
        ("src/Test_inline.py", "src/Test_inline.py"),
        ("Setup.py", "Setup.py"),
    ],
)
def test_case_variants_of_a_protected_input_are_forged(run, rel, shown):
    """Case-insensitive APFS finds Tests/helper.cpython-312.pyc for tests/helper.py."""
    plant(run[2], run[0] / rel)
    assert check(run) == (f"{shown} (protected input)",)


def test_a_case_variant_of_the_checkout_path_is_the_checkout(run):
    checkout, roots, prefix = run
    variant = str(checkout.parent) + "/" + checkout.name.upper()
    plant(prefix, variant + "/tests/helper.py")
    plant(prefix, variant + "/src/a.py")
    assert check(run) == ("tests/helper.py (protected input)",)


def test_a_case_variant_above_the_run_is_judged_as_spelled(run):
    """Case-sensitive filesystems: a case variant of the checkout's parents is another place.
    Protected names are still protected; anything else must lie in a write root as spelled."""
    checkout, roots, prefix = run
    variant = str(checkout).upper()
    plant(prefix, variant + "/src/a.py")
    plant(prefix, variant + "/tests/helper.py")
    found = check(run)
    assert f"{variant}/src/a.py (outside the run)" in found
    assert "tests/helper.py (protected input)" in found
    assert len(found) == 2


@pytest.mark.parametrize(
    "spelled",
    [
        "te\u017fts",  # long s: APFS finds teſts/ for tests/
        "te\ufb05s",  # ligature
        "\u212aeep",  # Kelvin sign
        "caf\u0065\u0301",  # NFD
    ],
)
def test_a_non_ascii_name_in_the_prefix_is_forged(run, spelled):
    checkout, roots, prefix = run
    plant(prefix, checkout / spelled / "helper.py")
    plant(prefix, checkout / "src" / "a.py", name=f"{spelled}.{TAG}.pyc")
    found = check(run)
    assert f"{spelled} (non-ASCII name)" in found
    assert f"src/{spelled}.{TAG}.pyc (non-ASCII name)" in found


def test_a_pytest_tag_in_any_case_is_forged(run):
    plant(run[2], run[0] / "src" / "a.py", name="a.CPYTHON-312-PYTEST-8.4.2.PYC")
    plant(run[2], run[0] / "tests" / "helper.py", name="helper.CPYTHON-312.PYC")
    found = check(run)
    assert "src/a.CPYTHON-312-PYTEST-8.4.2.PYC (pytest-rewritten bytecode)" in found
    assert "tests/helper.py (protected input)" in found


def test_the_directories_of_the_run_targets_are_protected(run):
    checkout, roots, prefix = run
    plant(prefix, checkout / "qa" / "helper.py")
    assert forged_bytecode(prefix, checkout, roots) == ()  # qa/ is not a test dir by name
    found = forged_bytecode(prefix, checkout, roots, ("qa/check_a.py::test_x",))
    assert found == ("qa/helper.py (protected input)",)
    assert forged_bytecode(prefix, checkout, roots, ("QA/check_a.py",)) == found


def test_any_pytest_rewritten_pyc_is_forged(run):
    plant(run[2], run[0] / "src" / "a.py", name=f"a.{TAG}-pytest-8.4.2.pyc")
    assert check(run) == ("src/a.cpython-312-pytest-8.4.2.pyc (pytest-rewritten bytecode)",)


def test_bytecode_of_a_module_outside_the_run_is_forged(run):
    plant(run[2], "/usr/lib/python3.12/colorsys.py")
    assert check(run) == ("/usr/lib/python3.12/colorsys.py (outside the run)",)


def test_a_dotted_name_counts_as_every_source_it_could_stand_for(run):
    plant(run[2], run[0] / "src" / "conftest.py", name=f"conftest.x.{TAG}.opt-1.pyc")
    assert check(run) == ("src/conftest.py (protected input)",)


def test_the_real_path_of_the_checkout_is_the_checkout_too(tmp_path, run):
    checkout, roots, prefix = run
    alias = tmp_path / "alias"
    alias.symlink_to(checkout.parent)
    plant(prefix, os.path.realpath(checkout) + "/tests/test_z.py")
    plant(prefix, os.path.realpath(checkout) + "/src/a.py")
    found = forged_bytecode(prefix, alias / "checkout", (alias, roots[1]))
    assert found == ("tests/test_z.py (protected input)",)


def test_symlinks_and_special_files_are_forged(run, tmp_path):
    checkout, roots, prefix = run
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    linked_dir = prefix / str(checkout).lstrip("/") / "src"
    linked_dir.parent.mkdir(parents=True)
    linked_dir.symlink_to(elsewhere)
    (prefix / "file.pyc").symlink_to(elsewhere / "x")
    os.mkfifo(prefix / "fifo")
    found = check(run)
    assert any("src (not a plain file or directory)" in f for f in found), found
    assert "/file.pyc (not a plain file or directory)" in found
    assert "/fifo (not a plain file or directory)" in found


def test_a_prefix_that_is_not_a_plain_directory_is_forged(run, tmp_path):
    checkout, roots, prefix = run
    prefix.rmdir()
    prefix.symlink_to(tmp_path)
    assert check(run) == ("pycache is not a plain directory",)


def test_too_many_entries_fail_closed(run, monkeypatch):
    monkeypatch.setattr(pycache_guard, "MAX_ENTRIES", 3)
    for index in range(5):
        (run[2] / f"f{index}.txt").write_text("x")
    assert check(run)[-1] == "pycache has more than 3 entries"


def test_an_unreadable_directory_fails_closed(run):
    locked = run[2] / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        found = check(run)
    finally:
        locked.chmod(0o700)
    assert any(f.startswith("pycache unreadable") for f in found), found


def test_the_reason_names_the_code():
    why = why_not_passed(
        ran_sandboxed=True, returncode=0, timed_out=False, mutated=(), survivors=0,
        log_too_big=False, tests_ran=True, forged=("tests/test_z.py (protected input)",),
    )  # fmt: skip
    assert why == (
        "FORGED_BYTECODE: bytecode cache holds code the run may not supply"
        " (1: tests/test_z.py (protected input))"
    )


def test_a_sibling_whose_name_extends_a_write_root_is_outside_the_run(run):
    """``run2`` is not under the write root ``run``: a root matches only up to a path separator
    (ACH mutation scan)."""
    sibling = run[1][1].parent / "run2" / "mod.py"
    plant(run[2], sibling)
    assert check(run) == (f"{sibling} (outside the run)",)
