"""#802: what an ordinary test needs from its environment under the macOS Seatbelt sandbox.

Time zone data and /etc/hosts are readable (read-only), getpass.getuser() answers a fixed name,
and nothing else nearby opened: other /private/etc and /private/var/db files stay unreadable,
neither new path is writable, and an AF_UNIX bind is still refused by ``(deny network*)`` (not by
path length; allowing it would need a path limit, a security-owner decision). The behavioural
witnesses run the real sandbox through run_verifiers, as test_seatbelt_shasum.py does.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_change

from cairnwork import verifiers
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from cairnwork_sandbox import seatbelt

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS sandbox-exec")
TZ_DATA = Path(seatbelt.TIMEZONE_DATA)
READ_ROOTS = tuple(Path(p) for p in seatbelt.SYSTEM_READ_SUBPATHS)


def _verify(repo, tmp_path, code):
    head = commit_change(repo, {"tests/test_env.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_env.py"])
    runs = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    return runs[0]


def _passed(run):
    assert run.observation.passed, Path(run.log_path).read_text()[-3000:]


def test_the_new_rules_are_realpaths_and_narrow():
    text = seatbelt.build_profile((), (), home=Path("/nonexistent-home")).text
    assert '(subpath "/private/var/db/timezone")' in text
    assert '(literal "/private/etc/hosts")' in text
    assert '"/var/db' not in text and '"/etc/' not in text  # Seatbelt matches the real path
    assert '(subpath "/private/var/db")' not in text and '(subpath "/private/etc")' not in text


def _links_out_of(tree, roots):
    """Links under tree whose real target is under none of roots."""
    out = []
    for root, dirs, files in os.walk(tree):
        for name in (*dirs, *files):
            path = Path(root, name)
            target = Path(os.path.realpath(path))
            if path.is_symlink() and not any(target.is_relative_to(r) for r in roots):
                out.append((path, target))
    return out


@mac_only
def test_no_link_in_the_time_zone_data_leaves_the_read_roots():
    # Some runner images link zoneinfo to /usr/share/zoneinfo.default (#886): fine while the
    # target stays under a root the policy can read.
    if not TZ_DATA.is_dir():
        pytest.skip(f"{TZ_DATA} missing: macOS moved its time zone data; update seatbelt.py")
    assert _links_out_of(TZ_DATA, READ_ROOTS) == []


def test_the_link_check_flags_a_target_outside_every_root(tmp_path):
    tree, inside, outside = tmp_path / "tz", tmp_path / "share", tmp_path / "etc"
    for d in (tree, inside, outside):
        d.mkdir()
    (tree / "zoneinfo").symlink_to(inside)
    (tree / "hosts").symlink_to(outside)
    roots = (tree.resolve(), inside.resolve())
    assert _links_out_of(tree, roots) == [(tree / "hosts", outside.resolve())]


def _host_tzname():
    env = {k: v for k, v in os.environ.items() if k != "TZ"}
    code = "import time; print(repr(time.tzname))"
    return subprocess.run(
        [sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


@mac_only
@pytest.mark.sandbox
def test_time_zone_data_is_readable(repo, tmp_path):
    code = (
        "import datetime, os, time, zoneinfo\n"
        "def test_tz():\n"
        f"    assert time.tzname == {_host_tzname()}  # TZ unset: /etc/localtime\n"
        "    ny = zoneinfo.ZoneInfo('America/New_York')\n"
        "    assert ny.utcoffset(datetime.datetime(2026, 1, 1)) == datetime.timedelta(hours=-5)\n"
        "    os.environ['TZ'] = 'Asia/Taipei'\n"
        "    time.tzset()\n"
        "    assert time.localtime().tm_zone == 'CST' and time.timezone == -8 * 3600\n"
    )
    _passed(_verify(repo, tmp_path, code))


@mac_only
@pytest.mark.sandbox
def test_the_zoneinfo_default_link_target_is_readable(repo, tmp_path):
    target = Path("/usr/share/zoneinfo.default/America/New_York")  # a runner's zoneinfo (#886)
    if not target.is_file():
        pytest.skip(f"{target} missing on this host")
    code = f"def test_read():\n    assert open({str(target)!r}, 'rb').read(4) == b'TZif'\n"
    _passed(_verify(repo, tmp_path, code))


@mac_only
@pytest.mark.sandbox
def test_localhost_resolves_and_nothing_else_does(repo, tmp_path):
    code = (
        "import socket, time\n"
        "import pytest\n"
        "def test_hosts():\n"
        "    assert socket.gethostbyname('localhost') == '127.0.0.1'\n"
        "    assert '::1' in [a[4][0] for a in socket.getaddrinfo('localhost', 80)]\n"
        "    start = time.monotonic()\n"
        "    with pytest.raises(socket.gaierror):\n"
        "        socket.getaddrinfo('cairnwork-not-in-hosts.example', 80)\n"
        "    assert time.monotonic() - start < 5\n"
    )
    _passed(_verify(repo, tmp_path, code))


@mac_only
@pytest.mark.sandbox
def test_getuser_is_the_fixed_name_whatever_the_parent_says(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "evil")
    monkeypatch.setenv("LOGNAME", "evil")
    monkeypatch.setattr(verifiers, "_ENV_KEEP", (*verifiers._ENV_KEEP, "USER", "LOGNAME"))
    code = (
        "import getpass, os\n"
        "def test_user(tmp_path):\n"
        "    assert getpass.getuser() == 'cairnwork'\n"
        "    assert os.environ['USER'] == os.environ['LOGNAME'] == 'cairnwork'\n"
        "    assert tmp_path.parent.parent.name == 'pytest-of-cairnwork'\n"
    )
    _passed(_verify(repo, tmp_path, code))


def _readable_db_file():
    for path in sorted(Path("/private/var/db").iterdir()):
        try:
            if path.is_file() and not path.is_symlink() and path.read_bytes():
                return str(path)
        except OSError:
            continue
    pytest.skip("no readable file in /private/var/db on this host")


@mac_only
@pytest.mark.sandbox
def test_nothing_else_opened(repo, tmp_path):
    db_file = _readable_db_file()
    assert Path("/private/etc/shells").read_bytes()  # readable outside, so a real control
    code = (
        "import os, socket\n"
        "import pytest\n"
        "def test_closed(tmp_path):\n"
        f"    for path in ('/private/etc/shells', {db_file!r}):\n"
        "        with pytest.raises(PermissionError):\n"
        "            open(path, 'rb').close()\n"
        "    for path in ('/private/etc/hosts', '/private/var/db/timezone/cw802'):\n"
        "        with pytest.raises(OSError):\n"
        "            open(path, 'ab').close()\n"
        "    os.chdir(tmp_path)\n"
        "    with socket.socket(socket.AF_UNIX) as s, pytest.raises(PermissionError):\n"
        "        s.bind('s')  # a short path: refused by the sandbox, not by length\n"
    )
    _passed(_verify(repo, tmp_path, code))
