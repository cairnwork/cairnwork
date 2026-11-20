"""Issue #1453: replay state-config provenance and Core refusal mapping."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_acceptance_casebook_replay import _args, _confirmed
from test_acceptance_casebook_replay_runner_e2e import (
    VERIFY,
    _git,
    _origin,
    _version_clone,
    core_http_server,
)

from experimental.acceptance_casebook import replay, replay_config
from experimental.acceptance_casebook.replay_runner import RealRunner

pytest_plugins = ["test_acceptance_casebook_origin"]


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_replay_records_state_config_presence_and_digest(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    first_config = tmp_path / "first.json"
    second_config = tmp_path / "second.json"
    secret = "https://core.example/SECRET-1453"
    first_config.write_text(json.dumps({"core_url": secret}))
    second_config.write_text(json.dumps({"core_url": "https://core.example/other"}))
    copied_state = tmp_path / "copied-state"
    copied_state.mkdir()
    copied_digest = RealRunner._copy_state_config(first_config, copied_state)
    assert copied_digest == hashlib.sha256((copied_state / "config.json").read_bytes()).hexdigest()
    monkeypatch.setattr(replay, "RUNNER", lambda *_: "accepted")

    output = Path(args[args.index("--out") + 1])
    assert replay.main(args) == 0
    without_config = _records(output)[0]
    args += ["--state-config", str(first_config)]
    args[args.index("--out") + 1] = str(tmp_path / "with-config.jsonl")
    assert replay.main(args) == 0
    with_first = _records(Path(args[args.index("--out") + 1]))[0]
    args[args.index("--state-config") + 1] = str(second_config)
    args[args.index("--out") + 1] = str(tmp_path / "other-config.jsonl")
    assert replay.main(args) == 0
    with_second = _records(Path(args[args.index("--out") + 1]))[0]

    assert without_config["state_config"] == "none"
    assert with_first["state_config"] == with_second["state_config"] == "given"
    # an injected runner copies nothing, so no digest may be claimed for it
    assert with_first["state_config_sha256"] is None
    assert with_second["state_config_sha256"] is None
    assert without_config["state_config_sha256"] is None
    assert secret not in json.dumps(with_first)


def _write_real_inputs(args: list[str]) -> None:
    inputs = Path(args[args.index("--inputs") + 1])
    data = json.loads(inputs.read_text())
    for row in data.values():
        row["verifier"] = [VERIFY]
    inputs.write_text(json.dumps(data))


def _real_args(
    tmp_path: Path, lg: dict[str, str], cases: list[dict[str, object]], repo: Path
) -> list[str]:
    args = _args(tmp_path, lg, cases)
    clone, version = _version_clone(tmp_path)
    registry = Path(args[args.index("--versions") + 1])
    registry.write_text(json.dumps({version: {"tree": _git(clone, "rev-parse", "HEAD^{tree}")}}))
    args[args.index("--version") + 1] = version
    args[args.index("--cairnwork-clone") + 1] = str(clone)
    args += ["--repo", str(repo), "--repeat", "1"]
    _write_real_inputs(args)
    return args


@pytest.mark.sandbox
def test_real_verify_maps_core_failed_to_refused(tmp_path, capsys, project):
    lg = _origin(tmp_path, project)
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _real_args(tmp_path, lg, cases, project)

    def failed(response: dict[str, object]) -> dict[str, object]:
        response["verification"] = {
            "status": "FAILED",
            "integrity": "VALID",
            "reason_codes": ["ISSUE_1453_TEST_FAILURE"],
        }
        return response

    with core_http_server(tmp_path, tamper=failed) as (url, token):
        config = tmp_path / "state-config.json"
        config.write_text(
            json.dumps(
                {
                    "verifier_account": False,
                    "verifier_python": __import__("sys").executable,
                    "core_url": url,
                    "core_token_file": str(token),
                }
            )
        )
        args += ["--state-config", str(config)]
        assert replay.main(args) == 0

    record = _records(Path(args[args.index("--out") + 1]))[0]
    assert record["mapped"] == ["refused"]
    assert record["exit_codes"] == [2]
    # case a1 is accepted_bad; a Core refusal now means the mistake is corrected
    assert record["outcome"] == "corrected"


@pytest.mark.sandbox
def test_real_verify_unreachable_core_is_no_verdict_not_refused(tmp_path, capsys, project):
    lg = _origin(tmp_path, project)
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _real_args(tmp_path, lg, cases, project)
    config = tmp_path / "state-config.json"
    with core_http_server(tmp_path) as (url, token):
        config.write_text(
            json.dumps(
                {
                    "verifier_account": False,
                    "verifier_python": __import__("sys").executable,
                    "core_url": url,
                    "core_token_file": str(token),
                }
            )
        )
    args += ["--state-config", str(config)]
    assert replay.main(args) == 3
    record = _records(Path(args[args.index("--out") + 1]))[0]
    assert record["outcome"] == "UNREPLAYABLE"
    assert record["reason"] == "NO_CORE_VERDICT"
    assert record["mapped"] == ["UNREPLAYABLE"]


def test_digest_is_cleared_when_a_case_fails_before_the_copy(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}")
    runner = object.__new__(RealRunner)
    runner.root = tmp_path
    runner.repo, runner.version, runner.timeout = tmp_path / "no-such-repo", "v", 30
    runner.state_config = config
    runner.state_config_sha256 = "stale-digest-from-an-earlier-case"
    with pytest.raises(Exception):  # noqa: B017 - the clone of a missing repo fails first
        runner.run({}, {})
    assert runner.state_config_sha256 is None
    assert replay_config.metadata(config, runner)["state_config_sha256"] is None


def test_copied_digest_reaches_the_record_and_differs_per_config(monkeypatch, tmp_path, lg, capsys):
    """A runner that copied the config reports its digest; the record carries exactly that."""

    class CopyingRunner:
        def __init__(self, _clone, _repo, _version, _timeout, state_config):
            self.state_config = state_config
            self.state_config_sha256: str | None = None

        def __call__(self, case, args):
            self.state_config_sha256 = hashlib.sha256(self.state_config.read_bytes()).hexdigest()
            return "accepted"

        def close(self) -> None:
            pass

    monkeypatch.setattr(replay, "RealRunner", CopyingRunner)
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case]) + ["--repo", str(tmp_path), "--state-config", ""]
    digests = []
    for name, body in (("one", {"core_url": "A"}), ("two", {"core_url": "B"})):
        config = tmp_path / f"{name}.json"
        config.write_text(json.dumps(body))
        args[args.index("--state-config") + 1] = str(config)
        args[args.index("--out") + 1] = str(tmp_path / f"{name}.jsonl")
        assert replay.main(args) == 0
        record = _records(tmp_path / f"{name}.jsonl")[0]
        assert record["state_config"] == "given"
        assert record["state_config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
        digests.append(record["state_config_sha256"])
    assert digests[0] != digests[1]
