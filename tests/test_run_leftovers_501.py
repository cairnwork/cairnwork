"""#501: leftover run directories must not collide with a P2P fallback or escape classification."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from p2p_side_stub import stub_side

from cairnwork import p2p, proc_cwd, recovery
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.verify_context import VerifyContext
from cairnwork.workspace import WorkspaceError


def test_module_dir_fallback_uses_a_distinct_probe_tag(monkeypatch, tmp_path):
    task = SimpleNamespace(
        repo="/repo",
        base_commit="b" * 40,
        test_roots=("tests",),
        import_paths=(),
        allowed_files=("src/calc.py",),
        p2p_budget=30,
    )
    result = SimpleNamespace(attempt_id="a0123456789abcdef", logs=[])
    files = ("tests/a/test_calc.py", "tests/z/test_misc.py")
    monkeypatch.setattr(p2p, "_base_tests", lambda *_args, **_kwargs: files)
    monkeypatch.setattr(
        p2p,
        "select_p2p_targets",
        lambda _changed, _existing, _roots, mode, _rule: (
            files if mode == "whole-suite" else files[:1]
        ),
    )
    tags = []

    def probe(_task, selected, _result, _ctx, _budget, tag="p"):
        tags.append((selected, tag))
        return (
            (None, "base-probe-timeout")
            if len(tags) == 1
            else ({"tests/a/test_calc.py::test_x": "passed"}, "")
        )

    monkeypatch.setattr(p2p, "_probe", probe)
    stub_side(monkeypatch)
    try:
        p2p.plan_p2p(
            task,
            (),
            (),
            "whole-suite",
            30,
            result,
            VerifyContext.of(task, tmp_path, result.attempt_id),
        )
    except BaseUncomparable:
        pass
    assert [tag for _, tag in tags] == ["p", "m"]


def test_p2p_tag_literals_are_in_shared_set_and_match_attempt_dir_pattern():
    source = Path(p2p.__file__).read_text()
    tree = ast.parse(source)
    tags = {
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "tag"
        and isinstance(kw.value, ast.Constant)
        and isinstance(kw.value.value, str)
    }
    assert tags <= set(recovery._RUN_TAGS)
    pattern = re.compile(
        rf"a0123456789abcdef-[{re.escape(recovery._RUN_TAGS)}]-v([0-9]+|bb)(\.[^/]*)?"
    )
    assert all(pattern.fullmatch(f"a0123456789abcdef-{tag}-v0.work") for tag in tags)


def test_module_dir_leftover_is_found_by_recovery_and_proc_cwd(tmp_path):
    attempt = "a0123456789abcdef"
    root = tmp_path / "runs"
    leftover = root / f"{attempt}-m-v0.work"
    leftover.mkdir(parents=True)
    works, _, _ = recovery._run_dirs(root, attempt)
    assert leftover in works
    assert proc_cwd.attempt_dirs([root], attempt) == (leftover.resolve(),)


def test_preexisting_account_stage_is_a_workspace_error_with_path(tmp_path):
    from cairnwork.account_checkout import STAGE, make_account_checkout

    work = tmp_path / "work"
    stage = work / STAGE
    stage.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match=re.escape(str(stage))):
        make_account_checkout(None, "python", tmp_path, "b" * 40, work, "clone")
