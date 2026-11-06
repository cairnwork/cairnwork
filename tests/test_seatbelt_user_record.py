"""#527: the Seatbelt profile allows the read-only user-record lookup, only on a local-only host."""

import subprocess
import sys
from pathlib import Path

import pytest
from test_seatbelt_env_802 import _passed, _verify

from cairnwork_sandbox import seatbelt

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS sandbox-exec")
LOCAL = "SearchPath: /Local/Default\nCSPSearchPath: /Local/Default\n"


def _runner(monkeypatch, out=None, code=0, error=None):
    def run(argv, **kw):
        assert argv == ["/usr/bin/dscl", "/Search", "-read", "/"] and kw["timeout"] == 2
        if error:
            raise error
        return subprocess.CompletedProcess(argv, code, out, "")

    monkeypatch.setattr(seatbelt.subprocess, "run", run)


@pytest.mark.parametrize(
    ("out", "code", "error", "want"),
    [
        (LOCAL, 0, None, True),
        ("SearchPath: /Local/Default /LDAPv3/x\nCSPSearchPath: /Local/Default\n", 0, None, False),
        ("SearchPath: /Local/Default\nCSPSearchPath: /Active Directory/CORP\n", 0, None, False),
        ("SearchPath: /Local/Default\n", 0, None, False),
        (LOCAL + "SearchPath: /Local/Default\n", 0, None, False),
        (LOCAL, 1, None, False),
        (None, 0, subprocess.TimeoutExpired("dscl", 2), False),
        (None, 0, OSError("no dscl"), False),
    ],
)
def test_local_directory_only(monkeypatch, out, code, error, want):
    _runner(monkeypatch, out, code, error)
    assert seatbelt._local_directory_only() is want


@pytest.mark.parametrize("allow_ipc", [True, False])
@pytest.mark.parametrize("local", [True, False])
def test_the_rule_is_in_the_profile_only_when_the_guard_says_so(monkeypatch, allow_ipc, local):
    monkeypatch.setattr(seatbelt, "_local_directory_only", lambda: local)
    profile = seatbelt.build_profile((), (), home=Path("/nonexistent-home"), allow_ipc=allow_ipc)
    assert (seatbelt.LIBINFO_RULE in profile.text) is local
    assert (seatbelt.LIBINFO_NOTE in profile.notes) is (not local)
    if local:  # before the network deny, which comes last of the allow rules
        assert profile.text.index(seatbelt.LIBINFO_RULE) < profile.text.index("(deny network*)")


@mac_only
@pytest.mark.sandbox
def test_the_user_record_resolves_but_dns_stays_closed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(seatbelt, "_local_directory_only", lambda: True)
    code = (
        "import os, pwd, socket\n"
        "import pytest\n"
        "def test_lookup():\n"
        "    assert pwd.getpwuid(os.getuid()).pw_dir\n"
        "    with pytest.raises(socket.gaierror):\n"
        "        socket.getaddrinfo('example.com', 80)\n"
    )
    _passed(_verify(repo, tmp_path, code))
