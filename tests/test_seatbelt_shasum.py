"""Council action 3, item C1: /usr/bin/shasum (a perl script) is readable, nothing else is.

Seatbelt lets a confined process exec /usr/bin/perl, but perl then has to *read* the script
it was handed. Without a read rule ``shasum`` dies with ``Can't open perl script
"/usr/bin/shasum": Operation not permitted``. The relaxation names exactly two literal
paths; every other script in /usr/bin stays unreadable, and the trailing credential denies
still come last. The behavioural witnesses run the real sandbox through run_verifiers.
"""

import os
import sys
from pathlib import Path

import pytest
from conftest import commit_change

from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from cairnwork_sandbox import seatbelt

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS sandbox-exec")
SHASUM = ("/usr/bin/shasum", "/usr/bin/shasum5.34")
# Other perl/python scripts shipped in /usr/bin: any one of them must stay unreadable.
OTHER_SCRIPTS = ("/usr/bin/crc32", "/usr/bin/corelist", "/usr/bin/cpan", "/usr/bin/binhex.pl")


def _verify(repo, tmp_path, code):
    head = commit_change(repo, {"tests/test_c1.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_c1.py"])
    runs = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    return runs[0]


def test_only_the_two_shasum_literals_are_added():
    profile = seatbelt.build_profile((), (), home=Path("/nonexistent-home"))
    for path in SHASUM:
        assert f'(literal "{path}")' in profile.text
    assert '(subpath "/usr/bin")' not in profile.text
    assert "regex" not in profile.text.replace('(ipc-posix-name-regex #"^/mp-")', "")
    usr_bin = [p for p in seatbelt.SYSTEM_READ_LITERALS if p.startswith("/usr/bin/")]
    assert sorted(usr_bin) == sorted(("/usr/bin/env", *SHASUM))


def test_the_credential_denies_still_come_last():
    profile = seatbelt.build_profile((), (), home=Path("/nonexistent-home"))
    last = profile.text.rstrip().rsplit("(deny file-read* file-write*", 1)
    assert len(last) == 2 and "allow" not in last[1]
    assert profile.text.index('(literal "/usr/bin/shasum")') < len(last[0])


@mac_only
@pytest.mark.sandbox
def test_a_verifier_can_run_shasum(repo, tmp_path):
    present = [p for p in SHASUM if os.path.exists(p)]
    if not present:
        pytest.skip("no /usr/bin/shasum on this host")
    code = (
        "import subprocess\n"
        "def test_shasum(tmp_path):\n"
        "    (tmp_path / 'f').write_bytes(b'abc')\n"
        f"    for tool in {present!r}:\n"
        "        done = subprocess.run([tool, '-a', '256', str(tmp_path / 'f')],\n"
        "                              capture_output=True, text=True)\n"
        "        assert done.returncode == 0, done.stderr\n"
        "        assert done.stdout.startswith('ba7816bf8f01cfea414140de5dae2223'), done.stdout\n"
    )
    result = _verify(repo, tmp_path, code)
    assert result.observation.passed, result.log_path.read_text()


@mac_only
@pytest.mark.sandbox
def test_other_usr_bin_scripts_stay_unreadable(repo, tmp_path):
    others = [p for p in OTHER_SCRIPTS if os.path.isfile(p)]
    assert others, f"none of {OTHER_SCRIPTS} exists: pick another /usr/bin script"
    code = (
        "import subprocess, pytest\n"
        "def test_denied():\n"
        f"    for path in {others!r}:\n"
        "        with pytest.raises(PermissionError):\n"
        "            open(path, 'rb').read()\n"
        "        done = subprocess.run(['/usr/bin/perl', path, '--help'],\n"
        "                              capture_output=True, text=True)\n"
        "        assert done.returncode != 0 and 'not permitted' in done.stderr, done.stderr\n"
    )
    result = _verify(repo, tmp_path, code)
    assert result.observation.passed, result.log_path.read_text()
