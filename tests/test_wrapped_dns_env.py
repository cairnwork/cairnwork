"""#932: a host-name lookup the sandbox blocks raises ``socket.gaierror``, whose errno is an
``EAI_*`` value, not a denial errno. A test that fails only because such a lookup failed is an
environment failure (``fail_kind`` env, the base UNCOMPARABLE ``BASE_ENV_ERROR``), also when a
library wraps it in its own non-``OSError`` exception (``raise ... from`` or an unhandled one in
``except``). Measured through the real sandbox on whichever backend runs (Seatbelt, bwrap). A
wrapped failure that is not an ``OSError`` stays teeth."""

import sys

import pytest
from conftest import commit_change

from cairnwork.base_teeth import base_teeth
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox

LOOKUP = "socket.getaddrinfo('cairnwork-not-in-hosts.example', 80)"
CASES = {
    "cause": (
        "    try:\n"
        f"        {LOOKUP}\n"
        "    except OSError as exc:\n"
        "        raise ClientError('lookup failed') from exc\n"
    ),
    "context": (
        "    try:\n"
        f"        {LOOKUP}\n"
        "    except OSError:\n"
        "        raise ClientError('lookup failed')\n"
    ),
}
NOT_OSERROR = (
    "    try:\n"
    "        {}['key']\n"
    "    except KeyError as exc:\n"
    "        raise ClientError('lookup failed') from exc\n"
)
NODE = "tests/test_net.py::test_fetch"


def _base(repo, tmp_path, body):
    code = f"import socket\nclass ClientError(Exception):\n    pass\ndef test_fetch():\n{body}"
    head = commit_change(repo, {"tests/test_net.py": code})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "-q", "tests/test_net.py"])
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    runs = run_verifiers(ctx, head, (spec,), "r1")
    assert runs[0].outcomes == {NODE: "failed"}, runs[0].outcomes
    return runs


@pytest.mark.parametrize("how", sorted(CASES))
def test_a_wrapped_blocked_lookup_is_an_environment_failure(repo, tmp_path, how):
    runs = _base(repo, tmp_path, CASES[how])
    assert runs[0].fail_kind == {NODE: "env"}
    assert base_teeth(runs, {NODE})[:2] == ("UNCOMPARABLE", "BASE_ENV_ERROR")


def test_a_wrapped_failure_that_is_not_an_oserror_keeps_its_teeth(repo, tmp_path):
    runs = _base(repo, tmp_path, NOT_OSERROR)
    assert runs[0].fail_kind == {}
    assert base_teeth(runs, {NODE}) == ("TEETH", "", "")
