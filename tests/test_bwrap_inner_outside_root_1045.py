"""#1045 (b): in account mode a link whose target the account cannot reach and which lies outside
every run root is skipped silently: not masked, not refused, not counted as dangling. The
under-a-root cases are pinned in test_bwrap_inner_links.py."""

import os

import pytest
from fixture_tree import world_dir

from cairnwork_sandbox import bwrap, bwrap_inner


@pytest.fixture
def world():
    with world_dir("cw-outside-") as base:
        (base / "home").mkdir(mode=0o755)
        (base / "out").mkdir(mode=0o755)
        (base / "other").mkdir(mode=0o755)  # the only root: away from the targets
        yield base


def _policy(world):
    return bwrap.BACKEND.policy(
        (world / "other",), (), home=world / "home", mask_home_credentials=False
    )


def _assert_left_alone(policy, world):
    assert not [ln for ln in policy.text.split("\n") if ln.startswith(str(world / "out"))]
    assert not any(n.startswith(bwrap_inner.DANGLING_NOTE) for n in policy.notes)


def test_an_unreachable_dangling_target_outside_every_root_is_not_counted(world):
    (world / "out" / "priv").mkdir(mode=0o700)
    (world / "home" / ".aws").mkdir()
    (world / "home" / ".aws" / "gone").symlink_to(world / "out" / "priv" / "gone")
    _assert_left_alone(_policy(world), world)


def test_an_unstattable_target_outside_every_root_is_not_refused(world):
    locked = world / "out" / "locked"
    locked.mkdir(mode=0o700)
    (locked / "tok").write_text("secret\n")
    (world / "home" / ".aws").mkdir()
    (world / "home" / ".aws" / "x").symlink_to(locked / "tok")
    locked.chmod(0)
    try:
        if os.access(locked / "tok", os.F_OK):
            pytest.skip("running as a user that stats through a mode-0 directory (root)")
        _assert_left_alone(_policy(world), world)
    finally:
        locked.chmod(0o700)
