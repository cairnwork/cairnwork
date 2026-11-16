"""#423 on real bwrap: a credential link whose target has parentheses, a colon and non-ASCII
letters is masked at that target (the marker behind it is unreadable), not merely named in the
policy text. The witness runs only where bwrap is the platform backend (the test-linux-sandbox
CI job) and is skipped elsewhere. It is not mocked: no bwrap, no witness."""

import sys

import pytest
from conftest import commit_change
from fixture_tree import reach_env, world_base

import cairnwork_sandbox
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from cairnwork_sandbox import bwrap
from cairnwork_sandbox.backend import SandboxError

MARKER = "CANARY-423-SECRET\n"


@pytest.mark.sandbox
def test_link_to_a_non_ascii_paren_colon_target_is_unreadable_in_real_bwrap(
    repo, tmp_path, monkeypatch, request
):
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name != "bwrap":
        pytest.skip("needs real bwrap (Linux CI test-linux-sandbox); Seatbelt is a different path")
    base = world_base(request, "cw-link-423-")  # off the private /tmp (#391)
    home, target = base / "fakehome", base / "文件 (x):y"
    (home / ".aws").mkdir(parents=True)
    target.mkdir()
    (target / "secret.txt").write_text(MARKER)
    (home / ".aws" / "linked").symlink_to(target)  # absolute link inside a credential dir
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CW_CANARIES", f"{target}/secret.txt:{home}/.aws/linked/secret.txt")
    reach, check = reach_env(monkeypatch, backend, base)  # the control proves base is visible
    import cairnwork.verifiers as verifiers

    monkeypatch.setattr(verifiers, "_ENV_KEEP", (*verifiers._ENV_KEEP, "CW_CANARIES", *reach))
    code = (
        "import os\n"
        "def test_masked():\n"
        f"{check}"
        "    for path in os.environ['CW_CANARIES'].split(':'):\n"
        "        try:\n"
        "            data = open(path).read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        assert 'CANARY-423-SECRET' not in data, path\n"
    )
    head = commit_change(repo, {"tests/test_masked.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_masked.py"])
    runs = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    assert runs[0].observation.passed, runs[0].log_path.read_text()


@pytest.mark.parametrize("name", ["a\nb", "a\x00b"])
def test_forged_link_target_is_still_refused_and_never_reaches_the_policy(tmp_path, name):
    """Control, policy level (runs on any host; it is NOT a bwrap witness): a link to a target
    with a newline or NUL is refused, so no flag naming it is ever built."""
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    if "\x00" in name:  # a NUL cannot exist in a filename: forge it via the path string itself
        with pytest.raises((SandboxError, ValueError)):
            bwrap._safe_link(f"{tmp_path}/{name}")
        return
    target = tmp_path / name
    target.mkdir()
    (home / ".aws" / "linked").symlink_to(target)
    with pytest.raises(SandboxError):
        bwrap.BACKEND.policy((), (), home=home)
