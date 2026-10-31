"""#37: a launchd-started system daemon (distnoted on a full-Xcode host) must not be counted as a
process the candidate left behind, but anything merely disguised as one must still be counted.

These tests drive ``_escaped_count`` with injected process data, so the exemption logic is checked
on every host; the real full-Xcode behaviour is exercised by the sandbox test in
test_verifier_account.py.
"""

import ctypes
from pathlib import Path
from types import SimpleNamespace

import cairnwork.verifier_account as va
import cairnwork.verifier_escape as ve

DAEMON = "/usr/sbin/distnoted"  # the one real launchd daemon seen on full-Xcode hosts
T0 = "1793429308.357318"  # a kernel start time as the helper prints it (sec.usec)


def _seen(paths):
    """{pid: path} as the helper reports it: {pid: (start, path)}, all started at T0."""
    return None if paths is None else {pid: (T0, path) for pid, path in paths.items()}


def _account(monkeypatch, procs, real, apple=(DAEMON,)):
    # The daemon exemption is macOS-only (proc_pidpath); force it on so the logic is tested on any
    # host (CI's test jobs run on Linux). A separate test covers the off-Darwin path.
    monkeypatch.setattr(va.sys, "platform", "darwin")
    monkeypatch.setattr(va.VerifierAccount, "_list", lambda self, deadline=None, attempts=1: procs)
    seen = _seen(real)
    monkeypatch.setattr(va.VerifierAccount, "_real_paths", lambda self, pids: seen)
    monkeypatch.setattr(ve, "apple_signed", lambda path: path in apple)
    return va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter"))


def test_real_launchd_daemon_is_exempt(monkeypatch):
    acct = _account(monkeypatch, [(100, 1)], {100: DAEMON})
    assert acct._escaped_count() == 0


def test_disguised_daemon_is_still_counted(monkeypatch):
    # `exec -a /usr/sbin/distnoted sleep`: argv[0] says distnoted, but the REAL path is /bin/sleep,
    # Apple-signed yet not allow-listed — it must still count (the issue's "Done when").
    acct = _account(monkeypatch, [(200, 1)], {200: "/bin/sleep"}, apple=(DAEMON, "/bin/sleep"))
    assert acct._escaped_count() == 1


def test_daemon_path_but_not_launchd_parent_is_counted(monkeypatch):
    # the candidate ran distnoted itself (parent is the candidate, not launchd): not exempt.
    acct = _account(monkeypatch, [(100, 999)], {100: DAEMON})
    assert acct._escaped_count() == 1


def test_daemon_path_but_not_apple_signed_is_counted(monkeypatch):
    acct = _account(monkeypatch, [(100, 1)], {100: DAEMON}, apple=())
    assert acct._escaped_count() == 1


def test_mix_counts_only_the_escapees(monkeypatch):
    procs = [(100, 1), (200, 1), (300, 200)]  # real daemon, disguised-as-daemon, plain escapee
    real = {100: DAEMON, 200: "/bin/sleep", 300: "/private/tmp/evil"}
    acct = _account(monkeypatch, procs, real, apple=(DAEMON, "/bin/sleep"))
    assert acct._escaped_count() == 2


def test_no_processes_is_zero(monkeypatch):
    acct = _account(monkeypatch, [], {})
    assert acct._escaped_count() == 0


def test_unknown_process_list_is_cannot_tell(monkeypatch):
    acct = _account(monkeypatch, None, {})
    assert acct._escaped_count() == -1


def test_unreadable_real_paths_fail_closed(monkeypatch):
    # if the real paths cannot be read, the count is unknown, never zero (fail closed).
    acct = _account(monkeypatch, [(100, 1)], None)
    assert acct._escaped_count() == -1


def test_a_pid_with_no_resolved_path_is_counted(monkeypatch):
    # proc_pidpath returned nothing for this pid (it exited mid-scan): count it, do not exempt.
    acct = _account(monkeypatch, [(100, 1)], {})
    assert acct._escaped_count() == 1


def test_off_darwin_counts_every_process_without_proc_pidpath(monkeypatch):
    # Off macOS there is no launchd distnoted and no proc_pidpath: every process counts, and the
    # real-path helper must never be called (it would crash). A would-be daemon still counts.
    acct = _account(monkeypatch, [(100, 1)], {100: DAEMON})
    monkeypatch.setattr(va.sys, "platform", "linux")

    def _boom(self, pids):
        raise AssertionError("proc_pidpath must not run off Darwin")

    monkeypatch.setattr(va.VerifierAccount, "_real_paths", _boom)
    assert acct._escaped_count() == 1


def _rec(*pairs, start=T0):
    return "".join(f"{pid}\0{start}\0{path}\0" for pid, path in pairs)


def test_parse_pidpaths_reads_well_formed_records():
    out = _rec((100, DAEMON), (101, "/tmp/a b\tc"))
    want = {100: (T0, DAEMON), 101: (T0, "/tmp/a b\tc")}
    assert ve.parse_pidpaths(out, [100, 101, 102]) == want
    assert ve.parse_pidpaths("", [100]) == {}


def test_parse_pidpaths_keeps_a_newline_path_as_one_record():
    forged = "/tmp/x\n4242\t" + DAEMON
    assert ve.parse_pidpaths(_rec((4242, forged)), [4242]) == {4242: (T0, forged)}
    assert not ve.is_exempt(forged, 1)


def test_parse_pidpaths_fails_closed_on_malformed_output():
    bad = [
        _rec((4242, "/x"), (4242, DAEMON)),  # repeated pid
        _rec((7, DAEMON)),  # pid not queried
        _rec((4242, "/x"))[:-1],  # truncated
        "4242\0",  # field count not a multiple of three
        f"4242\0{DAEMON}\0",  # pid and path only (the earlier format)
        f"42x\0{T0}\0/x\0",  # non-decimal pid
        f"4242\0{T0}\0\0",  # empty path
        _rec((4242, DAEMON), start="1793429308"),  # no usec
        _rec((4242, DAEMON), start="1793429308.35731"),  # five usec digits
        _rec((4242, DAEMON), start="-1.000000"),  # signed
        _rec((4242, DAEMON), start="1e9.000000"),  # not decimal
        f"4242\t{DAEMON}\n",  # old line format
    ]
    for out in bad:
        assert ve.parse_pidpaths(out, [4242]) is None, out


def test_garbled_helper_output_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    monkeypatch.setattr(
        va.VerifierAccount, "_list", lambda self, deadline=None, attempts=1: [(100, 1)]
    )
    monkeypatch.setattr(
        va.VerifierAccount, "_real_paths", lambda self, pids: ve.parse_pidpaths("100\0", pids)
    )
    acct = va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter"))
    assert acct._escaped_count() == -1


def _racing_account(monkeypatch, survives_kills=1):
    """A process table where pid 100 (ppid 1) starts pid 101 while its path is being read and is
    then running the real daemon: the first list shows only 100, a later one shows 101 too. The
    first ``survives_kills`` kills leave 100 running; later kills end everything."""
    monkeypatch.setattr(va.sys, "platform", "darwin")
    table = [(100, 1)]
    kills = []

    def listed(self, deadline=None, attempts=1):
        return list(table)

    def real_paths(self, pids):
        if (100, 1) in table and (101, 100) not in table:
            table.append((101, 100))  # started after the list was read, before the path check
        return {pid: (T0, DAEMON if pid == 100 else "/private/tmp/child") for pid in pids}

    def run(argv, **kwargs):
        assert "/bin/kill" in argv, argv
        kills.append(argv)
        if len(kills) > survives_kills:
            table.clear()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(va.VerifierAccount, "_list", listed)
    monkeypatch.setattr(va.VerifierAccount, "_real_paths", real_paths)
    monkeypatch.setattr(ve, "apple_signed", lambda path: path == DAEMON)
    monkeypatch.setattr(va.subprocess, "run", run)
    return va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter")), table, kills


def test_a_process_started_while_the_paths_are_read_is_counted(monkeypatch):
    acct, table, _ = _racing_account(monkeypatch)
    assert acct._escaped_count() == 1
    assert table == [(100, 1), (101, 100)]


def test_kill_all_does_not_stop_while_a_process_started_after_the_list_runs(monkeypatch):
    acct, table, kills = _racing_account(monkeypatch)
    assert acct.kill_all() == 0
    assert len(kills) == 2 and table == []


def _listing_account(monkeypatch, lists, seen=None):
    """Each _list call returns the next of ``lists`` (None: ps could not be read). The n-th helper
    call returns ``seen[n]`` (default: every asked pid runs the real daemon, started at T0).
    Returns the account and the pids each helper call was asked for."""
    monkeypatch.setattr(va.sys, "platform", "darwin")
    answers = iter(lists)
    asked = []

    def real_paths(self, pids):
        asked.append(list(pids))
        return seen[len(asked) - 1] if seen else {pid: (T0, DAEMON) for pid in pids}

    monkeypatch.setattr(va.VerifierAccount, "_list", lambda self, d=None, a=1: next(answers))
    monkeypatch.setattr(va.VerifierAccount, "_real_paths", real_paths)
    monkeypatch.setattr(ve, "apple_signed", lambda path: path == DAEMON)
    return va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter")), asked


def test_a_confirmed_daemon_is_exempt(monkeypatch):
    acct, asked = _listing_account(monkeypatch, [[(100, 1)], [(100, 1)]])
    assert acct._escaped_count() == 0
    assert asked == [[100], [100]]  # the path check, then the confirmation


def test_a_list_that_keeps_changing_is_unknown_not_zero(monkeypatch):
    acct, _ = _listing_account(monkeypatch, [[(100 + n, 1)] for n in range(10)])
    assert acct._escaped_count() == -1


def test_an_unreadable_second_list_is_unknown_not_zero(monkeypatch):
    acct, _ = _listing_account(monkeypatch, [[(100, 1)], None])
    assert acct._escaped_count() == -1


def test_a_daemon_restarted_once_by_launchd_is_still_exempt(monkeypatch):
    # launchd replaced the daemon between two lists: the new pid is checked and found exempt.
    acct, _ = _listing_account(monkeypatch, [[(100, 1)], [(105, 1)], [(105, 1)]])
    assert acct._escaped_count() == 0


def test_a_daemon_restarted_twice_by_launchd_is_still_exempt(monkeypatch):
    acct, asked = _listing_account(monkeypatch, [[(100, 1)], [(105, 1)], [(110, 1)], [(110, 1)]])
    assert acct._escaped_count() == 0
    assert asked == [[100], [105], [110], [110]]


def test_a_daemon_restarted_three_times_is_unknown(monkeypatch):
    lists = [[(100, 1)], [(105, 1)], [(110, 1)], [(115, 1)], [(115, 1)]]
    acct, _ = _listing_account(monkeypatch, lists)
    assert acct._escaped_count() == -1


def test_a_daemon_that_exits_before_the_second_list_is_still_clean(monkeypatch):
    acct, asked = _listing_account(monkeypatch, [[(100, 1), (105, 1)], [(105, 1)]])
    assert acct._escaped_count() == 0
    assert asked == [[100, 105], [105]]


def test_a_reused_pid_with_a_new_start_time_is_checked_again(monkeypatch):
    # Same (pid, ppid) on both lists, but the kernel start time moved by one microsecond: another
    # process has the pid now, so the next round reads its path (not the daemon) and counts it.
    t1 = "1793429308.357319"
    seen = [{100: (T0, DAEMON)}, {100: (t1, DAEMON)}, {100: (t1, "/private/tmp/x")}]
    acct, asked = _listing_account(monkeypatch, [[(100, 1)]] * 3, seen)
    assert acct._escaped_count() == 1
    assert asked == [[100], [100], [100]]


def test_a_confirmation_with_another_path_or_no_record_is_checked_again(monkeypatch):
    for confirm in ({100: (T0, "/private/tmp/x")}, {}, None):
        seen = [{100: (T0, DAEMON)}, confirm, {100: (T0, "/private/tmp/x")}]
        acct, asked = _listing_account(monkeypatch, [[(100, 1)]] * 3, seen)
        assert acct._escaped_count() == 1, confirm
        assert len(asked) == 3, confirm


def test_one_exempt_pid_with_a_new_start_time_is_enough_to_check_again(monkeypatch):
    # Two exempt daemons; at the confirmation pid 100 is unchanged but pid 105 has a new start
    # time. The next round reads 105's path (not the daemon) and counts it.
    t1 = "1793429308.357319"
    seen = [
        {100: (T0, DAEMON), 105: (T0, DAEMON)},
        {100: (T0, DAEMON), 105: (t1, DAEMON)},
        {100: (T0, DAEMON), 105: (t1, "/private/tmp/x")},
    ]
    acct, asked = _listing_account(monkeypatch, [[(100, 1), (105, 1)]] * 3, seen)
    assert acct._escaped_count() == 1
    assert asked == [[100, 105], [100, 105], [100, 105]]


def _run_helper(monkeypatch, capsys, starts, path=b"/usr/sbin/distnoted", pid_seen=None):
    """Run PIDPATH_HELPER in this process for pid 100 against a stand-in libproc: each
    proc_pidinfo call reports the next of ``starts`` ((sec, usec), or None: the call fails)."""
    calls = iter(starts)

    def proc_pidinfo(pid, flavor, arg, ref, size):
        start = next(calls)
        if start is None:
            return 0
        info = ref._obj
        info.pbi_pid = pid if pid_seen is None else pid_seen
        info.pbi_start_tvsec, info.pbi_start_tvusec = start
        return ctypes.sizeof(info)

    def proc_pidpath(pid, buf, size):
        if not path:
            return 0
        buf.value = path
        return len(path)

    libc = SimpleNamespace(proc_pidinfo=proc_pidinfo, proc_pidpath=proc_pidpath)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: libc)
    monkeypatch.setattr(va.sys, "argv", ["-c", "100"])
    exec(compile(ve.PIDPATH_HELPER, "pidpath-helper", "exec"), {"__name__": "__main__"})  # noqa: S102
    return capsys.readouterr().out


def test_the_helper_reports_start_time_and_path(monkeypatch, capsys):
    out = _run_helper(monkeypatch, capsys, [(1793429308, 7), (1793429308, 7)])
    assert out == "\0".join(["100", "1793429308.000007", DAEMON, ""])
    assert ve.parse_pidpaths(out, [100]) == {100: ("1793429308.000007", DAEMON)}


def test_the_helper_gives_no_record_when_the_start_time_changes_or_a_read_fails(
    monkeypatch, capsys
):
    cases = [
        {"starts": [(1793429308, 7), (1793429308, 8)]},  # another process by the path read
        {"starts": [None, (1793429308, 7)]},  # start unreadable before the path
        {"starts": [(1793429308, 7), None]},  # start unreadable after the path
        {"starts": [(1793429308, 7), (1793429308, 7)], "path": b""},  # path unreadable
        {"starts": [(1793429308, 7), (1793429308, 7)], "pid_seen": 101},  # another pid's record
    ]
    for case in cases:
        assert _run_helper(monkeypatch, capsys, **case) == "", case
