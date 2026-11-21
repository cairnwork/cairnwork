"""#437 R4: an account run root must be outside the candidate tree."""

import os
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork.account_prelude import locked_prelude
from cairnwork.run_scrub import stale_run_error


class _Account:
    user = "cairnwork-verifier"
    python = "/usr/bin/python3"

    def __init__(self, run_root):
        self.run_root = run_root


@pytest.mark.parametrize("use_symlink", [False, True])
def test_run_root_inside_candidate_tree_is_refused(tmp_path, use_symlink):
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    run_root = candidate / "runs"
    run_root.mkdir(mode=0o700)
    os.chmod(run_root, 0o700)
    marker = run_root / "preserve"
    marker.write_text("untouched")
    if use_symlink:
        alias = tmp_path / "candidate-link"
        alias.symlink_to(candidate, target_is_directory=True)
        candidate = alias

    message = stale_run_error(_Account(run_root), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message
    assert "move it outside" in message
    assert marker.read_text() == "untouched"


def test_run_root_equal_to_candidate_tree_is_refused(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    marker = candidate / "preserve"
    marker.write_text("untouched")

    message = stale_run_error(_Account(candidate), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message
    assert "move it outside" in message
    assert marker.read_text() == "untouched"


@pytest.mark.parametrize("run_name", ["cand.runs", "candidate-runs"])
def test_run_root_sibling_or_name_prefix_is_not_inside_candidate(tmp_path, run_name):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    run_root = tmp_path / run_name
    run_root.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    os.chmod(run_root, 0o700)

    assert stale_run_error(_Account(run_root), candidate) is None


def test_candidate_parent_is_not_an_inside_run_root(tmp_path):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    parent = tmp_path
    assert stale_run_error(_Account(parent), candidate) is None


def test_symlinked_parent_to_an_outside_run_root_is_allowed(tmp_path):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    alias = tmp_path / "outside-link"
    alias.symlink_to(outside, target_is_directory=True)
    run_root = alias / "runs"
    run_root.mkdir(mode=0o700)

    assert stale_run_error(_Account(run_root), candidate) is None


@pytest.mark.parametrize("symlink_inside_candidate", [False, True])
def test_outside_run_root_is_not_refused_for_symlinks_in_candidate(
    tmp_path, symlink_inside_candidate
):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    if symlink_inside_candidate:
        (candidate / "link").symlink_to(outside, target_is_directory=True)
        run_root = tmp_path / "runs"
        run_root.mkdir(mode=0o700)
    else:
        candidate_link = tmp_path / "candidate-link"
        candidate_link.symlink_to(candidate, target_is_directory=True)
        run_root = outside

    assert stale_run_error(_Account(run_root), candidate) is None


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_unusable_candidate_tree_is_refused(tmp_path, kind):
    run_root = tmp_path / "runs"
    run_root.mkdir(mode=0o700)
    os.chmod(run_root, 0o700)
    candidate = tmp_path / "cand"
    if kind == "file":
        candidate.write_text("not a directory")

    message = stale_run_error(_Account(run_root), candidate)

    assert message is not None
    assert "candidate tree" in message


def test_run_root_through_symlinked_ancestor_into_candidate_is_refused(tmp_path):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    (candidate / "rr").mkdir(mode=0o700)
    os.chmod(candidate / "rr", 0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(candidate, target_is_directory=True)

    message = stale_run_error(_Account(alias / "rr"), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message


def test_locked_prelude_passes_candidate_tree_to_account_scrub(tmp_path):
    class RecordingAccount:
        user = "cairnwork-verifier"

        def __init__(self):
            self.candidate_tree = None

        @contextmanager
        def exclusive(self, _timeout):
            yield

        def available(self, _python):
            return True

        def require_idle(self):
            pass

        def scrub_stale_runs(self, candidate_tree):
            self.candidate_tree = candidate_tree

    account = RecordingAccount()
    candidate = tmp_path / "repo"
    candidate.mkdir()

    with locked_prelude(account, "python3", 1, candidate):
        pass

    assert account.candidate_tree == candidate


@pytest.mark.parametrize(
    ("candidate_name", "run_name"),
    [
        ("cand", "CAND"),
        ("cand", "Cand"),
        ("cand", "cAnD"),
        ("CAND", "cand"),
        ("Cand", "cand"),
        ("cAnD", "cand"),
        ("café", unicodedata.normalize("NFD", "café")),
        (unicodedata.normalize("NFD", "café"), "café"),
    ],
)
def test_filesystem_aliases_of_candidate_are_refused(
    tmp_path, monkeypatch, candidate_name, run_name
):
    candidate = tmp_path / candidate_name
    candidate.mkdir()
    run_alias = tmp_path / run_name
    try:
        same_tree = os.path.samefile(candidate, run_alias)
    except OSError:
        same_tree = False
    if not same_tree:
        pytest.skip("filesystem does not alias these candidate names")
    run_root = run_alias / "runs"
    run_root.mkdir()
    monkeypatch.setattr(
        "cairnwork.run_scrub.os.listdir",
        lambda _path: pytest.fail("inside-tree root was listed"),
    )

    message = stale_run_error(_Account(run_root), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message


@pytest.mark.parametrize("alias_is_candidate", [False, True])
def test_private_var_firmlink_alias_is_refused(tmp_path, monkeypatch, alias_is_candidate):
    physical_candidate = tmp_path / "cand"
    physical_candidate.mkdir()
    candidate_text = str(physical_candidate)
    if candidate_text.startswith("/private/var/"):
        alias = Path(candidate_text.removeprefix("/private"))
    elif candidate_text.startswith("/var/"):
        alias = Path("/private" + candidate_text)
    else:
        pytest.skip("tmp_path is not under /var")
    candidate = alias if alias_is_candidate else physical_candidate
    run_parent = physical_candidate if alias_is_candidate else alias
    run_root = run_parent / "runs"
    try:
        same_tree = os.path.samefile(candidate, run_parent)
    except OSError:
        same_tree = False
    if not same_tree:
        pytest.skip("/var and /private/var are not aliases on this filesystem")
    run_root.mkdir()
    monkeypatch.setattr(
        "cairnwork.run_scrub.os.listdir",
        lambda _path: pytest.fail("inside-tree root was listed"),
    )

    message = stale_run_error(_Account(run_root), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message


def test_run_verifiers_prelude_receives_ctx_repo(tmp_path, monkeypatch):
    from cairnwork import verifiers
    from cairnwork.verify_context import VerifyContext

    repo = tmp_path / "repo"
    repo.mkdir()
    account: Any = SimpleNamespace(run_root=tmp_path / "runs")
    ctx = VerifyContext(repo, account.run_root, account=account, hash_seed="1")
    seen = []

    @contextmanager
    def prelude(_account, _python, _timeout, candidate_tree):
        seen.append(candidate_tree)
        yield

    monkeypatch.setattr(
        verifiers.pytest_config,
        "verifier_settings",
        lambda *_args: SimpleNamespace(pythonpath=()),
    )
    monkeypatch.setattr(verifiers, "verifier_interpreter", lambda _ctx: "/usr/bin/python3")
    monkeypatch.setattr(verifiers, "lock_wait_seconds", lambda *_args: 1)
    monkeypatch.setattr(verifiers, "locked_prelude", prelude)
    monkeypatch.setattr(verifiers, "_run_all", lambda *_args: ())

    assert verifiers.run_verifiers(ctx, "a" * 40, (), "run") == ()
    assert seen == [repo]


def test_per_spec_scrub_receives_ctx_repo(tmp_path, monkeypatch):
    from cairnwork import verifiers

    repo = tmp_path / "repo"
    repo.mkdir()

    class RecordingAccount(_Account):
        def __init__(self):
            super().__init__(tmp_path)
            self.candidates = []
            self.idle = 0

        def require_idle(self):
            self.idle += 1

        def scrub_stale_runs(self, candidate_tree):
            self.candidates.append(candidate_tree)

    account = RecordingAccount()
    ctx: Any = SimpleNamespace(run_root=tmp_path, repo=repo, account=account, attempt_id="attempt")
    no_settings: Any = None
    batch = verifiers._Batch(ctx, "/usr/bin/python3", "candidate", 1, no_settings, "", False)
    monkeypatch.setattr(verifiers, "make_work_dir", lambda work: work.mkdir(mode=0o700))
    monkeypatch.setattr(verifiers, "make_account_checkout", lambda *args: tmp_path / args[-1])
    monkeypatch.setattr(verifiers, "_run_one", lambda *_args: object())
    monkeypatch.setattr(verifiers, "unless_interrupted", lambda *args, **kwargs: _nullcontext())

    specs = [SimpleNamespace(verifier_id=verifier_id) for verifier_id in ("one", "two")]
    verifiers._run_all(batch, specs, "run")

    assert account.candidates == [repo]


def _nullcontext():
    from contextlib import nullcontext

    return nullcontext()


def test_run_blackbox_passes_ctx_repo_to_account_scrub(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from cairnwork import blackbox_exec
    from cairnwork.verify_context import VerifyContext

    repo = tmp_path / "repo"
    repo.mkdir()
    account: Any = SimpleNamespace(
        run_root=tmp_path / "account-runs",
        python="/usr/bin/python3",
        user="verifier",
        exclusive=lambda _timeout: nullcontext(),
        available=lambda _python: True,
        require_idle=lambda: None,
    )
    candidates = []
    account.scrub_stale_runs = candidates.append
    ctx = VerifyContext(repo, tmp_path / "runs", account=account)
    monkeypatch.setattr(
        blackbox_exec.pytest_config,
        "verifier_settings",
        lambda *_args: SimpleNamespace(pythonpath=()),
    )
    sentinel = object()
    monkeypatch.setattr(blackbox_exec, "_run", lambda *_args: sentinel)

    no_declaration: Any = None
    assert blackbox_exec.run_blackbox(no_declaration, ctx, "a" * 40, "run") is sentinel
    assert candidates == [repo]
