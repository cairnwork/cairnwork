"""What a v0.2 receipt's verifiers ran, against what its binding says was verified (#651).

The binding lists every test path of the test commit, so the receiver applies the producer's rule
(deferred_tests.untargeted): each Python test path is named whole by a bound verifier, or each of
its new or changed tests is named. Full mode reads those tests from the test commit itself
(deferred_tests.new_tests). Confidential mode cannot: there a file counts as named when a target
names a test in it, the signer's word, as the test contents are (``test_contents: not_verified``).
Each bound verifier must also be one Core required, or its run is not part of the verdict.
"""

from __future__ import annotations

from typing import Any

from cairnwork.deferred_tests import TestCommitRejected, new_tests, untargeted
from cairnwork_attest.binding_extras import required_ids
from cairnwork_attest.verify_response import VerifyResult, _fail

_PYTEST = ["python3", "-m", "pytest"]
_FLAGS = frozenset({"-q"})
_PAIRS = {"-p": "no:cacheprovider"}


def targets(argv: list[str]) -> tuple[str, ...]:
    """The targets of one bound argv in the one shape the producer accepts
    (verifier_spec.parse_verifier): ``python3 -m pytest`` with ``-q`` and ``-p no:cacheprovider``
    as the only options. Any other argv names no target, so it covers nothing."""
    if argv[:3] != _PYTEST:
        return ()
    rest, found = argv[3:], []
    while rest:
        if rest[0] in _FLAGS:
            rest = rest[1:]
        elif rest[0] in _PAIRS and rest[1:2] == [_PAIRS[rest[0]]]:
            rest = rest[2:]
        elif rest[0].startswith("-"):
            return ()
        else:
            found.append(rest.pop(0))
    return tuple(found)


def coverage_problem(ctx: Any) -> VerifyResult | None:
    """ctx is verify_binding's check context (binding and request; shapes already checked)."""
    binding = ctx.binding
    verifiers: list[Any] = binding["verifiers"]
    named, paths = [t for _, argv in verifiers for t in targets(argv)], tuple(binding["test_paths"])
    try:
        nodes = (
            new_tests(ctx.repo, binding["base"], binding["test_commit"], paths)
            if ctx.full
            else [t for t in named if "::" in t]
        )
    except TestCommitRejected as exc:  # the producer refuses such a test commit
        return _fail("TEST_PATH_NOT_TARGETED", f"cannot read the bound tests: {exc}")
    if missing := untargeted(paths, nodes, named):
        return _fail("TEST_PATH_NOT_TARGETED", f"no bound verifier runs these: {list(missing)}")
    required = required_ids(ctx.request)
    extra = sorted({vid for vid, _ in verifiers if vid not in (required or [])})
    if required is None or extra:
        return _fail("VERIFIER_NOT_REQUIRED", f"bound verifiers Core did not require: {extra}")
    return None
