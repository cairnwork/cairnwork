"""Bounded, non-leaking wording for failing visible tests (#1186); the verdict never moves."""

import sys
from types import SimpleNamespace

import pytest
from conftest import commit_change, git

from cairnwork import verifiers
from cairnwork.verifier_failure_summary import (
    assertion_summary,
    format_failure,
    with_failure_detail,
)
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

VIS = "tests/test_visible.py::test_bad"
SECRET = "tests/test_secret.py::test_secret"
LOG = "\n".join(
    [
        "=== FAILURES ===",
        "___ test_bad ___",
        "    def test_bad():",
        ">       source_line()",
        "E       assert 1 == 2",
        "=== short test summary info ===",
        "",
    ]
)


def _log(tmp_path, text=LOG):
    path = tmp_path / "pytest.log"
    path.write_text(text)
    return path


def test_visible_failure_gets_the_assertion_row_and_keeps_causes(tmp_path):
    out = format_failure("pytest exit code 1", {VIS: "failed"}, _log(tmp_path), (), None)
    assert out == "pytest exit code 1; tests/test_visible.py::test_bad assertion: E assert 1 == 2"


def test_hidden_failure_adds_a_count_and_keeps_the_other_causes(tmp_path):
    outcomes = {VIS: "failed", SECRET: "failed"}
    why = "pytest exit code 1; operation not permitted"
    out = format_failure(why, outcomes, _log(tmp_path), {"tests/test_secret.py"}, None)
    assert out == why + "; deferred: 1 failed"


@pytest.mark.parametrize("state", ["failed", "skipped", "xfailed"])
def test_hidden_test_leaves_no_identifier_in_any_state(tmp_path, state):
    outcomes = {SECRET: state, VIS: "failed"}
    out = format_failure("exit 1", outcomes, _log(tmp_path), {"tests/test_secret.py"}, None)
    for word in ("test_secret", "assertion:", "test_visible"):
        assert word not in out


def test_canary_in_a_visible_assertion_is_withheld_when_the_run_holds_an_overlay(tmp_path):
    log = LOG.replace("assert 1 == 2", "canary-HIDDEN-42")
    outcomes = {VIS: "failed", SECRET: "passed"}
    out = format_failure("exit 1", outcomes, _log(tmp_path, log), {"tests/test_secret.py"}, None)
    assert out == "exit 1"
    with_assertions = {VIS: "E canary-HIDDEN-42"}
    out = format_failure("exit 1", outcomes, None, {"tests/test_secret.py"}, with_assertions)
    assert "canary" not in out and "assertion:" not in out


def test_overlay_among_the_run_files_withholds_even_when_it_has_no_outcome(tmp_path):
    out = format_failure(
        "exit 1", {VIS: "failed"}, _log(tmp_path), {"tests/test_secret.py"}, None,
        run_files=("tests/test_secret.py",),
    )  # fmt: skip
    assert out == "exit 1"


@pytest.mark.parametrize("overlay", [{"data/fixture.json"}, {"conftest.py"}, {"tests/other.py"}])
def test_any_overlay_file_withholds_even_when_pytest_never_names_it(tmp_path, overlay):
    """A test commit may hold data only; the whole commit is laid over the checkout (#1186)."""
    log = LOG.replace("assert 1 == 2", "canary-HIDDEN-42")
    out = format_failure("exit 1", {VIS: "failed"}, _log(tmp_path, log), overlay, None)
    assert out == "exit 1"
    out = format_failure("exit 1", {VIS: "failed"}, None, overlay, {VIS: "E canary-HIDDEN-42"})
    assert out == "exit 1"


def test_a_source_line_is_never_the_summary(tmp_path):
    out = format_failure(
        "x", {VIS: "failed"}, _log(tmp_path, LOG.replace("E       ", "")), (), None
    )
    assert out == "x"
    assert assertion_summary("source()\nE   boom") == "assertion: E boom"
    assert assertion_summary("source()") == "assertion detail unavailable"


@pytest.mark.parametrize(
    "assertions",
    [[], "text", {VIS: ["E x"]}, {VIS: 5}, {"nope": "E x"}, None],
)
def test_malformed_assertions_are_unrecorded(tmp_path, assertions):
    out = format_failure("exit 1", {VIS: "failed"}, None, (), assertions)
    assert out == "exit 1"


@pytest.mark.parametrize(
    "bad",
    ["‮evil", "a​b", "\x1b[31mred\x1b[0m", "a\x00b\x07c", "x]y;z:w", "##[error]x\nE y"],
)
def test_untrusted_text_is_cleaned(bad):
    out = assertion_summary("E " + bad)
    for char in "‮​\x1b\x00\x07[];":
        assert char not in out
    assert "\n" not in out and "31m" not in out and "##[" not in out


def test_long_input_is_clamped(tmp_path):
    assert len(assertion_summary("E " + "x" * 5000)) <= 160
    nodes = {f"tests/test_visible.py::{'n' * 300}{i}": "failed" for i in range(8)}
    log = "___ " + "n" * 300 + "0 ___\nE " + "y" * 5000 + "\n"
    out = format_failure("", nodes, _log(tmp_path, log), (), None)
    first, *rest = out.split("; ")
    assert len(first) <= 160 and first.count("n") <= 130
    assert out.endswith("+3 more") or len(rest) <= 5


@pytest.mark.parametrize("tail", ["]x", ";y", ":z", "\nq", "a::b", "‮"])
def test_node_ids_cannot_forge_sections(tmp_path, tail):
    node = "tests/test_visible.py::test_bad" + tail
    out = format_failure("exit 1", {node: "failed"}, None, (), {node: "E boom"})
    assert out.startswith("exit 1; tests/test_visible.py::test_bad")
    assert "\n" not in out and "[" not in out and "]" not in out and "‮" not in out
    assert out.count(";") == 1


def test_more_than_five_failures_are_counted(tmp_path):
    nodes = {f"tests/test_visible.py::test_{i}": "failed" for i in range(8)}
    assertions = {node: "E boom" for node in nodes}
    out = format_failure("", nodes, None, (), assertions)
    assert out.count("assertion:") == 5 and out.endswith("+3 more")


def test_task_context_without_a_test_commit_field_does_not_raise(tmp_path):
    ctx = SimpleNamespace()  # no repo, base_commit or test_commit at all
    spec = SimpleNamespace(targets=())
    verdict = SimpleNamespace(outcomes={VIS: "failed"}, collected=())
    log = _log(tmp_path)
    assert with_failure_detail("w", ctx, spec, verdict, log).startswith("w; ")
    assert with_failure_detail("w", SimpleNamespace(test_commit="x"), spec, verdict, log) == "w"
    assert with_failure_detail("w", ctx, spec, None, log) == "w"


def test_overlay_files_come_from_the_test_commit_and_an_unreadable_one_adds_nothing(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    hidden = commit_change(repo, {"tests/test_secret.py": "def test_secret(): assert 0\n"})
    verdict = SimpleNamespace(outcomes={VIS: "failed", SECRET: "failed"}, collected=())
    spec = SimpleNamespace(targets=())
    ctx = VerifyContext(repo, tmp_path, base_commit=base, test_commit=hidden)
    out = with_failure_detail("w", ctx, spec, verdict, _log(tmp_path))
    assert out == "w; deferred: 1 failed"
    gone = VerifyContext(repo, tmp_path, base_commit=base, test_commit="0" * 40)
    assert with_failure_detail("w", gone, spec, verdict, _log(tmp_path)) == "w"


@pytest.mark.sandbox
class TestEndToEnd:
    """The real run_verifiers: the verdict fields are the ones of main, only the reason grows."""

    def _repo(self, repo):
        visible = "def test_bad():\n    x = 1\n    assert x == 2, 'canary-HIDDEN-42'\n"
        commit_change(repo, {"tests/test_visible.py": visible})
        base = git(repo, "rev-parse", "HEAD")
        hidden = commit_change(repo, {"tests/test_secret.py": "def test_secret(): assert 0\n"})
        return base, hidden

    def _run(self, repo, tmp_path, commit, test_commit, targets, plain):
        spec = parse_verifier("pytest", ["python3", "-m", "pytest", *targets])
        base = git(repo, "rev-parse", "HEAD~1")
        ctx = VerifyContext(
            repo, tmp_path / "runs", python=sys.executable, base_commit=base,
            test_commit=test_commit,
        )  # fmt: skip
        if plain:
            return run_verifiers(ctx, commit, (spec,), "r1")[0]
        return run_verifiers(ctx, commit, (spec,), "r2")[0]

    def _pair(self, repo, tmp_path, monkeypatch, test_commit, targets):
        head = git(repo, "rev-parse", "HEAD")
        after = self._run(repo, tmp_path, head, test_commit, targets, False)
        monkeypatch.setattr(verifiers, "with_failure_detail", lambda why, *rest: why)
        before = self._run(repo, tmp_path, head, test_commit, targets, True)
        fields = lambda r: (  # noqa: E731
            r.observation.passed, r.observation.verifier_id, r.returncode, r.ran,
            r.plain_failure, r.outcomes,
        )  # fmt: skip
        assert fields(before) == fields(after) and not after.observation.passed
        assert after.why.startswith(before.why)
        return before, after

    def test_visible_failure_only(self, repo, tmp_path, monkeypatch):
        self._repo(repo)
        before, after = self._pair(repo, tmp_path, monkeypatch, None, ["tests/test_visible.py"])
        assert "canary-HIDDEN-42" in after.why and after.why != before.why

    def test_run_with_hidden_test_gets_only_a_count(self, repo, tmp_path, monkeypatch):
        base, hidden = self._repo(repo)
        targets = ["tests/test_visible.py", "tests/test_secret.py"]
        before, after = self._pair(repo, tmp_path, monkeypatch, hidden, targets)
        assert after.why == before.why + "; deferred: 1 failed"
        for word in ("canary", "test_secret", "assertion:"):
            assert word not in after.why
