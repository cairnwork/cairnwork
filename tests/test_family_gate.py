"""The experimental family gate (#374): verdicts, refusal, sidecar, off = the plain runner."""

import json
import os
import re
import stat

import pytest
from test_issue_runner import BLOCK, FakeGitHub, issue, setup  # noqa: F401  (fixture)

import cairnwork.issue_runner as runner
from experimental.family_gate import __main__ as cli
from experimental.family_gate.families import (
    NOT_ANNOTATED,
    PASS,
    REFUSED,
    UNCOMPARABLE,
    decide,
    family,
    parse_annotation,
)
from experimental.family_gate.gate import HOLD, Sidecar


def annotated(number=7, families=None, raw=None):
    item = issue(number)
    text = raw if raw is not None else json.dumps(families)
    item["body"] += f"\n\n```cairnwork-families\n{text}\n```"
    return item


def gate_pass(setup, github, tmp_path, model="gemini-3.8-flash-high"):  # noqa: F811
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    result = cli.one_pass(
        setup["intake"], github, setup["clone"], {"worker_model": model, "sidecar": sidecar}
    )
    rows = [json.loads(x) for x in sidecar.path.read_text().splitlines()]
    return result, rows, sidecar


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("claude-opus-5-5", "anthropic"),
        ("Opus", "anthropic"),
        ("sonnet", "anthropic"),
        ("gpt-5.4", "openai"),
        ("o3", "openai"),
        ("gemini-3.8-flash-high", "google"),
        ("gemma-4", "google"),
        ("glm-5.3-flash", "zhipu"),
        ("openai", "openai"),
        ("splash-35b", None),
        ("o3xyz-local", None),
        ("gptq-llama", None),
        ("", None),
    ],
)
def test_the_family_table_is_fixed(value, expected):
    assert family(value) == expected


def test_a_different_family_passes_and_is_recorded(setup, tmp_path):  # noqa: F811
    github = FakeGitHub(
        [annotated(families={"issue_author": "claude-opus-5-5", "test_author": "gpt-5.4"})]
    )
    result, rows, _ = gate_pass(setup, github, tmp_path)
    assert result["gate"]["counts"] == {PASS: 1} and result["take"][0]["duplicate"] is False
    assert github.labels[7] == {runner.RUNNING} and len(setup["started"]) == 1
    (row,) = rows
    assert row["verdict"] == PASS and row["issue"] == 7
    assert row["issue_author"] == {"declared": "claude-opus-5-5", "family": "anthropic"}
    assert row["test_author"] == {"declared": "gpt-5.4", "family": "openai"}
    assert row["worker"] == {"model": "gemini-3.8-flash-high", "family": "google"}
    known = json.loads(next((setup["intake"].state / "issues").iterdir()).read_text())
    assert row["block_sha256"] == known["block_sha256"]  # joinable to the task core records


@pytest.mark.parametrize(
    ("model", "families", "code"),
    [
        ("claude-sonnet-5-5", {"issue_author": "opus"}, "FAMILY_SAME_AS_ISSUE_AUTHOR"),
        ("claude-sonnet-5-5", {"test_author": "claude-haiku-5-5"}, "FAMILY_SAME_AS_TEST_AUTHOR"),
        ("gemini-3.8-flash-high", {"issue_author": "gemma-4"}, "FAMILY_SAME_AS_ISSUE_AUTHOR"),
        (
            "gpt-5.4",
            {"issue_author": "splash-35b", "test_author": "codex"},
            "FAMILY_SAME_AS_TEST_AUTHOR",
        ),
    ],
)
def test_a_same_family_author_is_refused_before_take(setup, tmp_path, model, families, code):  # noqa: F811
    github = FakeGitHub([annotated(families=families), issue(number=8)])
    result, rows, _ = gate_pass(setup, github, tmp_path, model)
    assert result["gate"]["refused"] == [7]
    assert github.labels[7] == {HOLD} and github.labels[8] == {runner.RUNNING}
    assert [r["issue"] for r in result["take"]] == [8] and len(setup["started"]) == 1
    ((number, text),) = [c for c in github.comments if c[0] == 7]
    assert number == 7 and code in text and model in text
    assert rows[0]["verdict"] == REFUSED and rows[0]["reason"] == code


@pytest.mark.parametrize(
    ("body", "verdict", "reason"),
    [
        ({"issue_author": "splash-35b"}, UNCOMPARABLE, "AUTHOR_FAMILY_UNKNOWN"),
        (None, NOT_ANNOTATED, "FAMILY_BLOCK_ABSENT"),
        ("[1]", UNCOMPARABLE, "FAMILY_BLOCK_INVALID"),
        ('{"issue_author": "gpt", "model": "x"}', UNCOMPARABLE, "FAMILY_BLOCK_INVALID"),
        ('{"issue_author": 3}', UNCOMPARABLE, "FAMILY_BLOCK_INVALID"),
        ("{not json", UNCOMPARABLE, "FAMILY_BLOCK_INVALID"),
    ],
)
def test_unknown_or_missing_families_are_counted_apart_and_taken(
    setup,  # noqa: F811
    tmp_path,
    body,
    verdict,
    reason,
):
    if body is None:
        item = issue()
    elif isinstance(body, dict):
        item = annotated(families=body)
    else:
        item = annotated(raw=body)
    github = FakeGitHub([item])
    result, rows, _ = gate_pass(setup, github, tmp_path, "claude-opus-5-5")
    assert result["gate"]["counts"] == {verdict: 1} and result["gate"]["refused"] == []
    assert rows[0]["reason"] == reason and github.labels[7] == {runner.RUNNING}
    assert [c for c in github.comments if "家族閘門" in c[1]] == []


def test_two_blocks_are_invalid_and_never_refuse():
    one = '```cairnwork-families\n{"issue_author": "opus"}\n```'
    twice = parse_annotation(f"{one}\n{one}")
    assert twice is not None and twice.invalid
    assert decide(twice, "claude-opus-5-5").verdict == UNCOMPARABLE


def test_an_unknown_worker_family_cannot_be_compared():
    verdict = decide(parse_annotation('```cairnwork-families\n{"issue_author": "opus"}\n```'), "x")
    assert (verdict.verdict, verdict.reason) == (UNCOMPARABLE, "WORKER_FAMILY_UNKNOWN")


def test_untrusted_or_core_refused_issues_are_left_to_core(setup, tmp_path):  # noqa: F811
    untrusted = FakeGitHub([annotated(families={"issue_author": "gemini"})], trusted="UNTRUSTED")
    sidecar = Sidecar(tmp_path / "sidecar", untrusted.repo)
    gate = {"worker_model": "gemini-3.8-flash-high", "sidecar": sidecar}
    result = cli.one_pass(setup["intake"], untrusted, setup["clone"], gate)
    assert result["take"] == [{"issue": 7, "skipped": "UNTRUSTED"}] and untrusted.comments == []
    assert untrusted.labels[7] == {runner.QUEUE} and not sidecar.path.exists()
    bad = annotated(families={"issue_author": "gemini"})
    bad["body"] = bad["body"].replace('"src/calc.py"', '"pyproject.toml"')
    core_refused = FakeGitHub([bad])
    result = cli.one_pass(setup["intake"], core_refused, setup["clone"], gate)
    assert result["gate"]["counts"] == {} and len(core_refused.comments) == 1  # core's, once
    assert "TASK_ALLOWED_FILES_PROTECTED" in result["take"][0]["refused"]


def test_off_is_the_plain_runner(setup, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.delenv(cli.ENV, raising=False)
    assert not cli.enabled(False) and cli.enabled(True)
    monkeypatch.setenv(cli.ENV, "on")
    assert cli.enabled(False)
    calls = []

    class Counting(FakeGitHub):
        def issues(self, label):
            calls.append(label)
            return super().issues(label)

    github = Counting([annotated(families={"issue_author": "gemini"})])
    result = cli.one_pass(setup["intake"], github, setup["clone"], None)
    assert "gate" not in result and calls == [runner.QUEUE, runner.RUNNING]
    assert github.labels[7] == {runner.RUNNING}  # same family, but the gate is off
    assert not (tmp_path / "sidecar").exists()


def test_a_second_pass_records_nothing_new(setup, tmp_path):  # noqa: F811
    github = FakeGitHub([annotated(families={"issue_author": "opus"})])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    gate = {"worker_model": "claude-opus-5-5", "sidecar": sidecar}
    first = cli.one_pass(setup["intake"], github, setup["clone"], gate)
    github.labels[7] = {runner.QUEUE}  # re-queued unchanged
    second = cli.one_pass(setup["intake"], github, setup["clone"], gate)
    assert first["gate"]["recorded"] == 1 and second["gate"]["recorded"] == 0
    assert len(sidecar.path.read_text().splitlines()) == 1
    assert stat.S_IMODE(os.stat(sidecar.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sidecar.dir).st_mode) == 0o700
    assert not any("family" in p.name for p in setup["intake"].state.rglob("*"))


def test_a_github_error_while_refusing_holds_the_issue(setup, tmp_path):  # noqa: F811
    class Flaky(FakeGitHub):
        def relabel(self, number, add, remove):
            if add == HOLD:
                raise RuntimeError("gh issue failed: 502")
            super().relabel(number, add, remove)

    github = Flaky([annotated(families={"issue_author": "opus"})])
    result, _, _ = gate_pass(setup, github, tmp_path, "claude-opus-5-5")
    assert "502" in result["gate"]["errors"][0]["error"]
    assert result["take"] == [] and setup["started"] == []
    assert github.labels[7] == {runner.QUEUE}  # left queued for the next pass


def test_an_unwritable_sidecar_stops_take_not_settle(setup, tmp_path):  # noqa: F811
    (tmp_path / "sidecar").write_text("a file, not a directory")
    github = FakeGitHub([annotated(families={"issue_author": "opus"})])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    gate = {"worker_model": "claude-opus-5-5", "sidecar": sidecar}
    result = cli.one_pass(setup["intake"], github, setup["clone"], gate)
    assert result["gate"]["error"].startswith("GATE_NOT_RUN")
    assert result["take"] == [] and result["settle"] == []
    assert github.labels[7] == {runner.QUEUE} and github.comments == []


def test_the_worker_model_defaults_to_the_state_config(setup, tmp_path):  # noqa: F811
    (setup["intake"].state / "config.json").write_text('{"model": "claude-opus-5-5"}')
    github = FakeGitHub([annotated(families={"issue_author": "sonnet"})])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    result = cli.one_pass(
        setup["intake"], github, setup["clone"], {"worker_model": "", "sidecar": sidecar}
    )
    assert result["gate"]["refused"] == [7]


def test_the_cli_holds_the_runner_lock(setup, tmp_path, monkeypatch, capsys):  # noqa: F811
    import fcntl

    monkeypatch.setattr(cli, "verify_origin", lambda clone, repo: None)
    monkeypatch.setattr(cli, "require_bot_gh", lambda gh, repo, cwd: None)
    setup["intake"].prepare()
    fd = os.open(setup["intake"].state / "issues.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code = cli.main(
            [
                "--state",
                str(setup["intake"].state),
                "--repo",
                "James3014/demo",
                "--clone",
                str(setup["clone"]),
                "--gh",
                "/bin/nexus-agent-gh",
                "--sidecar",
                str(tmp_path / "sidecar"),
                "--enable",
                "run-once",
            ]  # fmt: skip
        )
    finally:
        os.close(fd)
    assert code == 2 and "BUSY" in capsys.readouterr().out


def test_off_matches_the_core_runner_main(setup, tmp_path, monkeypatch, capsys):  # noqa: F811
    # Guard for fd31's scope: the CLI copies only main's start checks and lock; when core's
    # main changes what a pass does, this comparison goes red.
    from cairnwork.intake import Intake

    monkeypatch.delenv(cli.ENV, raising=False)
    hubs = []

    def fake_hub(gh, repo):
        hubs.append(FakeGitHub([annotated(families={"issue_author": "gemini"}), issue(8)]))
        return hubs[-1]

    for module in (runner, cli):
        monkeypatch.setattr(module, "GitHub", fake_hub)
    monkeypatch.setattr(cli, "verify_origin", lambda clone, repo: None)
    monkeypatch.setattr(cli, "require_bot_gh", lambda gh, repo, cwd: None)
    other = Intake(tmp_path / "state2")
    other.set_accepting(True)
    outputs = []
    for main, state in ((runner.main, setup["intake"].state), (cli.main, other.state)):
        argv = ["--state", str(state), "--repo", "James3014/demo", "--clone", str(setup["clone"]),
                "--gh", "/bin/nexus-agent-gh", "run-once"]  # fmt: skip
        code = main(argv)
        out = json.loads(capsys.readouterr().out)
        for row in out["take"]:
            row.pop("task_id", None)
        outputs.append((code, out))
    assert outputs[0] == outputs[1] and outputs[0][0] == 0
    assert hubs[0].labels == hubs[1].labels
    task = re.compile(r"task-[0-9a-f]{32}")
    assert [(n, task.sub("T", t)) for n, t in hubs[0].comments] == [
        (n, task.sub("T", t)) for n, t in hubs[1].comments
    ]
