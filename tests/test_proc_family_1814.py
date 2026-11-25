"""#1814 (follow-ups of #1812's review): the Linux sweep decodes the mount table with
errors=replace, so a mount path that is not valid UTF-8 cannot crash it (unknown stays
"restricted", never "ours"), and the DISPATCHED mark is read in the one spelling the journal
writes (``<boot id> <repr(float)>``), as the start mark of #1802. Fake mountinfo and /proc only."""

import pytest
from test_proc_start import BOOT, _fake_proc

from cairnwork import proc_restrict, proc_start

PROC = "22 1 0:21 / /proc rw,nosuid,nodev,noexec,relatime shared:12 - proc proc {super}\n"
BAD_BYTES = [b"\xff", b"\x80", b"\xc3\x28", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\x00\xff"]


def table(tmp_path, *lines: bytes) -> str:
    path = tmp_path / "mountinfo"
    path.write_bytes(b"".join(lines))
    return str(path)


def mount(path: bytes, source: bytes = b"tmpfs") -> bytes:
    return b"31 1 0:40 / " + path + b" rw,nosuid shared:3 - tmpfs " + source + b" rw\n"


@pytest.mark.parametrize("bad", BAD_BYTES)
@pytest.mark.parametrize("where", ["path", "source"])
def test_a_non_utf8_byte_in_another_mount_does_not_crash_the_sweep(tmp_path, bad, where):
    other = mount(b"/mnt/a" + bad, b"x") if where == "path" else mount(b"/mnt/a", b"s" + bad)
    ok = table(tmp_path, other, PROC.format(super="rw").encode())
    assert proc_restrict.restriction(ok) == ""  # the /proc mount still decides
    closed = table(tmp_path, other, PROC.format(super="rw,hidepid=2").encode())
    assert proc_restrict.restriction(closed) == "hidepid=2 on /proc"


@pytest.mark.parametrize("bad", BAD_BYTES)
def test_unknown_stays_restricted_when_the_table_has_no_proc_mount(tmp_path, bad):
    only_other = table(tmp_path, mount(b"/mnt/a" + bad))
    assert proc_restrict.restriction(only_other) == "mount options of /proc unreadable"


@pytest.mark.parametrize("bad", BAD_BYTES)
def test_a_non_utf8_byte_in_the_proc_line_itself_is_not_taken_as_open(tmp_path, bad):
    line = PROC.format(super="rw,hidepid=2").encode().replace(b"proc proc", b"proc pr" + bad)
    assert proc_restrict.restriction(table(tmp_path, line)) == "hidepid=2 on /proc"
    odd = PROC.format(super="rw,hidepid=0").encode().replace(b"hidepid=0", b"hidepid=0" + bad)
    assert proc_restrict.restriction(table(tmp_path, odd)) != ""  # not exactly 0: restricted


def test_an_ordinary_table_parses_as_before(tmp_path):
    plain = table(tmp_path, mount(b"/mnt/ok"), PROC.format(super="rw,hidepid=1").encode())
    assert proc_restrict.restriction(plain) == "hidepid=1 on /proc"


CANONICAL = ["10000.0", "0.5", "12345.678901", "1.0", "0.0", repr(9999999999.123456)]
LOOSE = [
    "+10000.0", "-10000.0", "1e4", "1.0e4", "1E4", "1_0000.0", "10_000.5", "10000", "10000.",
    ".5", "007.5", "٣٢.٥", "10000.0\n", " 10000.0", "10000.0 ", "10000.0\t", "1 0.0",
    "nan", "inf", "-inf", "1e400", "0x10.0", "", "9" * 400 + ".0",
    "10000.50", "1.00", "0.50", "12345.6780", "00.5", "0.10", "1.5000000000",
    "0.1000000000000000055", "123456789.123456789123", "1e+16", "1e16", "1e-05", "1.0e-05",
]  # fmt: skip


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(proc_start, "LINUX", True)


@pytest.mark.parametrize("at", CANONICAL)
def test_the_spelling_the_journal_writes_is_read(tmp_path, linux, at):
    proc = _fake_proc(tmp_path / "proc")
    assert proc_start._dispatched(f"{BOOT} {at}", proc) == float(at)


@pytest.mark.parametrize("at", LOOSE)
def test_every_other_spelling_is_refused_so_the_strict_rule_holds(tmp_path, linux, at):
    proc = _fake_proc(tmp_path / "proc", rows=[(41, 1.0)])
    assert proc_start._dispatched(f"{BOOT} {at}", proc) is None
    assert proc_start.not_older([41], f"{BOOT} {at}", 0.0, proc) == [41]  # still counted


@pytest.mark.parametrize("seconds", [0.5, 1.0, 123.456, 86400.25, 3.0e6 + 0.125])
def test_what_boot_mark_writes_matches_the_canonical_spelling(
    tmp_path, linux, monkeypatch, seconds
):
    proc = _fake_proc(tmp_path / "proc")
    monkeypatch.setattr(proc_start.time, "CLOCK_BOOTTIME", 7, raising=False)
    monkeypatch.setattr(proc_start.time, "clock_gettime", lambda _clock: seconds)
    mark = proc_start.boot_mark(proc)
    assert mark.startswith(f"{BOOT} ")
    assert proc_start._dispatched(mark, proc) == seconds


def test_a_value_repr_would_write_in_exponent_form_is_refused_so_the_strict_rule_holds(
    tmp_path, linux
):
    # repr() writes 1e+16 / 1e-05 for such values; boot-relative seconds never get there, and a
    # refused mark only ever means "strict" (every pid counts), so refusing is the safe direction.
    proc = _fake_proc(tmp_path / "proc")
    for at in (repr(1e16), repr(1e-05)):
        assert proc_start._dispatched(f"{BOOT} {at}", proc) is None


NON_ASCII_DIGITS = ["\u0663", "\u06f3", "\u0969", "\uff13", "\U0001d7ce"]  # 5 scripts
NEVER_WRITTEN = [
    "0.50", "10000.000", "0.1000000000000000000001", "12345.6789012345678901",
    "10000000000000000.0", "1" + "0" * 300 + ".0", "1e+16", "1e-05",
]  # fmt: skip


@pytest.mark.parametrize("digit", NON_ASCII_DIGITS)
@pytest.mark.parametrize("shape", ["1{d}.5", "10.{d}", "{d}.5", "12.5{d}", "1{d}{d}.25"])
def test_ascii_led_spellings_with_non_ascii_digits_are_refused(tmp_path, linux, digit, shape):
    at = shape.format(d=digit)
    proc = _fake_proc(tmp_path / "proc", rows=[(41, 1.0), (42, 2.0)])
    mark = f"{BOOT} {at}"
    assert proc_start._dispatched(mark, proc) is None
    assert proc_start.not_older([41, 42], mark, 0.0, proc) == [41, 42]


@pytest.mark.parametrize("at", NEVER_WRITTEN)
def test_spellings_repr_never_writes_are_refused(tmp_path, linux, at):
    proc = _fake_proc(tmp_path / "proc", rows=[(41, 1.0), (42, 2.0)])
    mark = f"{BOOT} {at}"
    assert proc_start._dispatched(mark, proc) is None
    assert proc_start.not_older([41, 42], mark, 0.0, proc) == [41, 42]


@pytest.mark.parametrize("at", ["10000.0", "0.5", "12345.678901"])
def test_spellings_repr_writes_are_read(tmp_path, linux, at):
    proc = _fake_proc(tmp_path / "proc")
    assert proc_start._dispatched(f"{BOOT} {at}", proc) == float(at)
