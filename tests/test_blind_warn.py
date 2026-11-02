"""experimental/blind_warn: two blind tests as a warning line, never a verdict.

The model is a fake that returns fixed test files; the verifier run is faked for the rule cases
and real (sandbox) for one end-to-end case. Every failure must give warn=false and no exception.
"""

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import cast

import pytest
from conftest import commit_change, git

from cairnwork.verifiers import VerifierRun, run_verifiers
from experimental.blind_warn import cli
from experimental.blind_warn.judge import classify, run_blind_warn

PATCH_X2 = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -1 +1 @@\n-x = 1\n+x = 2\n"
)
PASS_TEST = "def test_ok():\n    assert True\n"
FAIL_TEST = "def test_x():\n    assert 1 == 2\n\ndef test_y():\n    assert False\n"


def fake_model(*replies):
    """A model that answers: conventions first, then A, then B (replies are test file texts)."""
    queue = ["- no basis"] + [f"```python\n{text}```" for text in replies]

    def call(prompt, model):
        return queue.pop(0), 0.01

    return call


def run_of(outcomes, rc=1, collect=()):
    fake = SimpleNamespace(returncode=rc, outcomes=outcomes, collect_errors=tuple(collect))
    return cast(VerifierRun, fake)


def runner_with(base, cand):
    """base/cand: {'a': run, 'b': run}; the run id ends in -b (base) or -c (candidate)."""

    def runner(ctx, commit, specs, run_id, **kwargs):
        table = base if run_id.endswith("-b") else cand
        return tuple(table[spec.verifier_id] for spec in specs)

    return runner


@pytest.fixture
def case(repo, tmp_path):
    (repo / "ISSUE.md").write_text("Title\nfoo is wrong\n")
    commit_change(repo, {"ISSUE.md": "Title\nfoo is wrong\n"})
    (repo / "src" / "a.py").write_text("x = 2\n")
    patch = tmp_path / "cand.patch"
    patch.write_text(git(repo, "diff") + "\n")
    git(repo, "checkout", "-q", "--", ".")
    state = tmp_path / "state"
    state.mkdir()
    return SimpleNamespace(ticket=repo / "ISSUE.md", base=repo, patch=patch, state=state)


def go(case, model, runner):
    return run_blind_warn(
        case.ticket, case.base, case.patch, case.state, call_model=model, runner=runner
    )


NODE = "tests/test_blind_a.py::"
FAILED = {NODE + "test_x": "failed", NODE + "test_y": "failed"}
GOOD = run_of({NODE + "test_x": "passed", NODE + "test_y": "passed"}, rc=0)


def test_both_fail_gives_a_warning_with_both_names(case):
    cand = run_of(FAILED)
    base = run_of({NODE + "test_x": "passed", NODE + "test_y": "passed"}, rc=0)
    result = go(
        case,
        fake_model(FAIL_TEST, FAIL_TEST),
        runner_with({"a": base, "b": base}, {"a": cand, "b": cand}),
    )
    assert result["warn"] is True and "error" not in result, result.get("error")
    assert "test_x" in result["line"] and "test_y" in result["line"]
    assert result["line"].endswith("not a verdict")
    assert result["cost_usd"] == pytest.approx(0.03)


def test_only_a_failing_is_no_warning(case):
    base = {"a": GOOD, "b": GOOD}
    result = go(
        case, fake_model(FAIL_TEST, PASS_TEST), runner_with(base, {"a": run_of(FAILED), "b": GOOD})
    )
    assert result["warn"] is False and result["line"] == ""
    assert result["a"]["failed"] == ["test_x", "test_y"] and result["b"]["failed"] == []


def test_failure_that_is_a_collection_error_on_base_is_undecided(case):
    broken_base = run_of({}, rc=2, collect=["tests/test_blind_a.py"])
    base = {"a": broken_base, "b": GOOD}
    result = go(
        case,
        fake_model(FAIL_TEST, FAIL_TEST),
        runner_with(base, {"a": run_of(FAILED), "b": run_of(FAILED)}),
    )
    assert result["warn"] is False
    assert result["a"]["failed"] == [] and result["a"]["undecided"] == ["test_x", "test_y"]


def test_candidate_not_collected_like_base_is_undecided(case):
    empty = run_of({}, rc=5)
    result = go(
        case,
        fake_model(FAIL_TEST, FAIL_TEST),
        runner_with({"a": empty, "b": empty}, {"a": empty, "b": empty}),
    )
    assert result["warn"] is False and result["a"]["undecided"] == ["(file)"]


def test_model_exception_is_an_error_not_a_raise(case):
    def boom(prompt, model):
        raise OSError("network down")

    result = go(case, boom, runner_with({}, {}))
    assert result["warn"] is False and result["line"] == ""
    assert "network down" in result["error"]


def test_missing_key_returns_error_without_connecting(case, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    result = run_blind_warn(case.ticket, case.base, case.patch, case.state)
    assert result["warn"] is False and "ANTHROPIC_API_KEY" in result["error"]


def test_a_skipped_reply_is_an_error_naming_the_missing_file(case):
    calls = iter(["- no basis", "skipped unclear", "```python\n" + PASS_TEST + "```"])
    result = go(case, lambda p, m: (next(calls), 0.0), runner_with({}, {}))
    assert result["warn"] is False and "a" in result["error"]


def test_classify_counts_only_functions_collected_on_base():
    cand = run_of({"f.py::test_new": "failed", "f.py::test_old": "failed"})
    base = run_of({"f.py::test_old": "passed"}, rc=0)
    assert classify(cand, base) == (["test_old"], ["test_new"], 2)


def test_cli_prints_one_json_object_and_exits_zero(case, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "r.json"
    code = cli.main(
        ["--ticket", str(case.ticket), "--base", str(case.base), "--candidate-diff",
         str(case.patch), "--state", str(case.state), "--out", str(out)]
    )  # fmt: skip
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed == json.loads(out.read_text()) and printed["warn"] is False


def test_core_does_not_import_the_package():
    # the enforcer is lint-imports ("Core must not import experimental"); this checks the runtime
    code = "import sys, cairnwork.intake; print(any(m.startswith('experimental') for m in sys))"
    code = code.replace("in sys)", "in sys.modules)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"


def test_tracked_symlink_to_an_outside_file_is_never_sent(case, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-TEXT")
    (case.base / "docs.md").symlink_to(outside)
    commit_change(case.base, {})
    prompts = []

    def spy(prompt, model):
        prompts.append(prompt)
        return "```python\n" + PASS_TEST + "```", 0.0

    go(case, spy, runner_with({}, {}))
    assert prompts and not any("OUTSIDE-SECRET-TEXT" in p for p in prompts)


@pytest.mark.parametrize("reply", ["OK", "", "ok."])
def test_a_conventions_reply_of_only_ok_is_an_error(case, reply):
    prompts = []

    def call(prompt, model):
        prompts.append(prompt)
        return reply, 0.0

    result = go(case, call, runner_with({}, {}))
    assert result["warn"] is False and "conventions" in result["error"]
    assert len(prompts) == 1 and "in your reply" in prompts[0]


def test_the_clone_is_removed_after_a_run(case):
    go(
        case,
        fake_model(PASS_TEST, PASS_TEST),
        runner_with({"a": GOOD, "b": GOOD}, {"a": GOOD, "b": GOOD}),
    )
    assert list((case.state / "blind_warn").iterdir()) == []


def link_patch(tmp_path, case, link_path, target):
    """A patch (made in a scratch commit) that adds a symlink at link_path pointing to target."""
    repo = case.base
    (repo / link_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / link_path).symlink_to(target)
    git(repo, "add", "-A")
    case.patch.write_text(git(repo, "diff", "--cached") + "\n")
    git(repo, "reset", "-q", "--hard")
    git(repo, "clean", "-fdq")


@pytest.mark.parametrize("kind", ["file", "dir"])
def test_a_link_at_the_test_path_is_refused_and_writes_nothing_outside(case, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "file":
        victim = outside / "victim.py"
        victim.write_text("ORIGINAL")
        link_patch(tmp_path, case, "tests/test_blind_a.py", victim)
    else:
        victim = outside / "test_blind_a.py"
        link_patch(tmp_path, case, "tests", outside)
    result = go(case, fake_model(PASS_TEST, PASS_TEST), runner_with({}, {}))
    assert result["warn"] is False and "unsafe test path" in result["error"]
    assert not victim.exists() or victim.read_text() == "ORIGINAL"
    assert sorted(p.name for p in outside.iterdir()) in ([], ["victim.py"])


@pytest.mark.sandbox
def test_end_to_end_through_the_real_verifier(case):
    (case.state / "config.json").write_text(
        json.dumps({"verifier_account": False, "verifier_python": sys.executable})
    )
    assert_a = "from src import a\n\ndef test_val():\n    assert a.x == 1\n"
    # the candidate patch sets x = 2, so both blind tests that assert x == 1 fail on it
    conftest = (
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
    )
    commit_change(case.base, {"tests/conftest.py": conftest})
    case.patch.write_text(PATCH_X2)
    result = go(case, fake_model(assert_a, assert_a), run_verifiers)
    assert result.get("error") is None, result
    assert result["warn"] is True and "test_val" in result["line"]


def _spy_prompts(case):
    prompts = []

    def spy(prompt, model):
        prompts.append(prompt)
        return "```python\n" + PASS_TEST + "```", 0.0

    go(case, spy, runner_with({}, {}))
    return "\n".join(prompts)


@pytest.mark.parametrize("name", ["說明文件.md", 'say "hi".md', "tab\tname.md"])
def test_files_with_quoted_or_non_ascii_names_are_listed(case, name):
    (case.base / name).write_text("MARKER-" + str(abs(hash(name))))
    commit_change(case.base, {})
    assert f"--- FILE: {name}\n" in _spy_prompts(case)


def test_uncommitted_file_and_index_link_are_not_sent(case, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-TEXT")
    (case.base / "draft.md").write_text("UNCOMMITTED-TEXT")
    git(case.base, "add", "draft.md")  # staged only, not in HEAD
    (case.base / "link.md").symlink_to(outside)
    git(case.base, "add", "link.md")
    text = _spy_prompts(case)
    assert "UNCOMMITTED-TEXT" not in text and "OUTSIDE-SECRET-TEXT" not in text
    assert "FILE: draft.md" not in text and "FILE: link.md" not in text


def test_a_non_utf8_name_is_skipped_and_other_files_are_listed(case):
    (case.base / "ok_doc.md").write_text("OK-TEXT")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=case.base, input=b"LATIN-TEXT",
        capture_output=True, check=True,
    ).stdout.strip().decode()  # fmt: skip
    entry = f"100644,{blob},".encode() + b"caf\xe9.md"  # not valid UTF-8; some systems refuse it
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", entry],
        cwd=case.base, check=True,
    )  # fmt: skip
    git(case.base, "add", "ok_doc.md")
    git(case.base, "commit", "-q", "-m", "non-utf8 name")
    text = _spy_prompts(case)
    assert "--- FILE: ok_doc.md\n" in text and "LATIN-TEXT" not in text


def test_a_link_committed_in_head_is_not_listed_or_sent(case, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-TEXT")
    (case.base / "link.md").symlink_to(outside)
    commit_change(case.base, {})
    text = _spy_prompts(case)
    assert "FILE: link.md" not in text and "OUTSIDE-SECRET-TEXT" not in text


def test_head_link_with_a_regular_file_in_the_index_is_not_sent(case, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-TEXT")
    (case.base / "swap.md").symlink_to(outside)
    commit_change(case.base, {})
    (case.base / "swap.md").unlink()
    (case.base / "swap.md").write_text("INDEX-ONLY-TEXT")
    git(case.base, "add", "swap.md")
    text = _spy_prompts(case)
    assert "INDEX-ONLY-TEXT" not in text and "FILE: swap.md" not in text


def test_a_file_swapped_for_an_outside_link_in_the_work_tree_sends_the_head_blob(case, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-TEXT")
    (case.base / "swap2.md").write_text("HEAD-BLOB-TEXT")
    commit_change(case.base, {})
    (case.base / "swap2.md").unlink()
    (case.base / "swap2.md").symlink_to(outside)
    text = _spy_prompts(case)
    assert "HEAD-BLOB-TEXT" in text and "OUTSIDE-SECRET-TEXT" not in text
