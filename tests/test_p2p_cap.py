"""#333: module-dir caps a large test directory to the files named after the change.

On a repo with large flat test directories, module-dir selected the whole directory (5,498 tests
in the expansion trial), so the base probe timed out and the task ended UNCOMPARABLE. A directory
with more than MODULE_DIR_CAP test files now contributes only its name-matched files; a smaller
one is still selected whole (#117: a flat layout brings in the sibling of the matched test). Every
cut is recorded, so "0 P2P tests" never reads as "P2P passed".
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_p2p_plan import RESULT, TASK, make_run, plan, uncomparable

from cairnwork import p2p
from cairnwork.p2p import P2PPlan, run_plan
from cairnwork.p2p_selection import MODULE_DIR_CAP, cap_note
from cairnwork.p2p_selection import select_p2p_targets as _select
from cairnwork.verify_context import VerifyContext

ROOTS = ("tests",)


def context(task, run_root) -> VerifyContext:
    """The context the pipeline builds for ``task``'s attempt RESULT."""
    return VerifyContext.of(task, run_root, RESULT.attempt_id)


def flat(count: int, *named: str) -> tuple[str, ...]:
    """``count`` test files directly in tests/, the ``named`` ones among them."""
    fillers = tuple(f"tests/test_m{i:03}.py" for i in range(count - len(named)))
    return tuple(sorted((*fillers, *(f"tests/{n}" for n in named))))


def select(changed, existing):
    return _select(changed, existing, ROOTS, "module-dir")


def note(changed, existing, mode="module-dir"):
    return cap_note(changed, existing, ROOTS, mode)


def test_only_module_dir_is_cut():
    existing = flat(MODULE_DIR_CAP + 1, "test_foo.py")
    assert note(("src/pkg/foo.py",), existing, "whole-suite") == {}
    assert _select(("src/pkg/foo.py",), existing, ROOTS, "whole-suite") == existing


def test_a_large_flat_directory_is_cut_to_the_named_test():
    existing = flat(MODULE_DIR_CAP + 1, "test_foo.py")
    assert select(("src/pkg/foo.py",), existing) == ("tests/test_foo.py",)
    assert note(("src/pkg/foo.py",), existing) == {"capped_dirs": ["tests"], "cap": MODULE_DIR_CAP}


def test_a_directory_at_the_cap_is_still_selected_whole():
    existing = flat(MODULE_DIR_CAP, "test_foo.py")
    assert select(("src/pkg/foo.py",), existing) == existing
    assert note(("src/pkg/foo.py",), existing) == {}


def test_only_files_directly_in_a_directory_count_toward_the_cap():
    direct = flat(MODULE_DIR_CAP - 5, "test_foo.py")
    nested = tuple(f"tests/sub/test_n{i:02}.py" for i in range(10))  # 30 under tests/ in all
    assert select(("src/pkg/foo.py",), direct + nested) == direct
    assert note(("src/pkg/foo.py",), direct + nested) == {}


def test_the_117_sibling_is_still_selected_and_nothing_is_cut():
    existing = ("tests/test_base.py", "tests/test_sibling.py")
    assert select(("src/pkg/base.py",), existing) == existing
    assert note(("src/pkg/base.py",), existing) == {}


def test_an_unmapped_change_with_a_large_root_selects_nothing_and_says_so():
    existing = flat(MODULE_DIR_CAP + 1)
    assert select(("src/pkg/zzz.py",), existing) == ()
    assert note(("src/pkg/zzz.py",), existing) == {"capped_dirs": ["tests"], "cap": MODULE_DIR_CAP}


def test_a_large_mirror_directory_is_cut_and_a_small_one_is_not():
    big = tuple(f"tests/sub/test_m{i:03}.py" for i in range(MODULE_DIR_CAP)) + (
        "tests/sub/test_a.py",
    )
    assert select(("src/p/sub/a.py",), big) == ("tests/sub/test_a.py",)
    small = ("tests/sub/test_a.py", "tests/sub/test_b.py")
    assert select(("src/p/sub/a.py",), small) == small


def test_the_cut_keeps_only_files_named_after_a_change():
    existing = flat(MODULE_DIR_CAP + 1, "test_foo.py", "foo_test.py", "test_bar.py")
    got = select(("src/pkg/foo.py", "src/pkg/bar.py"), existing)
    assert got == ("tests/foo_test.py", "tests/test_bar.py", "tests/test_foo.py")


def test_the_rule_names_the_cut_and_what_was_probed(monkeypatch):
    existing = flat(MODULE_DIR_CAP + 1, "test_b.py")
    outcomes = {"tests/test_b.py::test_1": "passed"}
    got, probed = plan(monkeypatch, existing, [(outcomes, "")], mode="module-dir")
    assert isinstance(got, P2PPlan) and probed == [("tests/test_b.py",)]
    assert got.rule["capped_dirs"] == ["tests"] and got.rule["cap"] == MODULE_DIR_CAP
    assert json.loads(got.note) == got.rule  # bound into the observation's artifact hash


def test_a_rule_without_a_cut_is_unchanged(monkeypatch):
    existing = ("tests/test_b.py", "tests/test_c.py")
    got, _ = plan(monkeypatch, existing, [({"tests/test_b.py::t": "passed"}, "")], "module-dir")
    assert isinstance(got, P2PPlan)
    assert "capped_dirs" not in got.rule and "cap" not in got.rule


def test_a_cut_to_nothing_is_recorded_as_no_p2p_not_as_a_pass(monkeypatch, tmp_path):
    got, probed = plan(monkeypatch, flat(MODULE_DIR_CAP + 1), [], mode="module-dir")
    assert isinstance(got, P2PPlan) and got.spec is None and probed == []
    assert got.rule["targets"] == 0 and got.rule["capped_dirs"] == ["tests"]
    runs, rule = run_plan(got, TASK, "c" * 40, None, VerifyContext.of(TASK, tmp_path))
    assert runs == () and rule["capped_dirs"] == ["tests"]  # what the result's "p2p" shows


def test_a_degraded_cut_to_nothing_is_uncomparable_and_names_the_cut(monkeypatch):
    got, _ = plan(monkeypatch, flat(MODULE_DIR_CAP + 1), [(None, "base-probe-timeout")])
    assert uncomparable(got)
    assert "capped: tests" in str(got) and f"over {MODULE_DIR_CAP}" in str(got)


@pytest.mark.parametrize("count", [MODULE_DIR_CAP, MODULE_DIR_CAP + 1])
def test_base_env_check_probes_the_capped_selection(monkeypatch, tmp_path, count):
    existing = flat(count, "test_b.py")
    probed = []

    def fake_probe(task, files, result, ctx, budget, tag="p"):
        probed.append(files)
        return {}, ""

    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: existing)
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    monkeypatch.setattr(p2p, "_env_fingerprint", lambda python: None)
    task = SimpleNamespace(**{**vars(TASK), "allowed_files": ("src/pkg/b.py",)})
    task.p2p_mode, task.test_commit, task.p2p_budget = "module-dir", "t" * 40, 30
    p2p.base_env_check(task, None, VerifyContext.of(task, tmp_path))
    assert probed == [existing if count == MODULE_DIR_CAP else ("tests/test_b.py",)]


def _cap_task(monkeypatch, existing, changed, by_test_commit=frozenset(), outcomes=None):
    """A module-dir task over ``existing`` base tests (the real ``exclude`` rule), probes stubbed
    to pass the first file (or to return ``outcomes``), the test commit changing the test
    functions ``by_test_commit``; returns (task, the file sets probed)."""
    probed: list[tuple[str, ...]] = []

    def fake_probe(task, files, result, ctx, budget, tag="p"):
        probed.append(files)
        return outcomes or {f"{files[0]}::t": "passed"}, ""

    monkeypatch.setattr(p2p, "changed_tests", lambda repo, base, test_commit, paths: by_test_commit)

    base = lambda task, exclude: tuple(p for p in existing if p not in exclude)  # noqa: E731
    monkeypatch.setattr(p2p, "_base_tests", base)
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    monkeypatch.setattr(p2p, "_env_fingerprint", lambda python: None)
    task = SimpleNamespace(**{**vars(TASK), "allowed_files": changed})
    task.p2p_mode, task.test_commit, task.p2p_budget = "module-dir", "t" * 40, 30
    return task, probed


def test_a_deferred_test_on_the_base_counts_toward_the_cap_in_both_steps(monkeypatch, tmp_path):
    # #361: base_env_check cannot know the deferred tests (the test commit is not on the machine
    # before the worker), so the plan must cut the same directories; 26 files with one deferred
    # used to be cut before the worker and probed whole (25 files) after it
    existing = flat(MODULE_DIR_CAP + 1, "test_b.py", "test_d.py")
    task, probed = _cap_task(monkeypatch, existing, ("src/pkg/b.py",))
    p2p.base_env_check(task, RESULT, context(task, tmp_path))
    got = p2p.plan_p2p(
        task, (), ("tests/test_d.py",), "module-dir", 30, RESULT, context(task, tmp_path)
    )
    assert probed == [("tests/test_b.py",), ("tests/test_b.py",)]
    assert got.rule["capped_dirs"] == ["tests"] and got.rule["selected_files"] == 1


def test_a_changed_test_file_stays_in_the_selection_it_maps_to(monkeypatch, tmp_path):
    existing = flat(MODULE_DIR_CAP, "test_b.py", "test_d.py")
    task, probed = _cap_task(monkeypatch, existing, ("src/pkg/d.py",))
    got = p2p.plan_p2p(
        task, (), ("tests/test_d.py",), "module-dir", 30, RESULT, context(task, tmp_path)
    )
    assert probed == [existing]
    assert "capped_dirs" not in got.rule


def test_a_changed_test_file_leaves_out_only_the_changed_tests(monkeypatch, tmp_path):
    # the changed file is bound by function, never whole: on the candidate it also holds the test
    # commit's own tests; a new file of the test commit is not on the base, so never selected
    existing = ("tests/test_b.py", "tests/test_d.py")
    d = "tests/test_d.py"
    outcomes = {"tests/test_b.py::t": "passed", f"{d}::t_kept[1]": "passed",
                f"{d}::t_kept[2]": "passed", f"{d}::t_new": "passed"}  # fmt: skip
    task, probed = _cap_task(monkeypatch, existing, ("src/pkg/b.py",),
                             frozenset({"tests/test_d.py::t_new"}), outcomes)  # fmt: skip
    got = p2p.plan_p2p(task, (), ("tests/test_d.py", "tests/test_n.py"), "whole-suite", 30,
                       RESULT, context(task, tmp_path))  # fmt: skip
    assert probed == [existing] and got.rule["selected_files"] == 2
    assert got.spec is not None
    assert got.spec.targets == ("tests/test_b.py", "tests/test_d.py::t_kept")
    assert got.nodes == tuple(n for n in outcomes if not n.endswith("t_new"))
    assert got.rule["nodes"] == 3 and got.rule["changed_by_test_commit"] == 1


def test_a_cut_that_leaves_only_changed_tests_runs_no_p2p_and_says_so(monkeypatch, tmp_path):
    # #389 review: the cut keeps test_d.py (named after the change); the test commit changes its
    # only test, so nothing is bound; the rule records 0 targets and names the cut
    existing = flat(MODULE_DIR_CAP + 1, "test_d.py")
    task, probed = _cap_task(monkeypatch, existing, ("src/pkg/d.py",),
                             frozenset({"tests/test_d.py::t"}))  # fmt: skip
    got = p2p.plan_p2p(
        task, (), ("tests/test_d.py",), "module-dir", 30, RESULT, context(task, tmp_path)
    )
    assert probed == [("tests/test_d.py",)] and got.spec is None
    assert got.rule["targets"] == 0 and got.rule["capped_dirs"] == ["tests"]
    assert got.rule["changed_by_test_commit"] == 1


def test_a_base_probe_whose_log_went_over_the_cap_is_degraded_like_an_oversized_report(
    monkeypatch, tmp_path
):
    # #481: a narrower suite writes a smaller log, so this one may degrade (not UNCLEAN)
    run = make_run(True, outcomes={"tests/test_a.py::test_a": "passed"}, log_dir=tmp_path)
    monkeypatch.setattr(p2p, "run_verifiers", lambda *a, **k: (replace(run, log_too_big=True),))
    got = p2p._probe(TASK, ("tests/test_a.py",), RESULT, context(TASK, tmp_path), 30)
    assert got == (None, "base-probe-log-over-cap") and got[1] not in p2p.UNCLEAN


def test_a_log_over_the_cap_twice_degrades_once_then_is_uncomparable(monkeypatch, tmp_path):
    # #481, through the real _probe: whole-suite over the log cap, module-dir over it again
    run = make_run(True, outcomes={"tests/test_calc.py::test_a": "passed"}, log_dir=tmp_path)
    probed = []

    def fake_run_verifiers(ctx, commit, specs, run_id, **kwargs):
        probed.append(specs[0].targets)
        return (replace(run, log_too_big=True),)

    existing = ("tests/nested/test_big.py", "tests/test_calc.py")
    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: existing)
    monkeypatch.setattr(p2p, "run_verifiers", fake_run_verifiers)
    with pytest.raises(p2p.BaseUncomparable) as raised:
        p2p.plan_p2p(TASK, (), (), "whole-suite", 30, RESULT, context(TASK, tmp_path))
    assert uncomparable(raised.value) and probed == [existing, ("tests/test_calc.py",)]
    assert "base-probe-log-over-cap" in str(raised.value), str(raised.value)


@pytest.mark.parametrize(
    ("taint", "on_timeout"),
    [
        ({"forged": ("tests/__pycache__/test_a.cpython-312.pyc",)}, "base-probe-forged"),
        ({"survivors": 1}, "base-probe-timeout"),  # the timeout's kill can leave a survivor count
        ({"mutated": ("junk.txt",)}, "base-probe-timeout"),  # or a half-written file
    ],
)
@pytest.mark.parametrize("cap", ["timed_out", "report_too_big", "log_too_big"])
def test_an_unclean_probe_that_also_hit_a_limit_keeps_its_unclean_reason(
    monkeypatch, tmp_path, cap, taint, on_timeout
):
    # #481 sweep (V2): a limit must not hide an unclean base run behind a reason that degrades;
    # only after a timeout may survivors and mutated be the timeout's own doing
    run = make_run(False, timed_out=cap == "timed_out", log_dir=tmp_path)
    run = replace(run, **({} if cap == "timed_out" else {cap: True}), **taint)
    unclean = p2p.UNCLEAN[("survivors", "mutated", "forged").index(next(iter(taint)))]
    want = on_timeout if cap == "timed_out" else unclean
    monkeypatch.setattr(p2p, "run_verifiers", lambda *args, **kwargs: (run,))
    got = p2p._probe(TASK, ("tests/test_a.py",), RESULT, context(TASK, tmp_path), 30)
    assert got == (None, want), got
