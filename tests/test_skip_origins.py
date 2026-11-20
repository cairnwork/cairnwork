"""#1565: a failed run's reason says where each skipped test comes from (base-skip,
candidate-skip, always-skip); the verdict and the reason prefix do not change."""

from types import SimpleNamespace
from typing import Any

from cairnwork.core_request import VerifierObservation
from cairnwork.reasons import explain, why_not_passed

PREFIX = "pytest collected tests that did not pass"
KW: dict[str, Any] = dict(
    ran_sandboxed=True, returncode=0, timed_out=False, mutated=(), survivors=0,
    log_too_big=False, tests_ran=False,
)  # fmt: skip


def _run(outcomes, xfail=()):
    why = why_not_passed(**KW, outcomes=outcomes)
    obs = VerifierObservation("v1", "a", "h", False)
    spec = SimpleNamespace(verifier_id="v1")
    skips = {n: ("marker", "") for n, r in outcomes.items() if r == "skipped" and n not in xfail}
    return SimpleNamespace(
        spec=spec, observation=obs, why=why, notes=(), outcomes=outcomes, skips=skips
    )


def _base(nodes, skips):
    return SimpleNamespace(
        base_nodes={"v1": tuple(nodes)}, base_skips={"v1": dict.fromkeys(skips, ("marker", ""))}
    )


def test_a_skip_the_base_also_has_is_a_base_skip():
    run = _run({"t.py::a": "skipped", "t.py::b": "passed"})
    text = explain([run], _base(["t.py::a", "t.py::b"], ["t.py::a"]))
    assert PREFIX in text and "skip origin: base-skip 1: t.py??a" in text


def test_a_skip_the_base_ran_is_a_candidate_skip_not_a_base_skip():
    run = _run({"t.py::a": "skipped", "t.py::b": "skipped", "t.py::c": "passed"})
    text = explain([run], _base(["t.py::a", "t.py::b", "t.py::c"], ["t.py::b"]))
    assert "candidate-skip 1: t.py??a" in text and "base-skip 1: t.py??b" in text
    assert "base-skip 1: t.py??a" not in text


def test_all_tests_skipped_on_base_and_candidate_is_always_skip():
    run = _run({"t.py::a": "skipped", "t.py::b": "skipped"})
    text = explain([run], _base(["t.py::a", "t.py::b"], ["t.py::a", "t.py::b"]))
    assert "skip origin: always-skip" in text


def test_without_a_base_record_the_text_is_unchanged_and_still_failed():
    run = _run({"t.py::a": "skipped", "t.py::b": "passed"})
    plain = explain([run])
    assert plain == f" [v1: {PREFIX} (1 skipped: t.py??a)]"
    assert explain([run], SimpleNamespace(base_nodes={}, base_skips={})) == plain


def test_a_capped_base_skip_list_is_not_trusted():
    run = _run({"t.py::a": "skipped", "t.py::b": "passed"})
    skips = [f"x.py::{i}" for i in range(50)]
    assert explain([run], _base(["t.py::a", "t.py::b", *skips], skips)) == explain([run])


def test_a_run_with_a_skip_stays_failed_whatever_the_origin():
    run = _run({"t.py::a": "skipped", "t.py::b": "passed"})
    assert not run.observation.passed and run.why.startswith(PREFIX)
    text = explain([run], _base(["t.py::a", "t.py::b"], ["t.py::a"]))
    assert text.startswith(f" [v1: {PREFIX} (1 skipped: ") and text.endswith(")]")


def test_an_xfail_on_base_and_candidate_is_not_labelled_a_candidate_skip():
    run = _run({"t.py::a": "skipped", "t.py::b": "skipped"}, xfail=("t.py::a",))
    text = explain([run], _base(["t.py::a", "t.py::b"], ["t.py::b"]))
    assert "base-skip 1: t.py??b" in text and "t.py??a" not in text.split("skip origin")[1]


def test_a_skipped_node_the_base_never_collected_is_a_new_skip():
    run = _run({"t.py::a": "skipped", "t.py::new": "skipped", "t.py::b": "passed"})
    text = explain([run], _base(["t.py::a", "t.py::b"], ["t.py::a"]))
    assert "base-skip 1: t.py??a" in text and "new-skip 1: t.py??new" in text
