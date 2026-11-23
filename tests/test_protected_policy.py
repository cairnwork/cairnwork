"""#1585 step 2: one resolved protected-path policy; any doubt means the full default list.

PR-A only: no waiver is allowed yet (WAIVABLE is empty), so every policy file is rejected and the
resolved list equals PROTECTED_PATHS. The injected ``waivable`` set proves the loader does read a
good file, so "ignored" in the negative cases is not just "always ignored"."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_protected_paths import _attempt, _commit, issue_verdict, submit_verdict

from cairnwork import protected_paths
from cairnwork.protected_paths import PROTECTED_PATHS, is_protected, path_refusal
from cairnwork.protected_policy import WAIVABLE, ResolvedPolicy, resolve
from cairnwork.workspace import CandidateRejected, collect_candidate

ENTRY = "MUSE_PROTO.md"
GOOD = {"version": 1, "repos": {"acme/app": {"waive": [ENTRY]}}}
UNIQUE = "zq_policy_marker_1585"


def _write(directory: Path, doc, mode=0o600) -> Path:
    directory.mkdir(exist_ok=True)
    directory.chmod(0o700)
    path = directory / "policy.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    path.chmod(mode)
    return path


def _res(path, waivable=frozenset({ENTRY})):
    return resolve(PROTECTED_PATHS, None if path is None else str(path), waivable=waivable)


def test_waivable_is_empty_so_a_real_policy_changes_nothing(tmp_path):
    assert WAIVABLE == frozenset()
    got = resolve(PROTECTED_PATHS, str(_write(tmp_path / "d", GOOD)))
    assert got.source == "default" and got.waived == {} and got.paths == PROTECTED_PATHS


def test_a_good_operator_file_is_read(tmp_path):
    got = _res(_write(tmp_path / "d", GOOD))
    assert got.source.endswith("policy.json") and got.waived == {"acme/app": frozenset({ENTRY})}
    assert ENTRY not in got.paths_for("acme/app") and ENTRY in got.paths_for("other/app")
    assert got.paths_for(None) == PROTECTED_PATHS  # no repo named: the full list


@pytest.mark.parametrize("env", [None, "", "relative/policy.json", "/nonexistent/policy.json"])
def test_no_or_missing_file_is_the_default(env):
    got = resolve(PROTECTED_PATHS, env, waivable=frozenset({ENTRY}))
    assert got.source == "default" and got.paths_for("acme/app") == PROTECTED_PATHS


@pytest.mark.parametrize(
    "doc",
    [
        "{not json",
        "[]",
        {"version": 2, "repos": {}},
        {"version": 1},
        {"version": 1, "repos": {}, "x": 1},
        {"version": 1, "repos": {"r": {"waive": [ENTRY], "x": 1}}},
        {"version": 1, "repos": {"r": {"waive": [7]}}},
        {"version": 1, "repos": {"r": {"waive": "MUSE_PROTO.md"}}},
        {"version": 1, "repos": {"r": {"waive": ["pyproject.toml"]}}},  # packaging
        {"version": 1, "repos": {"r": {"waive": ["src/cairnwork*"]}}},  # Cairnwork's own
        {"version": 1, "repos": {"r": {"waive": [".github"]}}},  # CI
        {"version": 1, "repos": {"r": {"waive": ["conftest.py"]}}},
        {"version": 1, "repos": {"r": {"waive": [ENTRY, "pyproject.toml"]}}},  # one bad: all
        "[" * 100000,
    ],
)
def test_a_malformed_or_forbidden_file_keeps_the_full_list(tmp_path, doc):
    got = _res(_write(tmp_path / "d", doc))
    assert got.source == "default" and got.waived == {}


def test_a_group_or_other_writable_file_or_directory_is_ignored(tmp_path):
    assert _res(_write(tmp_path / "a", GOOD, mode=0o620)).source == "default"
    assert _res(_write(tmp_path / "b", GOOD, mode=0o602)).source == "default"
    loose = _write(tmp_path / "c", GOOD)
    loose.parent.chmod(0o770)
    assert _res(loose).source == "default"


def test_a_symlink_and_a_directory_are_ignored(tmp_path):
    real = _write(tmp_path / "d", GOOD)
    link = tmp_path / "d" / "link.json"
    link.symlink_to(real)
    assert _res(link).source == "default"
    assert _res(tmp_path / "d").source == "default"


def test_a_file_inside_a_git_checkout_is_ignored(tmp_path):
    checkout = tmp_path / "candidate"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    in_checkout = _write(checkout / "conf", GOOD)  # a candidate commit could add exactly this
    assert _res(in_checkout).source == "default"
    assert _res(_write(tmp_path / "outside", GOOD)).source != "default"  # same bytes, elsewhere


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to make a file someone else owns")
def test_a_file_owned_by_another_user_is_ignored(tmp_path):
    path = _write(tmp_path / "d", GOOD)
    os.chown(path, 12345, -1)
    assert _res(path).source == "default"


def test_the_default_list_is_unchanged_for_every_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(protected_paths, "_policy", None)
    monkeypatch.setenv("CAIRNWORK_PROTECTED_POLICY_FILE", str(_write(tmp_path / "d", GOOD)))
    for entry in PROTECTED_PATHS:
        probe = entry.rstrip("*") + ("x" if entry.endswith("*") else "/y.py")
        assert is_protected(probe), entry
    assert is_protected(ENTRY) and path_refusal(ENTRY)  # not waived: a bad policy is ignored


# --- AC6: every enforcement site reads the one resolved policy ---------------------------


def test_all_four_enforcement_sites_follow_the_one_resolved_policy(monkeypatch, repo, tmp_path):
    marker = f"src/{UNIQUE}.py"
    assert not is_protected(marker) and issue_verdict(marker) is None
    only_marker = ResolvedPolicy((f"src/{UNIQUE}*",), {}, "test")
    monkeypatch.setattr(protected_paths, "_policy", only_marker)
    assert is_protected(marker) and not is_protected("pyproject.toml")  # the replaced list
    assert issue_verdict(marker) == "TASK_ALLOWED_FILES_PROTECTED"  # Issue intake
    assert submit_verdict(marker) == "TASK_ALLOWED_FILES_PROTECTED"  # check_task
    assert issue_verdict("scripts/check_size.py") is None  # only the one list is consulted
    attempt, clone = _attempt(repo, tmp_path)
    (clone / "src" / f"{UNIQUE}.py").write_text("x = 1\n")
    _commit(clone)
    with pytest.raises(CandidateRejected) as info:  # check_candidate_commit
        collect_candidate(repo, attempt, (marker,), python=sys.executable)
    assert info.value.code == "PROTECTED_PATH"


def test_the_real_entry_reads_the_environment_once_per_process(tmp_path):
    """Real entry (Issue intake) in a fresh interpreter: a candidate-supplied file is ignored."""
    candidate = tmp_path / "candidate"
    (candidate / ".git").mkdir(parents=True)
    bad = _write(candidate / "conf", GOOD)
    code = (
        "from cairnwork.protected_paths import active_policy, is_protected\n"
        "p = active_policy()\n"
        "print(p.source, is_protected('MUSE_PROTO.md'), p is active_policy())\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "CAIRNWORK_PROTECTED_POLICY_FILE": str(bad)},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.split()
    assert out == ["default", "True", "True"]


# --- a symlinked parent directory gets no trust of its own --------------------------------


def _checkout_conf(tmp_path) -> Path:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    return _write(checkout / "conf", GOOD).parent


def _mode_dir(tmp_path, name, mode) -> Path:
    real = _write(tmp_path / name, GOOD).parent
    real.chmod(mode)
    return real


def _via_link(tmp_path, target: Path) -> Path:
    op = tmp_path / "op"
    op.mkdir(exist_ok=True)
    (op / "link").symlink_to(target)
    return op / "link" / "policy.json"


def _symlinked_file(tmp_path) -> Path:
    real = _write(tmp_path / "real", GOOD)
    link = real.parent / "link.json"
    link.symlink_to(real)
    return link


@pytest.mark.parametrize(
    "build",
    [
        lambda t: _via_link(t, _checkout_conf(t)),  # link -> dir inside a checkout
        lambda t: _via_link(t, _mode_dir(t, "wide", 0o777)),
        lambda t: _via_link(t, _mode_dir(t, "group", 0o770)),
        lambda t: _via_link(t, _checkout_conf(t).parent / "conf"),  # same dir, other spelling
        lambda t: _symlinked_file(t),
    ],
    ids=["into-checkout", "0777", "0770", "checkout-conf", "symlinked-file"],
)
def test_a_symlinked_parent_directory_gets_no_trust(tmp_path, build):
    assert _res(build(tmp_path)).source == "default"


def test_a_symlinked_parent_directory_for_a_checkout_root_gets_no_trust(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    _write(checkout, GOOD)
    assert _res(_via_link(tmp_path, checkout)).source == "default"


def test_a_real_good_directory_is_read_even_through_an_aliased_ancestor(tmp_path):
    real = _write(tmp_path / "real" / "d", GOOD) if (tmp_path / "real").mkdir() is None else None
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real")  # only a higher ancestor is a symlink (macOS /tmp)
    assert real is not None
    assert _res(real).source != "default"
    assert _res(alias / "d" / "policy.json").source != "default"


def test_an_ancestor_swapped_after_the_check_is_not_followed(tmp_path, monkeypatch):
    """Swap a middle directory for a symlink to another directory holding the same relative file
    just before the first open: the held-handle walk must refuse it."""
    op = tmp_path / "op"
    (op / "anc").mkdir(parents=True)
    good = _write(op / "anc" / "d", GOOD)
    (tmp_path / "evil").mkdir()
    _write(tmp_path / "evil" / "d", GOOD)
    real_open = os.open
    swapped: list[bool] = []

    def swap_before_open(*args, **kw):
        if not swapped:
            swapped.append(True)
            (op / "anc").rename(op / "anc_moved")
            (op / "anc").symlink_to(tmp_path / "evil")
        return real_open(*args, **kw)

    monkeypatch.setattr(os, "open", swap_before_open)
    assert _res(good).source == "default"
    assert swapped
    monkeypatch.undo()
    assert _res(_write(tmp_path / "ok", GOOD)).source != "default"  # control: no swap, read
