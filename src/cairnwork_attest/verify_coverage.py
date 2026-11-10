"""What a v0.2 receipt's verifiers ran, against what its binding says was verified (#651).

The binding lists every test path of the test commit, so the receiver applies the producer's rule
(deferred_tests.untargeted): each Python test path is named whole by a bound verifier, or each of
its new or changed tests is named. Full mode reads those tests from the test commit itself
(deferred_tests.new_tests). Confidential mode cannot: there a file counts as named when a target
names a test in it, the signer's word, as the test contents are (``test_contents: not_verified``).
Each bound verifier must also be one Core required, or its run is not part of the verdict.

The rule is static: it reads which tests a target names, not which ones pytest collected. The
producer also refuses a test commit whose new or changed tests are not all in the base run's guard
record (DEFERRED_TESTS_NOT_RUN, #660), and a candidate whose verification runs did not all run them
(DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION, #697); the receipt carries no such record, so the receiver
relies on the signer for that. What the files themselves show it checks in full mode, through
new_tests: a test a class inherits from another in the file is a test of that class too, and a new
test in a class pytest does not collect (``__init__``/``__new__``) is refused
(DEFERRED_TESTS_NOT_COLLECTED, reported as TEST_PATH_NOT_TARGETED). Confidential mode sees neither,
as above.

Data paths (#850): a test commit may declare ``.py`` files that are data, not tests, in a
``Cairnwork-Data`` trailer; the receipt then carries ``data_paths`` and ``data_rule``. Only a full
receiver whose policy sets ``allow_data_paths: true`` accepts them (DATA_PATHS_NOT_ALLOWED
otherwise); it re-derives the declaration from the pinned test commit (DATA_PATHS_MISMATCH,
DATA_PATHS_INVALID). A data file is not read for tests, is exempt from the rule above, and no
verifier may name it. That it was not executed is the signer's word (``assurance.data_rule``).
"""

from __future__ import annotations

from typing import Any

from cairnwork import protection
from cairnwork.deferred_tests import TestCommitRejected, new_tests, untargeted
from cairnwork.issue_task import test_target
from cairnwork_attest.binding_extras import conftest_problem, data_problem, required_ids
from cairnwork_attest.verify_response import VerifyResult, _fail

_PYTEST = ["python3", "-m", "pytest"]
_FLAGS = frozenset({"-q"})
_PAIRS = {"-p": "no:cacheprovider"}
_TARGET = test_target(None)  # the producer's target grammar, under any plain directory


def targets(argv: list[str]) -> tuple[str, ...]:
    """The targets of one bound argv in the one shape the producer accepts
    (verifier_spec.parse_verifier): ``python3 -m pytest`` with ``-q`` and ``-p no:cacheprovider``
    as the only options, and every target in the producer's grammar (issue_task.test_target).
    Any other argv names no target, so it covers nothing."""
    if argv[:3] != _PYTEST:
        return ()
    rest, found = argv[3:], []
    while rest:
        if rest[0] in _FLAGS:
            rest = rest[1:]
        elif rest[0] in _PAIRS and rest[1:2] == [_PAIRS[rest[0]]]:
            rest = rest[2:]
        elif rest[0].startswith("-") or not _TARGET.fullmatch(rest[0]):
            return ()
        else:
            found.append(rest.pop(0))
    return tuple(found)


def _tests(paths: tuple[str, ...], data: frozenset[str]) -> tuple[str, ...]:
    return tuple(p for p in paths if p not in data)


def coverage_problem(ctx: Any) -> VerifyResult | None:
    """ctx is verify_binding's check context (binding and request; shapes already checked)."""
    binding = ctx.binding
    verifiers: list[Any] = binding["verifiers"]
    ids = [verifier_id for verifier_id, _ in verifiers]
    if len(ids) != len(set(ids)) or any(protection.reserved(verifier_id) for verifier_id in ids):
        return _fail(
            "VERIFIER_ID_INVALID", "bound verifier ids must be unique and cannot be reserved"
        )
    named, paths = [t for _, argv in verifiers for t in targets(argv)], tuple(binding["test_paths"])
    data = frozenset(binding.get("data_paths", []))  # #850: declared data, never read for tests
    if (problem := data_problem(ctx)) is not None:  # a failed result is falsy
        return problem
    folded = {path.casefold() for path in data}  # a case variant names the same file on macOS
    if clash := sorted(t for t in named if t.split("::", 1)[0].casefold() in folded):
        return _fail("DATA_PATHS_INVALID", f"a verifier names a data path: {clash}")
    try:
        nodes = (
            new_tests(ctx.repo, binding["base"], binding["test_commit"], _tests(paths, data))
            if ctx.full
            else [t for t in named if "::" in t]
        )
    except TestCommitRejected as exc:  # the producer refuses such a test commit
        return _fail("TEST_PATH_NOT_TARGETED", f"cannot read the bound tests: {exc}")
    if (problem := conftest_problem(ctx)) is not None:  # #798: a failed result is falsy
        return problem
    exempt = frozenset(binding.get("conftest_paths", [])) | data  # validated subsets of test_paths
    if missing := untargeted(tuple(p for p in paths if p not in exempt), nodes, named):
        return _fail("TEST_PATH_NOT_TARGETED", f"no bound verifier runs these: {list(missing)}")
    required = required_ids(ctx.request)
    extra = sorted({vid for vid, _ in verifiers if vid not in (required or [])})
    if required is None or extra:
        return _fail("VERIFIER_NOT_REQUIRED", f"bound verifiers Core did not require: {extra}")
    return None
