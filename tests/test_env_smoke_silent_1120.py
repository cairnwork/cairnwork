"""git read failures that still exit 0/1 must refuse the mirror check, and benign repos must
still pass (Issue #1120). Real git, real files; only the stderr cases patch one git call."""

import os
import subprocess

import pytest

from scripts import env_smoke
from scripts.env_smoke import _mirror_multi_branch_reason

_REAL_RUN = subprocess.run
_REFSPEC = "+refs/heads/main:refs/remotes/origin/main"
needs_user = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


def _g(repo, *args):
    _REAL_RUN(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return path


@pytest.fixture
def mirror(repo, home):
    _g(repo, "config", "remote.origin.fetch", _REFSPEC)
    return repo


def _hide_dir(path):
    path.chmod(0)


@pytest.fixture(autouse=True)
def _restore_modes(tmp_path):
    yield
    for p in [tmp_path, *tmp_path.rglob("*")]:
        if p.is_dir() and not p.is_symlink():
            p.chmod(0o755)
        elif p.exists() and not p.is_symlink():
            p.chmod(0o644)


@needs_user
@pytest.mark.parametrize(
    "hidden",
    ["refs/remotes/origin", "refs/remotes"],
)
def test_unreadable_remote_ref_dir_hiding_a_branch_is_refused(mirror, hidden):
    _g(mirror, "update-ref", "refs/remotes/origin/other", "HEAD")
    _hide_dir(mirror / ".git" / hidden)
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_unreadable_nested_branch_dir_is_refused(mirror):
    _g(mirror, "update-ref", "refs/heads/feat/x", "HEAD")
    _hide_dir(mirror / ".git" / "refs" / "heads" / "feat")
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_unreadable_refs_heads_is_refused(mirror):
    _hide_dir(mirror / ".git" / "refs" / "heads")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_corrupt_loose_ref_hiding_a_branch_is_refused(mirror):
    (mirror / ".git" / "refs" / "heads" / "hid").write_text("not a sha\n")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_missing_relative_include_in_repo_config_is_refused(mirror):
    _g(mirror, "config", "include.path", "absent.cfg")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_missing_absolute_include_is_refused(mirror, tmp_path):
    _g(mirror, "config", "include.path", str(tmp_path / "nope.cfg"))
    assert _mirror_multi_branch_reason(mirror) is not None


def test_global_include_with_missing_target_is_refused(mirror, home):
    (home / ".gitconfig").write_text("[include]\n\tpath = ~/gone.cfg\n")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_matching_includeif_with_missing_target_is_refused(mirror):
    _g(mirror, "config", f"includeIf.gitdir:{mirror}/.path", "/nonexistent/x.cfg")
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_unreadable_global_config_is_refused(mirror, home):
    (home / ".gitconfig").write_text("[user]\n\tname = x\n")
    (home / ".gitconfig").chmod(0)
    assert _mirror_multi_branch_reason(mirror) is not None


@pytest.mark.parametrize("code", [0, 1])
def test_stderr_on_the_refspec_read_alone_is_refused(mirror, monkeypatch, code):
    def fake_run(args, **kwargs):
        got = _REAL_RUN(args, **kwargs)
        if "--get-all" in args:
            got = subprocess.CompletedProcess(args, code, got.stdout, "warning: x")
        return got

    monkeypatch.setattr(env_smoke.subprocess, "run", fake_run)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "reading remote.origin.fetch" in reason


def test_stderr_on_for_each_ref_alone_is_refused(mirror, monkeypatch):
    def fake_run(args, **kwargs):
        got = _REAL_RUN(args, **kwargs)
        if "for-each-ref" in args:
            got = subprocess.CompletedProcess(args, 0, got.stdout, "warning: x")
        return got

    monkeypatch.setattr(env_smoke.subprocess, "run", fake_run)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "listing refs" in reason


# Benign repos main accepts must still pass.
def test_linked_worktree_of_a_single_branch_repo_passes(mirror, tmp_path):
    linked = tmp_path / "linked"
    _g(mirror, "worktree", "add", "-q", "--detach", str(linked))
    assert _mirror_multi_branch_reason(linked) is None


def test_reftable_single_branch_repo_passes(home, tmp_path):
    path = tmp_path / "rt"
    made = _REAL_RUN(["git", "init", "-q", "--ref-format=reftable", str(path)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this git has no reftable")
    _g(path, "config", "remote.origin.fetch", _REFSPEC)
    assert _mirror_multi_branch_reason(path) is None


def test_present_relative_include_in_repo_config_passes(mirror):
    (mirror / ".git" / "extra.cfg").write_text("[user]\n\tname = y\n")
    _g(mirror, "config", "include.path", "extra.cfg")
    assert _mirror_multi_branch_reason(mirror) is None


def test_non_matching_includeif_with_missing_target_passes(mirror, tmp_path):
    other = tmp_path / "elsewhere"
    _g(mirror, "config", f"includeIf.gitdir:{other}/.path", "/nonexistent/x.cfg")
    assert _mirror_multi_branch_reason(mirror) is None


def test_packed_refs_single_branch_passes(mirror):
    _g(mirror, "pack-refs", "--all")
    assert _mirror_multi_branch_reason(mirror) is None


def test_unset_home_passes(mirror, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    assert _mirror_multi_branch_reason(mirror) is None


def test_present_global_relative_include_passes(mirror, home):
    (home / "extra.cfg").write_text("[user]\n\tname = z\n")
    (home / ".gitconfig").write_text("[include]\n\tpath = extra.cfg\n")
    assert _mirror_multi_branch_reason(mirror) is None


# Round-2 review of #1208: log-line injection through a directory name, hasconfig includes.
@needs_user
@pytest.mark.parametrize(
    ("parent", "name"),
    [("refs/heads", "a\n::error::x"), ("refs/remotes/origin", "p\n##[error]y")],
)
def test_hostile_dir_name_never_reaches_a_log_line_raw(mirror, parent, name):
    (mirror / ".git" / parent / name).mkdir(parents=True)
    _hide_dir(mirror / ".git" / parent / name)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "\n" not in reason
    assert not any(line.startswith(("::", "##[")) for line in reason.splitlines())


def test_matching_hasconfig_include_with_missing_target_is_refused(mirror):
    _g(mirror, "config", "remote.origin.url", "https://x/y.git")
    _g(mirror, "config", "includeIf.hasconfig:remote.*.url:https://x/**.path", "/nonexistent/x.cfg")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_dangling_global_gitconfig_symlink_passes(mirror, home):
    (home / ".gitconfig").symlink_to(home / "no-such-target")
    assert _mirror_multi_branch_reason(mirror) is None


# Round-1 cross-model review of #1208: checks the first tests left unpinned (mutants survived).
@needs_user
def test_unreadable_xdg_config_is_refused(mirror, home, monkeypatch):
    xdg = home / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text("[user]\n\tname = x\n")
    (xdg / "git" / "config").chmod(0)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_hostile_global_config_path_never_reaches_a_log_line_raw(mirror, home, monkeypatch):
    xdg = home / "x\n::error::y"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text("[user]\n\tname = x\n")
    (xdg / "git" / "config").chmod(0)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "\n" not in reason


def _only(monkeypatch, flag, code, stderr=""):
    def fake_run(args, **kwargs):
        got = _REAL_RUN(args, **kwargs)
        if flag in args:
            got = subprocess.CompletedProcess(args, code, "", stderr)
        return got

    monkeypatch.setattr(env_smoke.subprocess, "run", fake_run)


@pytest.mark.parametrize("code", [2, 128])
def test_refspec_read_exit_above_one_is_refused_by_its_own_check(mirror, monkeypatch, code):
    _only(monkeypatch, "--get-all", code)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert f"could not read remote.origin.fetch (git exit {code})" in reason


@pytest.mark.parametrize(("code", "stderr"), [(128, ""), (0, "warning: x"), (1, "warning: x")])
def test_include_listing_failure_is_refused(mirror, monkeypatch, code, stderr):
    _only(monkeypatch, "--get-regexp", code, stderr)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "could not list config includes" in reason


@pytest.mark.parametrize(("code", "stderr"), [(128, ""), (0, "warning: x")])
def test_git_dir_lookup_failure_is_refused(mirror, monkeypatch, code, stderr):
    _only(monkeypatch, "--git-common-dir", code, stderr)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "could not locate the git dir" in reason


# Round-1 Codex review of #1208: the whole family of "a config file git reads is unreadable".
def _cfg(path, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[user]\n\tname = x\n")
    if mode is not None:
        path.chmod(mode)
    return path


@needs_user
def test_unreadable_git_config_global_file_is_refused(mirror, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_cfg(tmp_path / "g" / "cfg", 0)))
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_unsearchable_parent_of_an_existing_global_config_is_refused(mirror, home, monkeypatch):
    xdg = home / "xdg"
    _cfg(xdg / "git" / "config")
    (xdg / "git").chmod(0)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_unreadable_system_config_is_refused(mirror, tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(_cfg(tmp_path / "s" / "cfg", 0)))
    assert _mirror_multi_branch_reason(mirror) is not None


@needs_user
def test_global_config_git_does_not_read_is_not_refused(mirror, home, tmp_path, monkeypatch):
    _cfg(home / ".gitconfig", 0)  # ignored by git once GIT_CONFIG_GLOBAL is set
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_cfg(tmp_path / "ok" / "cfg")))
    assert _mirror_multi_branch_reason(mirror) is None


def test_missing_git_config_global_file_passes(mirror, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent" / "cfg"))
    assert _mirror_multi_branch_reason(mirror) is None


# Round-2 Codex review of #1208: includes supplied on the command line (GIT_CONFIG_COUNT).
def _env_include(monkeypatch, value):
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(n + 1))
    monkeypatch.setenv(f"GIT_CONFIG_KEY_{n}", "include.path")
    monkeypatch.setenv(f"GIT_CONFIG_VALUE_{n}", value)


def test_missing_include_given_on_the_command_line_is_refused(mirror, tmp_path, monkeypatch):
    _env_include(monkeypatch, str(tmp_path / "absent.cfg"))
    assert _mirror_multi_branch_reason(mirror) is not None


def test_present_include_given_on_the_command_line_passes(mirror, tmp_path, monkeypatch):
    _env_include(monkeypatch, str(_cfg(tmp_path / "here.cfg")))
    assert _mirror_multi_branch_reason(mirror) is None


# Round-3 Claude full review of #1208: a relative XDG_CONFIG_HOME (git resolves it against the
# repo because of -C), a hostile include value, and a "~/" include.
@needs_user
@pytest.mark.parametrize("hide", ["xr/git/config", "xr/git"])
def test_unreadable_config_under_a_relative_xdg_config_home_is_refused(mirror, monkeypatch, hide):
    _cfg(mirror / "xr" / "git" / "config")
    (mirror / hide).chmod(0)
    monkeypatch.setenv("XDG_CONFIG_HOME", "xr")
    assert _mirror_multi_branch_reason(mirror) is not None


@pytest.mark.parametrize("present", [True, False])
def test_readable_or_absent_config_under_a_relative_xdg_config_home_passes(
    mirror, monkeypatch, present
):
    if present:
        _cfg(mirror / "xr" / "git" / "config")
    monkeypatch.setenv("XDG_CONFIG_HOME", "xr")
    assert _mirror_multi_branch_reason(mirror) is None


@needs_user
def test_default_xdg_config_git_does_not_read_is_not_refused(mirror, home, monkeypatch):
    _cfg(home / ".config" / "git" / "config", 0)  # git reads xr/ instead of ~/.config
    monkeypatch.setenv("XDG_CONFIG_HOME", "xr")
    assert _mirror_multi_branch_reason(mirror) is None


@needs_user
def test_empty_xdg_config_home_still_means_the_default_location(mirror, home, monkeypatch):
    _cfg(home / ".config" / "git" / "config", 0)
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert _mirror_multi_branch_reason(mirror) is not None


def test_hostile_include_value_never_reaches_a_log_line_raw(mirror):
    _g(mirror, "config", "include.path", "/absent/x\n::error::pwn")
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "\n" not in reason
    assert not reason.startswith(("::", "##["))


def test_present_tilde_include_passes(mirror, home):
    (home / "ok.cfg").write_text("[user]\n\tname = y\n")
    _g(mirror, "config", "include.path", "~/ok.cfg")
    assert _mirror_multi_branch_reason(mirror) is None


@pytest.mark.parametrize(("code", "stderr", "stdout"), [(128, "", None), (0, "warning: x", None)])
def test_git_dir_lookup_failure_alone_is_refused_with_a_real_path(
    mirror, monkeypatch, code, stderr, stdout
):
    def fake_run(args, **kwargs):
        got = _REAL_RUN(args, **kwargs)
        if "--git-common-dir" in args:
            got = subprocess.CompletedProcess(args, code, got.stdout, stderr)
        return got

    monkeypatch.setattr(env_smoke.subprocess, "run", fake_run)
    reason = _mirror_multi_branch_reason(mirror)
    assert reason is not None
    assert "could not locate the git dir" in reason


def test_git_dir_lookup_with_no_output_is_refused(mirror, monkeypatch):
    def fake_run(args, **kwargs):
        got = _REAL_RUN(args, **kwargs)
        if "--git-common-dir" in args:
            got = subprocess.CompletedProcess(args, 0, "", "")
        return got

    monkeypatch.setattr(env_smoke.subprocess, "run", fake_run)
    assert _mirror_multi_branch_reason(mirror) is not None
