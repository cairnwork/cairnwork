"""Top-directory preflight failures must be precise and safe."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from run_scrub_unlock_harness import walk_source

import cairnwork.run_scrub as run_scrub


def _lines(result) -> list[str]:
    return result.stderr.splitlines()


@pytest.mark.parametrize("mode", [0o000, 0o400, 0o500, 0o600, 0o700])
def test_top_without_permissions_is_fixed_before_walking(tmp_path, mode):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    os.chmod(top, mode)
    try:
        result = walk_source(top, (2, 45))
        assert result.returncode == 1 and "limit reached" in result.stderr, result
        assert stat.S_IMODE(os.lstat(top).st_mode) == 0o700
    finally:
        os.chmod(top, 0o700)


def test_missing_top_has_accurate_failure(tmp_path):
    top = tmp_path / "missing"
    result = walk_source(top, (10, 45))
    assert result.returncode != 0
    assert "not writable by the account" not in result.stderr
    assert "limit reached" not in result.stderr
    assert "does not exist" in result.stderr


def test_lstat_failure_has_accurate_failure(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    preamble = f"""\
real_lstat=os.lstat
def broken_lstat(path, *args, **kwargs):
    if os.fspath(path) == {str(top)!r}: raise PermissionError('injected lstat failure')
    return real_lstat(path, *args, **kwargs)
os.lstat=broken_lstat
"""
    result = walk_source(top, (10, 45), preamble)
    assert result.returncode != 0
    assert "not writable by the account" not in result.stderr
    assert "limit reached" not in result.stderr
    assert "could not inspect top" in result.stderr


def test_lchflags_failure_has_accurate_failure(tmp_path):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    preamble = f"""\
import os, stat
real_lstat=os.lstat; seen=[]
def flagged_lstat(path, *args, **kwargs):
    info=real_lstat(path, *args, **kwargs)
    if os.fspath(path) == {str(top)!r} and not seen: seen.append(1); info.st_flags=1
    return info
os.lstat=flagged_lstat
os.lchflags=lambda *args: (_ for _ in ()).throw(PermissionError('injected lchflags failure'))
stat.UF_IMMUTABLE=1
stat.UF_APPEND=2
"""
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode == 1 and "limit reached" in result.stderr, result
    assert "not writable by the account" not in result.stderr


def test_really_unwritable_top_stops_before_walker(tmp_path):
    top = tmp_path / "top"
    (top / "child").mkdir(parents=True)
    denied = (
        f"real_chmod=os.chmod; real_access=os.access; "
        f"os.chmod=lambda p,m,**k: None if os.fspath(p)=={str(top)!r} "
        f"else real_chmod(p,m,**k); "
        f"os.access=lambda p,m,**k: False if os.fspath(p)=={str(top)!r} "
        "else real_access(p,m,**k)"
    )
    result = walk_source(top, (2, 45), denied)
    assert result.returncode != 0
    assert "not writable by the account" in result.stderr
    assert "limit reached" not in result.stderr
    assert not list(top.glob(".unlock-*"))


def test_control_characters_in_reported_paths_are_neutralized(tmp_path):
    top = tmp_path / "top\n::warning::notice\n##[group]heading"
    top.mkdir()
    denied = (
        f"real_chmod=os.chmod; real_access=os.access; "
        f"os.chmod=lambda p,m,**k: None if os.fspath(p)=={str(top)!r} "
        f"else real_chmod(p,m,**k); "
        f"os.access=lambda p,m,**k: False if os.fspath(p)=={str(top)!r} "
        "else real_access(p,m,**k)"
    )
    result = walk_source(top, (2, 45), denied)
    assert result.returncode != 0
    assert not any(line.startswith("::warning::") for line in _lines(result))
    assert not any(line.startswith("##[group]") for line in _lines(result))


def test_control_characters_in_recovery_commands_are_neutralized(tmp_path):
    top = tmp_path / "top\n::warning::notice\n##[group]heading"
    message = run_scrub._hint("verifier", sys.executable, top)
    assert not any(line.startswith("::warning::") for line in message.splitlines())
    assert not any(line.startswith("##[group]") for line in message.splitlines())


def _preflight_preamble(top: os.PathLike[str], *lines: str) -> str:
    return "\n".join((f"TOP={str(top)!r}", *lines))


@pytest.mark.parametrize("step", ["chmod", "acl", "flags"])
def test_top_unlock_step_failure_is_best_effort_when_access_is_true(tmp_path, step):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    os.chmod(top, 0o700)
    if step == "flags":
        preamble = _preflight_preamble(
            top,
            "stat.UF_IMMUTABLE=1; stat.UF_APPEND=2",
            "real_lstat=os.lstat; SEEN=[]",
            """def flagged(path, *a, **k):
    info = real_lstat(path, *a, **k)
    if os.fspath(path) == TOP and not SEEN:
        SEEN.append(1)
        info.st_flags = 1
    return info""",
            "os.lstat=flagged",
            "os.lchflags=lambda *a: (_ for _ in ()).throw(PermissionError('lchflags'))",
        )
    elif step == "acl":
        preamble = _preflight_preamble(
            top,
            "del os.lchflags",
            "fake_lib.setattrlist=lambda *a: -1",
        )
    else:
        preamble = _preflight_preamble(
            top,
            "real_chmod=os.chmod",
            """def denied_chmod(path, mode, **kwargs):
    if os.fspath(path) == TOP:
        raise PermissionError('chmod')
    return real_chmod(path, mode, **kwargs)
os.chmod = denied_chmod""",
        )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode == 1 and "limit reached" in result.stderr, result
    assert "not writable by the account" not in result.stderr


def test_group_writable_setgid_top_walks_when_account_cannot_chmod_it(tmp_path):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    os.chmod(top, 0o3770)
    preamble = _preflight_preamble(
        top,
        "real_chmod=os.chmod",
        """def denied_chmod(path, mode, **kwargs):
    if os.fspath(path) == TOP:
        raise PermissionError('group-owned')
    return real_chmod(path, mode, **kwargs)
os.chmod = denied_chmod""",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode == 1 and "limit reached" in result.stderr, result


def test_access_false_only_for_search_permission_is_refused(tmp_path):
    top = tmp_path / "top"
    (top / "child").mkdir(parents=True)
    os.chmod(top, 0o600)
    preamble = _preflight_preamble(
        top,
        "real_access=os.access",
        "os.chmod=lambda *a,**k: None",
        """def checked_access(path, mode, **kwargs):
    if os.fspath(path) == TOP:
        return mode != (os.W_OK | os.X_OK)
    return real_access(path, mode, **kwargs)
os.access = checked_access""",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0 and "not writable by the account" in result.stderr
    assert "limit reached" not in result.stderr
    assert not list(top.glob(".unlock-*"))
    os.chmod(top, 0o700)


def test_unwritable_top_names_failed_best_effort_steps(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    preamble = _preflight_preamble(
        top,
        "real_access=os.access",
        """def denied_chmod(path, mode, **kwargs):
    if os.fspath(path) == TOP:
        raise PermissionError('chmod')
os.chmod = denied_chmod""",
        "os.access=lambda p,m,**k: False if os.fspath(p)==TOP else real_access(p,m,**k)",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0
    assert "not writable by the account" in result.stderr
    assert "chmod failed: PermissionError" in result.stderr
    assert "limit reached" not in result.stderr


def test_immutable_top_clears_flags_before_acl_attempt(tmp_path):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    preamble = _preflight_preamble(
        top,
        "flag_state={'value': 1, 'events': []}",
        "stat.UF_IMMUTABLE=1; stat.UF_APPEND=2",
        "real_lstat=os.lstat",
        """def flagged(path, *args, **kwargs):
    info = real_lstat(path, *args, **kwargs)
    if os.fspath(path) == TOP:
        info.st_flags = flag_state['value']
    return info""",
        "os.lstat=flagged",
        """def clear_flags(path, value):
    flag_state['events'].append('flags')
    flag_state['value'] = value""",
        "os.lchflags=clear_flags",
        """def clear_acl(*args):
    flag_state['events'].append('acl')
    return 0 if flag_state['value'] == 0 else -1""",
        "fake_lib.setattrlist=clear_acl",
    )
    result = walk_source(top, (500_000, 45), preamble, "print(flag_state['events'][:2])")
    assert result.returncode == 0, result
    assert result.stdout.strip() == "['flags', 'acl']"
    # The first top operation clears flags, then tries the ACL.
    assert "could not clear top ACL" not in result.stderr


@pytest.mark.parametrize("sample", ["\n", "\r", "\t", "\x1b", "\x7f"])
def test_all_control_bytes_are_escaped_in_unlock_and_recovery_text(tmp_path, sample):
    top = tmp_path / f"top{sample}name"
    top.mkdir()
    preamble = _preflight_preamble(
        top,
        "real_access=os.access",
        "os.chmod=lambda *a,**k: None",
        "os.access=lambda p,m,**k: False if os.fspath(p)==TOP else real_access(p,m,**k)",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0
    escaped_path = str(top).encode("unicode_escape").decode()
    assert escaped_path in result.stderr
    assert len(result.stderr.splitlines()) == 1
    if sample != "\n":
        assert sample not in result.stderr
    hint = run_scrub._hint("verifier", sys.executable, top)
    assert f"\\x{ord(sample):02x}" in hint
    assert sample not in hint


def test_unwritable_flagged_top_reports_flag_failure(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    preamble = _preflight_preamble(
        top,
        "stat.UF_IMMUTABLE=1; stat.UF_APPEND=2",
        "real_lstat=os.lstat",
        """def flagged(path, *args, **kwargs):
    info = real_lstat(path, *args, **kwargs)
    if os.fspath(path) == TOP:
        info.st_flags = 1
    return info""",
        "os.lstat=flagged",
        "os.lchflags=lambda *a: (_ for _ in ()).throw(PermissionError('lchflags'))",
        "real_access=os.access",
        "os.access=lambda p,m,**k: False if os.fspath(p)==TOP else real_access(p,m,**k)",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0
    assert "not writable by the account" in result.stderr
    assert "could not clear top flags" in result.stderr


def test_top_access_and_chmod_are_no_follow(tmp_path):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    preamble = _preflight_preamble(
        top,
        "real_chmod=os.chmod; real_access=os.access",
        """def checked_chmod(path, mode, **kwargs):
    if os.fspath(path) == TOP and kwargs.get('follow_symlinks') is not False:
        raise AssertionError('chmod followed links')
    return real_chmod(path, mode, **kwargs)""",
        """def checked_access(path, mode, **kwargs):
    if os.fspath(path) == TOP and kwargs.get('follow_symlinks') is not False:
        raise AssertionError('access followed links')
    return real_access(path, mode, **kwargs)""",
        "os.chmod=checked_chmod; os.access=checked_access",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode == 1 and "limit reached" in result.stderr, result


@pytest.mark.parametrize("replaced", ["PermissionError", "FileNotFoundError", "symlink", "file"])
def test_top_replaced_after_clearing_locks_is_refused(tmp_path, replaced):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "plain").write_text("x")
    (tmp_path / "link").symlink_to(top, target_is_directory=True)
    preamble = _preflight_preamble(
        top,
        f"REPLACED={replaced!r}; OTHER={str(tmp_path)!r}; DONE=[]",
        "real_chmod=os.chmod; real_lstat=os.lstat",
        """def marking_chmod(path, mode, **kwargs):
    if os.fspath(path) == TOP:
        DONE.append(1)
    return real_chmod(path, mode, **kwargs)""",
        """def replaced_lstat(path, *a, **k):
    if os.fspath(path) != TOP or not DONE:
        return real_lstat(path, *a, **k)
    if REPLACED in ("symlink", "file"):
        return real_lstat(os.path.join(OTHER, "link" if REPLACED == "symlink" else "plain"))
    raise __import__("builtins").__dict__[REPLACED]()""",
        "os.chmod=marking_chmod; os.lstat=replaced_lstat",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0, result
    if replaced in ("symlink", "file"):
        assert "is not a real directory" in result.stderr, result
    else:
        assert f"after clearing locks ({replaced})" in result.stderr, result
    assert "limit reached" not in result.stderr
    assert not list(top.glob(".unlock-*"))


def test_top_with_uncleared_lock_flag_is_refused_even_when_access_is_true(tmp_path):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    preamble = _preflight_preamble(
        top,
        "stat.UF_IMMUTABLE=1; stat.UF_APPEND=2",
        "real_lstat=os.lstat; real_access=os.access",
        """def flagged(path, *a, **k):
    info = real_lstat(path, *a, **k)
    if os.fspath(path) == TOP:
        info.st_flags = 2
    return info""",
        "os.lstat=flagged; os.lchflags=lambda *a: 0",
        "os.access=lambda p,m,**k: True if os.fspath(p)==TOP else real_access(p,m,**k)",
    )
    result = walk_source(top, (2, 45), preamble)
    assert result.returncode != 0, result
    assert "not writable by the account (top lock flags remain)" in result.stderr, result
    assert "limit reached" not in result.stderr
    assert not list(top.glob(".unlock-*"))


def test_symlink_top_is_not_modified_before_refusal(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    preamble = _preflight_preamble(
        link,
        "def forbidden(*a): raise AssertionError('symlink top was modified')",
        "fake_lib.setattrlist=forbidden",
        "os.lchflags=forbidden",
    )
    result = walk_source(link, (2, 45), preamble)
    assert result.returncode != 0 and "not a real directory" in result.stderr
    assert list(target.iterdir()) == []
