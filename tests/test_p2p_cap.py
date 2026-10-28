"""#333: module-dir caps a large test directory to the files named after the change.

On a repo with large flat test directories, module-dir selected the whole directory (5,498 tests
in the expansion trial), so the base probe timed out and the task ended UNCOMPARABLE. A directory
with more than MODULE_DIR_CAP test files now contributes only its name-matched files; a smaller
one is still selected whole (#117: a flat layout brings in the sibling of the matched test). Every
cut is recorded, so "0 P2P tests" never reads as "P2P passed".
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_p2p_plan import RESULT, TASK, plan, uncomparable

from cairnwork import p2p
from cairnwork.p2p import P2PPlan, run_plan
from cairnwork.p2p_selection import MODULE_DIR_CAP, cap_note
from cairnwork.p2p_selection import select_p2p_targets as _select

ROOTS = ("tests",)


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
    runs, rule = run_plan(got, TASK, "c" * 40, tmp_path, None, (None, None))
    assert runs == () and rule["capped_dirs"] == ["tests"]  # what the result's "p2p" shows


def test_a_degraded_cut_to_nothing_is_uncomparable_and_names_the_cut(monkeypatch):
    got, _ = plan(monkeypatch, flat(MODULE_DIR_CAP + 1), [(None, "base-probe-timeout")])
    assert uncomparable(got)
    assert "capped: tests" in str(got) and f"over {MODULE_DIR_CAP}" in str(got)


@pytest.mark.parametrize("count", [MODULE_DIR_CAP, MODULE_DIR_CAP + 1])
def test_base_env_check_probes_the_capped_selection(monkeypatch, tmp_path, count):
    existing = flat(count, "test_b.py")
    probed = []

    def fake_probe(task, files, run_root, result, verifying, budget, tag="p"):
        probed.append(files)
        return {}, ""

    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: existing)
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    monkeypatch.setattr(p2p, "_env_fingerprint", lambda python: None)
    task = SimpleNamespace(**{**vars(TASK), "allowed_files": ("src/pkg/b.py",)})
    task.p2p_mode, task.test_commit, task.p2p_budget = "module-dir", "t" * 40, 30
    p2p.base_env_check(task, tmp_path, None, (None, None))
    assert probed == [existing if count == MODULE_DIR_CAP else ("tests/test_b.py",)]


def _cap_task(monkeypatch, existing, changed):
    """A module-dir task over ``existing`` base tests (the real ``exclude`` rule), probes stubbed
    to pass the first file; returns (task, the file sets probed)."""
    probed: list[tuple[str, ...]] = []

    def fake_probe(task, files, run_root, result, verifying, budget, tag="p"):
        probed.append(files)
        return {f"{files[0]}::t": "passed"}, ""

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
    p2p.base_env_check(task, tmp_path, RESULT, (None, None))
    got = p2p.plan_p2p(task, (), ("tests/test_d.py",), "module-dir", 30, tmp_path, RESULT,
                       (None, None))  # fmt: skip
    assert probed == [("tests/test_b.py",), ("tests/test_b.py",)]
    assert got.rule["capped_dirs"] == ["tests"] and got.rule["selected_files"] == 1


def test_a_deferred_test_is_dropped_from_the_selection_it_maps_to(monkeypatch, tmp_path):
    existing = flat(MODULE_DIR_CAP, "test_b.py", "test_d.py")
    task, probed = _cap_task(monkeypatch, existing, ("src/pkg/d.py",))
    got = p2p.plan_p2p(task, (), ("tests/test_d.py",), "module-dir", 30, tmp_path, RESULT,
                       (None, None))  # fmt: skip
    assert probed == [tuple(p for p in existing if p != "tests/test_d.py")]
    assert "capped_dirs" not in got.rule
