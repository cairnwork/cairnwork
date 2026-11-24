"""#353: a run whose credential masks did not hold for the whole run is never counted. The check
runs on any host through the real bwrap policy builder (a fake home, no bwrap needed); the wiring
tests use a fake backend. The real-bwrap witnesses are in tests/test_bwrap_482_real.py."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import commit_change

import cairnwork_sandbox
from cairnwork import (
    account_check,
    blackbox,
    blackbox_exec,
    blackbox_judge,
    mask_recheck,
    verifier_exec,
)
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.mask_recheck import MaskChanged, PolicyChanged, guard
from cairnwork.verify_context import VerifyContext
from cairnwork_sandbox import bwrap, bwrap_private
from cairnwork_sandbox.backend import Policy, SandboxError

BACKEND = bwrap.BACKEND


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home.resolve()


def _policy(home: Path, owner: bool = True) -> Policy:
    return BACKEND.policy((), (), home=home, mask_home_credentials=owner)


def _run(home: Path, change, owner: bool = True) -> None:
    """The guard around a run whose body is ``change`` (the host event)."""
    with guard(BACKEND, _policy(home, owner)):
        change()


def _point(link: Path, target: Path) -> None:
    link.unlink()
    link.symlink_to(target)


def test_the_error_is_a_sandbox_error_with_a_code():
    assert issubclass(MaskChanged, SandboxError)
    assert str(MaskChanged("x")) == "CREDENTIAL_MASK_CHANGED: x"


def test_a_repointed_credential_link_is_detected_and_named(tmp_path, home):
    for name in ("v1", "v2"):
        (tmp_path / "keys" / name).mkdir(parents=True)
    (home / ".ssh").symlink_to(tmp_path / "keys" / "v1")
    with pytest.raises(MaskChanged, match=r"CREDENTIAL_MASK_CHANGED.*\.ssh"):
        _run(home, lambda: _point(home / ".ssh", tmp_path / "keys" / "v2"))


def test_no_change_or_an_in_place_rewrite_is_not_a_change(home):
    (home / ".netrc").write_text("machine a password b\n")
    _run(home, lambda: None)
    _run(home, lambda: (home / ".netrc").write_text("machine c password d, longer\n"))


def test_a_regular_file_replaced_by_rename_is_detected(home):
    (home / ".netrc").write_text("old\n")
    (home / "new").write_text("new\n")
    with pytest.raises(MaskChanged, match=r"\.netrc"):
        _run(home, lambda: os.replace(home / "new", home / ".netrc"))


def test_delete_then_create_of_a_file_is_detected(home):
    (home / ".npmrc").write_text("old\n")

    def change():
        (home / ".npmrc").unlink()
        (home / ".npmrc").write_text("old\n")  # same name and content, a new object

    with pytest.raises(MaskChanged, match=r"\.npmrc"):
        _run(home, change)


def test_a_deleted_credential_file_is_a_change(home):
    (home / ".npmrc").write_text("old\n")
    with pytest.raises(MaskChanged, match=r"\.npmrc"):
        _run(home, (home / ".npmrc").unlink)


def test_a_directory_removed_and_made_again_is_detected(home):
    (home / ".gnupg").mkdir()

    def change():
        (home / ".gnupg").rmdir()
        (home / ".gnupg").mkdir()

    with pytest.raises(MaskChanged, match=r"\.gnupg"):
        _run(home, change)


def test_a_credential_path_created_during_the_run_is_detected(home):
    with pytest.raises(MaskChanged, match=r"\.vault-token"):
        _run(home, lambda: (home / ".vault-token").write_text("t\n"))


def test_a_socket_tree_created_during_the_run_is_detected(tmp_path, home, monkeypatch):
    tree = tmp_path / "x11-style"  # a tree that is private only once it exists
    monkeypatch.setattr(bwrap_private, "TREES", (str(tree),))
    with pytest.raises(MaskChanged, match="x11-style"):
        _run(home, tree.mkdir)


def test_a_daemon_socket_created_during_the_run_is_detected(tmp_path, home, monkeypatch):
    sock = tmp_path / "daemon.sock"
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(sock),))
    with pytest.raises(MaskChanged, match=r"daemon\.sock"):
        _run(home, lambda: sock.write_text(""))


@pytest.mark.parametrize(("reach", "raises"), [(False, False), (True, True)])
def test_account_mode_counts_only_what_the_account_can_reach(
    tmp_path, home, monkeypatch, reach, raises
):
    sock = tmp_path / "daemon.sock"
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(sock),))
    monkeypatch.setattr(bwrap, "account_may_traverse", lambda directory: reach)
    if raises:
        with pytest.raises(MaskChanged, match=r"daemon\.sock"):
            _run(home, lambda: sock.write_text(""), owner=False)
    else:
        _run(home, lambda: sock.write_text(""), owner=False)


def test_a_policy_without_inputs_is_refused_before_the_run(home):
    ran = []
    with pytest.raises(MaskChanged, match="no build inputs"):
        with guard(BACKEND, replace(_policy(home), inputs=None)):
            ran.append(1)
    assert ran == []


def test_another_backend_is_a_no_op():
    seatbelt = SimpleNamespace(name="seatbelt")
    with guard(seatbelt, Policy("(version 1)\n")):
        pass


def test_inputs_rebuild_the_same_policy_and_never_reach_the_hash(home):
    policy = _policy(home)
    assert policy.inputs is not None
    assert BACKEND.policy(*policy.inputs).text == policy.text
    bare = replace(policy, inputs=None)
    assert policy.sha256 == hashlib.sha256(policy.text.encode()).hexdigest() == bare.sha256
    assert policy == bare and hash(policy) == hash(bare)


def _fake(rebuild):
    return SimpleNamespace(name="bwrap", masked_home_paths=(), policy=rebuild)


def _raising(exc):
    def rebuild(*_args):
        raise exc

    return rebuild


@pytest.mark.parametrize("exc", [OSError("gone"), SandboxError("too broad")])
def test_an_error_while_rechecking_counts_as_a_change(exc):
    with pytest.raises(MaskChanged, match="could not be re-checked"):
        with guard(_fake(_raising(exc)), Policy("--ro-bind\n/\n/\n", (), ())):
            pass


def test_an_oserror_reading_a_mask_while_rechecking_counts_as_a_change(home, monkeypatch):
    (home / ".netrc").write_text("x\n")

    def boom(_path):
        raise PermissionError("denied")

    with pytest.raises(MaskChanged, match="could not be re-checked"):
        _run(home, lambda: monkeypatch.setattr(mask_recheck, "_identity", boom))


def _drifting(home, change):
    """A guard whose rebuilt policy is ``change(original text)`` (host churn seen on rebuild)."""
    policy = _policy(home)
    fake = SimpleNamespace(
        name="bwrap",
        masked_home_paths=BACKEND.masked_home_paths,
        policy=lambda *_a: replace(policy, text=change(policy.text)),
    )
    return fake, policy


def test_a_difference_outside_the_mask_list_is_not_called_a_credential_change(home):
    fake, policy = _drifting(home, lambda text: text + "\n--symlink\n/run/x\n/etc/x")
    with pytest.raises(PolicyChanged) as err:
        with guard(fake, policy):
            pass
    assert err.value.code == "SANDBOX_POLICY_CHANGED" and issubclass(PolicyChanged, MaskChanged)
    assert "CREDENTIAL" not in str(err.value)
    assert "other than the credential masks" in str(err.value)
    assert "retry is safe" in str(err.value)


def test_a_changed_mask_list_keeps_the_credential_code(home):
    fake, policy = _drifting(home, lambda text: text + "\n--tmpfs\n/home/x/.gnupg-new")
    with pytest.raises(MaskChanged) as err:
        with guard(fake, policy):
            pass
    assert type(err.value) is MaskChanged and err.value.code == "CREDENTIAL_MASK_CHANGED"
    assert ".gnupg-new" in str(err.value)


def test_the_same_masks_in_another_order_is_a_credential_change(home):
    (home / ".netrc").write_text("x\n")
    (home / ".vault-token").write_text("t\n")
    policy = _policy(home)
    assert len(mask_recheck.mask_paths(policy.text)) >= 2

    def reorder(text):
        lines = text.split("\n")
        pairs = [
            i
            for i, line in enumerate(lines[:-1])
            if line == "--ro-bind" and lines[i + 1] == "/dev/null"
        ]
        a, b = pairs[0], pairs[1]
        lines[a : a + 3], lines[b : b + 3] = lines[b : b + 3], lines[a : a + 3]
        return "\n".join(lines)

    fake, policy = _drifting(home, reorder)
    with pytest.raises(MaskChanged) as err:
        with guard(fake, policy):
            pass
    assert type(err.value) is MaskChanged


def test_a_mask_change_together_with_another_difference_keeps_the_credential_code(home):
    fake, policy = _drifting(
        home, lambda text: text + "\n--symlink\n/run/x\n/etc/x\n--tmpfs\n/home/x/.gnupg-new"
    )
    with pytest.raises(MaskChanged) as err:
        with guard(fake, policy):
            pass
    assert type(err.value) is MaskChanged


def test_run_confined_raises_the_policy_code_for_a_difference_outside_the_masks(
    tmp_path, home, monkeypatch
):
    fake, policy = _drifting(home, lambda text: text + "\n--symlink\n/run/x\n/etc/x")
    monkeypatch.setattr(verifier_exec, "_execute", lambda *_a, **_k: (0, False, 0))
    args = (False, policy, "py", ["t"], tmp_path / "p", tmp_path, {})
    with pytest.raises(PolicyChanged, match="other than the credential masks"):
        verifier_exec._run_confined(*args, 5, tmp_path / "log", None, fake)


def test_a_body_that_raised_propagates_unchanged(home):
    def failing():
        (home / ".vault-token").write_text("t\n")  # a change too: the body's error still wins
        raise OSError("from the run")

    with pytest.raises(OSError, match="from the run"):
        _run(home, failing)


def test_the_message_has_names_not_paths(tmp_path, home):
    (home / ".netrc").write_text("old\n")
    (home / "new").write_text("new\n")
    with pytest.raises(MaskChanged) as err:
        _run(home, lambda: os.replace(home / "new", home / ".netrc"))
    assert str(home) not in str(err.value) and ".netrc" in str(err.value)


# Wiring: a host event after the sandboxed run must surface as MaskChanged at every caller.


def test_run_confined_raises_when_the_masks_changed_during_the_run(tmp_path, home, monkeypatch):
    (home / ".netrc").write_text("old\n")
    (home / "new").write_text("new\n")

    def execute(*_args, **_kwargs):  # the host event happens while the sandboxed run is going
        os.replace(home / "new", home / ".netrc")
        return 0, False, 0

    monkeypatch.setattr(verifier_exec, "_execute", execute)
    args = (False, _policy(home), "py", ["t"], tmp_path / "p", tmp_path, {})
    with pytest.raises(MaskChanged, match=r"\.netrc"):
        verifier_exec._run_confined(*args, 5, tmp_path / "log", None, BACKEND)


def test_run_confined_returns_normally_when_the_masks_held(tmp_path, home, monkeypatch):
    (home / ".netrc").write_text("old\n")
    monkeypatch.setattr(verifier_exec, "_execute", lambda *_a, **_k: (0, False, 0))
    args = (False, _policy(home), "py", ["t"], tmp_path / "p", tmp_path, {})
    done = verifier_exec._run_confined(*args, 5, tmp_path / "log", None, BACKEND)
    assert done[1:4] == (0, False, 0)


def test_base_stage_makes_it_uncomparable():
    with pytest.raises(BaseUncomparable, match="CREDENTIAL_MASK_CHANGED") as err:
        with account_check.base_stage(None, None):
            raise MaskChanged("the credential masks did not hold")
    assert err.value.comparison.code == account_check.UNUSABLE
    assert err.value.comparison.kind == "UNCOMPARABLE"


def _declared():
    check = {"id": "n", "module": "calc", "function": "f",
             "cases": [{"id": "c1", "args": [], "expected": 1}]}  # fmt: skip
    return blackbox.parse(json.dumps({"salt": "ab" * 32, "checks": [check]}).encode())


def test_blackbox_checks_are_incomplete_when_the_masks_changed(monkeypatch):
    def changed(*_args, **_kwargs):
        raise MaskChanged("the credential masks did not hold")

    monkeypatch.setattr(blackbox_judge, "run_blackbox", changed)
    (verdict,), log = blackbox_judge.judge_blackbox(
        _declared(), VerifyContext(Path("/r"), Path("/x")), "c" * 40, "a-v"
    )
    assert log is None and not verdict.observation.passed
    assert verdict.reason == "blackbox:n: VERIFICATION_INCOMPLETE: RUN_ERROR:MaskChanged"


def test_the_blackbox_run_itself_is_guarded(repo, tmp_path, monkeypatch):
    texts = iter(["--tmpfs\n/a\n", "--tmpfs\n/a\n--tmpfs\n/b\n"])  # build, then the re-check
    backend = _fake(lambda *_a, **_k: Policy(next(texts), (), ()))
    monkeypatch.setattr(cairnwork_sandbox, "select", lambda: backend)
    monkeypatch.setattr(blackbox_exec, "_execute", lambda *_a, **_k: (0, False, 0))
    head = commit_change(repo, {"calc.py": "def f():\n    return 1\n"})
    with pytest.raises(MaskChanged, match="did not hold"):
        blackbox_exec.run_blackbox(_declared(), VerifyContext(repo, tmp_path / "runs"), head, "a-v")


def test_a_repointed_link_inside_a_credential_directory_is_detected(tmp_path, home):
    """#377 masks the outside target of a link inside ``~/.aws``; re-pointing it unmasks the new
    target, and the same rebuild-and-compare catches it with no second list of masks."""
    aws, outside = home / ".aws", tmp_path / "outside"
    aws.mkdir()
    outside.mkdir()
    for name in ("v1", "v2"):
        (outside / name).write_text(f"{name}-marker-353")
    link = aws / "credentials"
    link.symlink_to(outside / "v1")
    with pytest.raises(MaskChanged):
        _run(home, lambda: _point(link, outside / "v2"))


def test_a_link_inside_a_credential_directory_that_stays_inside_is_not_a_change(home):
    aws = home / ".aws"
    aws.mkdir()
    for name in ("one", "two"):
        (aws / name).write_text(name)
    link = aws / "credentials"
    link.symlink_to(aws / "one")
    _run(home, lambda: _point(link, aws / "two"))
