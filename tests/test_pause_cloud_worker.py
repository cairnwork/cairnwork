"""PAUSE K arm's cloud worker (#476 step 2b), driven through a fake ``claude`` binary and a fake
``claude-api-run`` wrapper: no model is called and no API request is made."""

import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest

from cairnwork.confined import ATTEMPT_ENV
from experimental.pause.cloud_worker import (
    CloudWorker,
    CloudWorkerConfig,
    CloudWorkerError,
    CloudWorkerRefusal,
)

RUN = uuid.uuid4().hex[:8]  # the worker's sweep kills by attempt name: keep it unique per run
TASK = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))


def asst(mid, stop: str | None = "end_turn"):
    msg = {"id": mid, "model": "claude-sonnet-5-5", "content": []}
    if stop is not None:
        msg["stop_reason"] = stop
    return {"type": "assistant", "message": msg}


def result(**extra):
    return {"type": "result", "subtype": "success", "is_error": False, "result": "ok", **extra}


def fake(tmp_path, name, lines, exit_code=0, touch=None, sleep=0):
    """A fake binary that records argv and env, optionally edits a file, prints ``lines``."""
    seen = tmp_path / f"{name}.seen.json"
    path = tmp_path / name
    edit = f"open({touch!r}, 'w').write('x')" if touch else ""
    record = "json.dumps({'argv': sys.argv, 'env': dict(os.environ)})"
    path.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\n"
        f"open({str(seen)!r}, 'w').write({record})\n"
        f"{edit}\n"
        f"time.sleep({sleep})\n"
        f"for line in {lines!r}:\n    print(line if isinstance(line, str) else json.dumps(line))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path), seen


def clone(tmp_path, n):
    workdir = tmp_path / f"attempt{n}-{RUN}"
    workdir.mkdir()
    (workdir / "a.py").write_text("x = 1\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(workdir)]
    subprocess.run([*git[:5], "init", "-q"], check=True, cwd=workdir)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    return workdir


def run(tmp_path, n, lines, **kw):
    touch = kw.pop("touch_in_clone", False)
    workdir = clone(tmp_path, n)
    binary, seen = fake(
        tmp_path, "claude", lines, touch=str(workdir / "a.py") if touch else None, **kw
    )
    config = CloudWorkerConfig(claude=binary, wrap_api_run=False)
    return workdir, seen, config


def summary(workdir):
    return json.loads((workdir.parent / f"{workdir.name}.claude-run.json").read_text())


def test_model_and_effort_are_frozen():
    config = CloudWorkerConfig()
    assert (config.model, config.effort) == ("claude-sonnet-5-5", "medium")
    with pytest.raises(CloudWorkerError, match="frozen"):
        CloudWorkerConfig(model="claude-haiku-5-5")
    with pytest.raises(CloudWorkerError, match="frozen"):
        CloudWorkerConfig(effort="high")


def test_model_and_effort_are_sent_explicitly(tmp_path):
    workdir, seen, config = run(tmp_path, 1, [asst("m1"), result()])
    CloudWorker(config).run(workdir, TASK)
    argv = json.loads(seen.read_text())["argv"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "do not start subagents" in argv[argv.index("-p") + 1]


def test_the_preliminary_run_goes_through_the_api_wrapper_the_formal_one_does_not(tmp_path):
    workdir = clone(tmp_path, 2)
    claude, seen = fake(tmp_path, "claude", [asst("m1"), result()])
    wrapper = tmp_path / "claude-api-run"
    wrapper.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(tmp_path / 'wrap.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "i = sys.argv.index('--')\nos.execv(sys.argv[i + 1], sys.argv[i + 1:])\n"
    )
    wrapper.chmod(0o755)
    config = CloudWorkerConfig(claude=claude, api_run=str(wrapper))
    CloudWorker(config).run(workdir, TASK)
    wrapped = json.loads((tmp_path / "wrap.json").read_text())
    assert wrapped[:3] == ["--tag", "pause-cw-k", "--"]
    assert wrapped[3:5] == [claude, "-p"]
    assert summary(workdir)["wrapped"] is True
    formal = clone(tmp_path, 3)
    (tmp_path / "wrap.json").unlink()
    CloudWorker(CloudWorkerConfig(claude=claude, api_run=str(wrapper), wrap_api_run=False)).run(
        formal, TASK
    )
    assert not (tmp_path / "wrap.json").exists()
    assert summary(formal)["wrapped"] is False


def test_requests_are_counted_from_distinct_responses_and_retries(tmp_path):
    lines = [
        asst("m1", "tool_use"),
        asst("m1", "tool_use"),  # one response, two content blocks: one request
        {"type": "system", "subtype": "api_retry", "attempt": 1},  # sent, but no response
        asst("m2", "tool_use"),
        asst("m3"),
        result(num_turns=99),
    ]
    workdir, _, config = run(tmp_path, 4, lines)
    CloudWorker(config).run(workdir, TASK)
    got = summary(workdir)
    assert (got["request_count"], got["request_count_source"]) == (4, "stream_responses")


def test_a_plain_json_result_counts_its_turns(tmp_path):
    workdir, _, config = run(tmp_path, 5, [json.dumps(result(num_turns=3, stop_reason="end_turn"))])
    CloudWorker(config).run(workdir, TASK)
    got = summary(workdir)
    assert (got["request_count"], got["request_count_source"]) == (3, "num_turns")


def test_an_uncountable_run_says_so_and_never_guesses(tmp_path):
    workdir, _, config = run(tmp_path, 6, [result(stop_reason="end_turn")])
    CloudWorker(config).run(workdir, TASK)
    got = summary(workdir)
    assert got["request_count"] is None
    assert got["request_count_source"] == "UNCOUNTABLE"


def test_a_refusal_stop_reason_fails_the_run_and_is_not_resent(tmp_path):
    workdir, seen, config = run(tmp_path, 7, [asst("m1", "refusal"), result()])
    with pytest.raises(CloudWorkerRefusal) as caught:
        CloudWorker(config).run(workdir, TASK)
    assert caught.value.basis == "stop_reason"
    assert summary(workdir)["refusal"] == "stop_reason"


def test_without_a_stop_reason_a_single_request_that_changed_nothing_is_a_refusal(tmp_path):
    workdir, _, config = run(tmp_path, 8, [asst("m1", None), result()])
    with pytest.raises(CloudWorkerRefusal) as caught:
        CloudWorker(config).run(workdir, TASK)
    assert caught.value.basis == "inferred"


def test_without_a_stop_reason_an_edit_or_a_longer_run_is_not_a_refusal(tmp_path):
    workdir, _, config = run(tmp_path, 9, [asst("m1", None), result()], touch_in_clone=True)
    CloudWorker(config).run(workdir, TASK)
    assert summary(workdir)["refusal"] == ""
    workdir, _, config = run(tmp_path, 10, [asst("m1", None), asst("m2", None), result()])
    CloudWorker(config).run(workdir, TASK)
    assert summary(workdir)["refusal"] == ""


def test_a_failed_exit_or_an_error_result_fails_the_run(tmp_path):
    workdir, _, config = run(tmp_path, 11, [asst("m1")], exit_code=3)
    with pytest.raises(CloudWorkerError, match="exited 3"):
        CloudWorker(config).run(workdir, TASK)
    workdir, _, config = run(tmp_path, 12, [asst("m1"), result(is_error=True)])
    with pytest.raises(CloudWorkerError, match="error result"):
        CloudWorker(config).run(workdir, TASK)


def test_a_run_past_its_timeout_is_killed_and_fails(tmp_path):
    workdir, _, config = run(tmp_path, 13, [result()], sleep=60)
    short = CloudWorkerConfig(claude=config.claude, wrap_api_run=False, timeout_seconds=1)
    with pytest.raises(CloudWorkerError, match="timed out"):
        CloudWorker(short).run(workdir, TASK)


def test_only_the_needed_environment_reaches_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_FAKE_TOKEN", "secret-value")
    workdir, seen, config = run(tmp_path, 14, [asst("m1"), result()])
    CloudWorker(config).run(workdir, TASK)
    env = json.loads(seen.read_text())["env"]
    assert "CW_FAKE_TOKEN" not in env
    assert env[ATTEMPT_ENV] == workdir.name
    assert env["HOME"] == os.environ["HOME"]


def test_provenance_names_the_requested_model_and_the_observed_one_only_if_single(tmp_path):
    workdir, _, config = run(
        tmp_path, 15, [asst("m1"), result(modelUsage={"claude-sonnet-5-5": {}})]
    )
    prov = CloudWorker(config).run(workdir, TASK)
    assert prov == {
        "worker": "claude-cloud",
        "requested_model": "claude-sonnet-5-5",
        "observed_model": "claude-sonnet-5-5",
    }
    workdir, _, config = run(
        tmp_path, 16, [asst("m1"), result(modelUsage={"claude-sonnet-5-5": {}, "other": {}})]
    )
    assert CloudWorker(config).run(workdir, TASK)["observed_model"] == "unknown"
