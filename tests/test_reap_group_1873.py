"""#1849 option A and the account-mode shape (#1873 rounds 1-2): the group id is sudo's pid, which
never shows in the account's ps; the collector is its child. A live collector's group is its own."""

import cairnwork.verifier_group as vg
from tests.test_reap_group_1849 import COLL, MAIN, OUT, L, _acct  # noqa: F401


# Option A (t-b022 shape): a live collector's group is its own, the group kill ends it.
# Option A (t-b022 shape): a live collector's group is its own, the group kill ends it.
# Option A (t-b022 shape): a live collector's group is its own, the group kill ends it.
def test_must_pass_a_live_collector_with_overlapping_children_counts_zero(monkeypatch):
    rows = [(MAIN, MAIN, None)] + [(200 + i, MAIN, 0.1 * i + 0.15) for i in range(30)]
    world, acct = _acct(monkeypatch, rows)
    assert int(acct.reap(1, group=MAIN)) == 0


def test_must_pass_a_process_outside_the_group_is_still_counted_with_a_live_collector(
    monkeypatch,
):
    rows = [(MAIN, MAIN, None), (OUT, OUT, None), (101, MAIN, None)]
    world, acct = _acct(monkeypatch, rows)
    assert int(acct.reap(1, group=MAIN)) >= 1


def test_must_pass_a_zombie_collector_with_a_leftover_is_still_counted(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, None)])
    world.zombies = {MAIN}
    assert int(acct.reap(1, group=MAIN)) >= 1


# Round 1 of #1873: in account mode the group leader is sudo, never in the account's ps. The
# collector is the group's process whose ppid is the group id (the rows above show it that way).
def test_must_pass_a_clean_live_collector_on_the_stop_path_counts_zero(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None)])
    assert int(acct.reap(1, group=MAIN)) == 0


def test_must_pass_a_live_collector_with_a_persistent_child_counts_zero(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (102, MAIN, None)])
    assert int(acct.reap(1, group=MAIN)) == 0


def test_must_block_a_child_that_called_setsid_is_still_counted(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (102, 102, None)])
    assert int(acct.reap(1, group=MAIN)) >= 1


def test_must_block_an_orphan_leftover_with_the_collector_gone(monkeypatch):
    world, acct = _acct(monkeypatch, [(102, MAIN, None)])
    assert int(acct.reap(0, group=MAIN)) >= 1


def test_the_collector_is_the_process_whose_ppid_is_the_group_never_a_leftover():
    rows = [(1000, 100, 100, "S"), (1001, 1000, 100, "S"), (1002, 1, 100, "S")]
    assert vg._lead(rows, 100) == {1000} and vg._members(rows, 100) == {1001, 1002}
    assert vg._live(rows, 100) and not vg._live([(1000, 100, 100, "Z")], 100)
    assert vg._lead([(100, 1, 100, "S"), (101, 100, 100, "S")], 100) == {100, 101}


def test_must_block_a_collector_that_called_setsid_is_not_the_collector(monkeypatch):
    # (1000, ppid 100, pgid 1000) left the group; its child stays in group 100 with ppid 1000.
    rows = [(1000, 100, 1000, "S"), (1001, 1000, 100, "S")]
    assert vg._lead(rows, 100) == set() and vg._members(rows, 100) == {1001}
